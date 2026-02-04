#!/usr/bin/env python3
"""
Web-based Real-Time Arbitrage Dashboard
Beautiful UI with live arbitrage opportunities and timestamps
"""

import asyncio
import base64
import json
import os
import time
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Optional, Tuple, List
from threading import Thread, Lock

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from flask import Flask, render_template, jsonify

from kalshi_sdk import API_KEY_ID, PRIVATE_KEY_PEM
from fetch_active_event_simple import get_active_event_for_series

KALSHI_WS_URL = os.getenv("KALSHI_WS_URL", "wss://api.elections.kalshi.com/trade-api/ws/v2")

app = Flask(__name__)

# Global state
arbitrage_opportunities = []
market_stats = {
    'total_positions': 0,
    'priced_positions': 0,
    'arbitrage_count': 0,
    'last_update': None
}
state_lock = Lock()


def sign_message(message: str) -> str:
    """Generate RSA-PSS signature for Kalshi WebSocket auth"""
    private_key = serialization.load_pem_private_key(
        PRIVATE_KEY_PEM.encode(),
        password=None
    )
    signature = private_key.sign(
        message.encode(),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA256()
    )
    return base64.b64encode(signature).decode()


def parse_range(subtitle: str) -> Optional[Tuple[float, float]]:
    """Parse range market subtitle to get bounds"""
    match = re.search(r'\$?([\d,]+(?:\.\d+)?)\s+to\s+\$?([\d,]+(?:\.\d+)?)', subtitle)
    if match:
        lower = float(match.group(1).replace(',', ''))
        upper = float(match.group(2).replace(',', ''))
        if upper != int(upper):
            upper = float(int(upper) + 1)
        return (lower, upper)
    return None


def parse_threshold(subtitle: str) -> Optional[float]:
    """Parse threshold market subtitle to get strike"""
    match = re.search(r'\$?([\d,]+(?:\.\d+)?)\s+or\s+above', subtitle, re.IGNORECASE)
    if match:
        return float(match.group(1).replace(',', ''))
    return None


class ArbitragePosition:
    """Single-range arbitrage position"""
    def __init__(self, range_ticker: str, range_lower: float, range_upper: float):
        self.range_ticker = range_ticker
        self.range_lower = range_lower
        self.range_upper = range_upper
        self.lower_threshold_ticker = None
        self.upper_threshold_ticker = None
        self.lower_no_ask = None
        self.range_yes_ask = None
        self.upper_yes_ask = None
        self.last_update = None

    def update_price(self, ticker: str, yes_ask: Optional[float], no_ask: Optional[float]):
        """Update prices for this position - only update if new price is not None"""
        if ticker == self.lower_threshold_ticker and no_ask is not None:
            self.lower_no_ask = no_ask
            self.last_update = time.time()
        elif ticker == self.range_ticker and yes_ask is not None:
            self.range_yes_ask = yes_ask
            self.last_update = time.time()
        elif ticker == self.upper_threshold_ticker and yes_ask is not None:
            self.upper_yes_ask = yes_ask
            self.last_update = time.time()

    def is_complete(self) -> bool:
        """Check if all prices are available"""
        return (self.lower_no_ask is not None and
                self.range_yes_ask is not None and
                self.upper_yes_ask is not None)

    def get_cost(self) -> Optional[float]:
        """Calculate total cost"""
        if not self.is_complete():
            return None
        return self.lower_no_ask + self.range_yes_ask + self.upper_yes_ask

    def get_profit(self) -> Optional[float]:
        """Calculate profit"""
        cost = self.get_cost()
        if cost is None:
            return None
        return 1.0 - cost

    def is_arbitrage(self) -> bool:
        """Check if this is an arbitrage opportunity"""
        profit = self.get_profit()
        return profit is not None and profit > 0.0001

    def to_dict(self):
        """Convert to dictionary for JSON"""
        return {
            'range_lower': self.range_lower,
            'range_upper': self.range_upper,
            'range_ticker': self.range_ticker,
            'lower_threshold_ticker': self.lower_threshold_ticker,
            'upper_threshold_ticker': self.upper_threshold_ticker,
            'lower_no_ask': self.lower_no_ask,
            'range_yes_ask': self.range_yes_ask,
            'upper_yes_ask': self.upper_yes_ask,
            'cost': self.get_cost(),
            'profit': self.get_profit(),
            'timestamp': datetime.now(ZoneInfo("America/New_York")).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        }


class ArbitrageMonitor:
    """Real-time arbitrage monitor"""

    def __init__(self):
        self.positions: Dict[str, ArbitragePosition] = {}
        self.threshold_map = {}
        self.orderbooks = {}
        self.should_stop = False
        self.reload_requested = False
        self.last_message_ts = time.time()
        self.last_refresh_check = 0.0
        self.active_event_tickers = None

    async def fetch_and_setup_markets(self):
        """Fetch active events and set up arbitrage positions"""
        print("Fetching active markets...")
        self.positions = {}
        self.threshold_map = {}
        self.orderbooks = {}

        kxbtc_event = get_active_event_for_series("KXBTC")
        kxbtcd_event = get_active_event_for_series("KXBTCD")

        if not kxbtc_event or not kxbtcd_event:
            print("Failed to fetch events")
            return False

        self.active_event_tickers = (
            kxbtc_event.get("event_ticker"),
            kxbtcd_event.get("event_ticker"),
        )

        # Build threshold map
        for market in kxbtcd_event['markets']:
            strike = parse_threshold(market['subtitle'])
            if strike is not None:
                self.threshold_map[strike] = market['ticker']

        # Create positions for each range market
        for market in kxbtc_event['markets']:
            if '-B' not in market['ticker']:
                continue

            bounds = parse_range(market['subtitle'])
            if bounds is None:
                continue

            lower, upper = bounds
            lower_threshold = self.threshold_map.get(lower)
            upper_threshold = self.threshold_map.get(upper)

            if lower_threshold and upper_threshold:
                position = ArbitragePosition(market['ticker'], lower, upper)
                position.lower_threshold_ticker = lower_threshold
                position.upper_threshold_ticker = upper_threshold
                self.positions[market['ticker']] = position

        print(f"✓ Created {len(self.positions)} arbitrage positions")

        with state_lock:
            market_stats['total_positions'] = len(self.positions)

        return True

    def _normalize_levels(self, levels):
        cleaned = []
        for lvl in levels or []:
            try:
                price, qty = lvl
            except Exception:
                continue
            if qty is None or qty <= 0:
                continue
            cleaned.append([float(price), qty])
        cleaned.sort(key=lambda item: item[0])
        return cleaned

    def update_best_prices(self, ticker: str):
        """Calculate and update best bid/ask from orderbook"""
        book = self.orderbooks.get(ticker)
        if not book:
            return

        yes_bids = book.get("yes") or []
        no_bids = book.get("no") or []

        best_yes_bid = yes_bids[-1][0] if yes_bids else None
        best_no_bid = no_bids[-1][0] if no_bids else None

        yes_ask = (100 - best_no_bid) / 100 if best_no_bid is not None else None
        no_ask = (100 - best_yes_bid) / 100 if best_yes_bid is not None else None

        for position in self.positions.values():
            position.update_price(ticker, yes_ask, no_ask)

    def update_global_state(self):
        """Update global state for web dashboard"""
        complete_positions = [p for p in self.positions.values() if p.is_complete()]
        arb_positions = [p for p in complete_positions if p.is_arbitrage()]

        # Sort all complete positions by profit (best first, even if negative)
        complete_positions.sort(key=lambda x: x.get_profit() or -999, reverse=True)

        with state_lock:
            global arbitrage_opportunities
            # Return ALL complete positions (not just arbitrage)
            arbitrage_opportunities = [p.to_dict() for p in complete_positions[:50]]  # Show top 50
            market_stats['priced_positions'] = len(complete_positions)
            market_stats['arbitrage_count'] = len(arb_positions)
            market_stats['last_update'] = datetime.now(ZoneInfo("America/New_York")).strftime('%H:%M:%S.%f')[:-3]

    async def connect_websocket(self):
        """Connect to websocket and stream updates"""
        while not self.should_stop:
            all_tickers = set()
            for pos in self.positions.values():
                all_tickers.add(pos.range_ticker)
                all_tickers.add(pos.lower_threshold_ticker)
                all_tickers.add(pos.upper_threshold_ticker)

            all_tickers = list(all_tickers)

            timestamp = str(int(time.time() * 1000))
            message = timestamp + "GET" + "/trade-api/ws/v2"
            signature = sign_message(message)

            headers = {
                "KALSHI-ACCESS-KEY": API_KEY_ID,
                "KALSHI-ACCESS-SIGNATURE": signature,
                "KALSHI-ACCESS-TIMESTAMP": timestamp
            }

            print(f"Connecting to websocket for {len(all_tickers)} markets...")

            try:
                async with websockets.connect(KALSHI_WS_URL, additional_headers=headers) as ws:
                    self.ws = ws
                    self.last_message_ts = time.time()

                    subscribe_msg = {
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["orderbook_delta"],
                            "market_tickers": all_tickers,
                        }
                    }

                    await ws.send(json.dumps(subscribe_msg))
                    print(f"✓ Subscribed to {len(all_tickers)} markets")
                    print(f"✓ Web dashboard running at http://localhost:8080")

                    while not self.should_stop:
                        # Periodic active event check (every 60s)
                        now = time.time()
                        if now - self.last_refresh_check >= 60:
                            self.last_refresh_check = now
                            kxbtc_event = get_active_event_for_series("KXBTC")
                            kxbtcd_event = get_active_event_for_series("KXBTCD")
                            if kxbtc_event and kxbtcd_event:
                                new_pair = (
                                    kxbtc_event.get("event_ticker"),
                                    kxbtcd_event.get("event_ticker"),
                                )
                                if new_pair != self.active_event_tickers:
                                    print("Active event changed, reloading markets...")
                                    self.reload_requested = True

                        if self.reload_requested:
                            break

                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        except asyncio.TimeoutError:
                            # No messages; reconnect if stale
                            if time.time() - self.last_message_ts > 30:
                                print("No websocket updates for 30s, reconnecting...")
                                break
                            continue

                        self.last_message_ts = time.time()
                        data = json.loads(message)
                        msg_type = data.get('type')

                        if msg_type == 'subscribed':
                            continue

                        if msg_type == 'orderbook_snapshot':
                            msg = data.get('msg', {})
                            ticker = msg.get('market_ticker')
                            if not ticker:
                                continue
                            self.orderbooks[ticker] = {
                                "yes": self._normalize_levels(msg.get("yes")),
                                "no": self._normalize_levels(msg.get("no")),
                            }
                            self.update_best_prices(ticker)

                        elif msg_type == 'orderbook_delta':
                            msg = data.get('msg', {})
                            ticker = msg.get('market_ticker')
                            side = msg.get('side')
                            price = msg.get('price')
                            qty = msg.get('quantity')

                            if ticker is None or side not in ("yes", "no") or price is None:
                                continue

                            book = self.orderbooks.setdefault(ticker, {"yes": [], "no": []})
                            levels = book[side]

                            if qty is None or qty == 0:
                                levels[:] = [lvl for lvl in levels if lvl[0] != price]
                            else:
                                updated = False
                                for idx, lvl in enumerate(levels):
                                    if lvl[0] == price:
                                        levels[idx] = [float(price), qty]
                                        updated = True
                                        break
                                if not updated:
                                    levels.append([float(price), qty])

                            levels.sort(key=lambda item: item[0])
                            self.update_best_prices(ticker)

                        self.update_global_state()

            except Exception as e:
                print(f"Websocket error: {e}")

            if self.reload_requested:
                self.reload_requested = False
                await self.fetch_and_setup_markets()

    async def run(self):
        """Main run loop"""
        success = await self.fetch_and_setup_markets()
        if not success:
            return

        try:
            await self.connect_websocket()
        finally:
            if self.ws:
                await self.ws.close()

    def stop(self):
        """Stop the monitor"""
        self.should_stop = True


# Flask routes
@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')


@app.route('/api/opportunities')
def get_opportunities():
    """API endpoint for arbitrage opportunities"""
    with state_lock:
        return jsonify({
            'opportunities': arbitrage_opportunities,
            'stats': market_stats
        })


# Background thread to run asyncio event loop
def run_monitor():
    """Run the arbitrage monitor in background"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    monitor = ArbitrageMonitor()
    loop.run_until_complete(monitor.run())


if __name__ == "__main__":
    # Start monitor in background thread
    monitor_thread = Thread(target=run_monitor, daemon=True)
    monitor_thread.start()

    # Give it a moment to initialize
    time.sleep(2)

    # Start Flask server
    print("\n" + "=" * 80)
    print("KALSHI ARBITRAGE WEB DASHBOARD")
    print("=" * 80)
    print("\nStarting web server...")
    print("Open your browser to: http://localhost:8080")
    print("\nPress Ctrl+C to stop")
    print("=" * 80 + "\n")

    app.run(host='0.0.0.0', port=8080, debug=False)

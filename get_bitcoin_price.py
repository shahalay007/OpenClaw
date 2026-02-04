#!/usr/bin/env python3
"""
Get Current Bitcoin Price from Kalshi Markets
Analyzes threshold and range markets to determine implied BTC price
"""

import asyncio
import base64
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from kalshi_sdk import API_KEY_ID, PRIVATE_KEY_PEM
from fetch_active_event_simple import get_active_event_for_series

KALSHI_WS_URL = os.getenv("KALSHI_WS_URL", "wss://api.elections.kalshi.com/trade-api/ws/v2")


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


def parse_threshold(subtitle: str):
    """Parse threshold from subtitle like '$89,500 or above'"""
    import re
    match = re.search(r'\$?([\d,]+(?:\.\d+)?)\s+or\s+above', subtitle, re.IGNORECASE)
    if match:
        return float(match.group(1).replace(',', ''))
    return None


def parse_range(subtitle: str):
    """Parse range from subtitle like '$89,250 to 89,499.99'"""
    import re
    match = re.search(r'\$?([\d,]+(?:\.\d+)?)\s+to\s+\$?([\d,]+(?:\.\d+)?)', subtitle)
    if match:
        lower = float(match.group(1).replace(',', ''))
        upper = float(match.group(2).replace(',', ''))
        return (lower, upper)
    return None


def normalize_levels(levels):
    """Normalize orderbook levels"""
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


def calculate_best_prices(book):
    """Calculate best bid/ask from orderbook"""
    yes_bids = book.get("yes") or []
    no_bids = book.get("no") or []

    best_yes_bid = yes_bids[-1][0] if yes_bids else None
    best_no_bid = no_bids[-1][0] if no_bids else None

    # YES bid is the probability the market assigns
    yes_prob = best_yes_bid / 100 if best_yes_bid is not None else None
    no_prob = best_no_bid / 100 if best_no_bid is not None else None

    return {
        'yes_bid': best_yes_bid,
        'no_bid': best_no_bid,
        'yes_prob': yes_prob,
        'no_prob': no_prob
    }


async def get_live_bitcoin_price():
    """Get implied Bitcoin price from Kalshi markets"""

    print(f"\n{'='*80}")
    print("BITCOIN PRICE FROM KALSHI MARKETS")
    print(f"{'='*80}\n")

    # Fetch active events
    print("Fetching active markets...")
    kxbtc_event = get_active_event_for_series("KXBTC")
    kxbtcd_event = get_active_event_for_series("KXBTCD")

    if not kxbtc_event or not kxbtcd_event:
        print("❌ Failed to fetch active events")
        return

    print(f"\n✓ Active KXBTC Event: {kxbtc_event['event_ticker']}")
    print(f"✓ Active KXBTCD Event: {kxbtcd_event['event_ticker']}")

    # Prepare threshold markets for analysis
    threshold_markets = {}
    range_markets = {}

    for market in kxbtcd_event['markets']:
        threshold = parse_threshold(market['subtitle'])
        if threshold:
            threshold_markets[market['ticker']] = {
                'threshold': threshold,
                'subtitle': market['subtitle']
            }

    for market in kxbtc_event['markets']:
        range_bounds = parse_range(market['subtitle'])
        if range_bounds:
            range_markets[market['ticker']] = {
                'lower': range_bounds[0],
                'upper': range_bounds[1],
                'subtitle': market['subtitle']
            }

    print(f"\n✓ Found {len(threshold_markets)} threshold markets")
    print(f"✓ Found {len(range_markets)} range markets")

    # Get all tickers
    all_tickers = list(threshold_markets.keys()) + list(range_markets.keys())

    # Connect to WebSocket
    print(f"\nConnecting to Kalshi WebSocket...")

    timestamp = str(int(time.time() * 1000))
    message = timestamp + "GET" + "/trade-api/ws/v2"
    signature = sign_message(message)

    headers = {
        "KALSHI-ACCESS-KEY": API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp
    }

    orderbooks = {}

    async with websockets.connect(KALSHI_WS_URL, additional_headers=headers) as ws:
        subscribe_msg = {
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta"],
                "market_tickers": all_tickers[:100],  # Limit to first 100 markets
            }
        }

        await ws.send(json.dumps(subscribe_msg))
        print(f"✓ Subscribed to {len(all_tickers[:100])} markets\n")
        print("Collecting orderbook data...")

        snapshot_count = 0
        target_snapshots = min(len(all_tickers), 100)

        while snapshot_count < target_snapshots:
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=10.0)
            except asyncio.TimeoutError:
                break

            data = json.loads(message)
            msg_type = data.get('type')

            if msg_type == 'orderbook_snapshot':
                msg = data.get('msg', {})
                ticker = msg.get('market_ticker')
                if ticker:
                    orderbooks[ticker] = {
                        "yes": normalize_levels(msg.get("yes")),
                        "no": normalize_levels(msg.get("no")),
                    }
                    snapshot_count += 1
                    if snapshot_count % 10 == 0:
                        print(f"  Received {snapshot_count}/{target_snapshots} orderbooks...")

            elif msg_type == 'orderbook_delta':
                msg = data.get('msg', {})
                ticker = msg.get('market_ticker')
                side = msg.get('side')
                price = msg.get('price')
                delta = msg.get('delta')

                if ticker and side in ("yes", "no") and price is not None and delta is not None:
                    book = orderbooks.setdefault(ticker, {"yes": [], "no": []})
                    levels = book[side]

                    # Find current quantity
                    current_qty = 0
                    for lvl in levels:
                        if lvl[0] == price:
                            current_qty = lvl[1]
                            break

                    # Apply delta
                    new_qty = current_qty + delta

                    if new_qty <= 0:
                        levels[:] = [lvl for lvl in levels if lvl[0] != price]
                    else:
                        updated = False
                        for idx, lvl in enumerate(levels):
                            if lvl[0] == price:
                                levels[idx] = [float(price), new_qty]
                                updated = True
                                break
                        if not updated:
                            levels.append([float(price), new_qty])
                    levels.sort(key=lambda item: item[0])

    print(f"\n✓ Collected {len(orderbooks)} orderbooks\n")

    # Analyze threshold markets to find implied price
    print(f"{'='*80}")
    print("THRESHOLD MARKET ANALYSIS")
    print(f"{'='*80}\n")

    threshold_data = []
    for ticker, info in threshold_markets.items():
        if ticker in orderbooks:
            prices = calculate_best_prices(orderbooks[ticker])
            if prices['yes_prob'] is not None:
                threshold_data.append({
                    'threshold': info['threshold'],
                    'yes_prob': prices['yes_prob'],
                    'ticker': ticker,
                    'subtitle': info['subtitle']
                })

    # Sort by threshold
    threshold_data.sort(key=lambda x: x['threshold'])

    # Find where probability crosses 50%
    implied_price = None
    for i in range(len(threshold_data) - 1):
        current = threshold_data[i]
        next_item = threshold_data[i + 1]

        if current['yes_prob'] >= 0.5 and next_item['yes_prob'] < 0.5:
            # Price is between these two thresholds
            implied_price = (current['threshold'] + next_item['threshold']) / 2
            break

    # Show top and bottom thresholds
    print("Top Thresholds (Market thinks BTC will be ABOVE these):")
    for item in threshold_data[-10:]:
        prob_pct = item['yes_prob'] * 100
        print(f"  ${item['threshold']:>10,.2f} or above: {prob_pct:>5.1f}% probability")

    print("\nBottom Thresholds (Market thinks BTC will be BELOW these):")
    for item in threshold_data[:10]:
        prob_pct = item['yes_prob'] * 100
        print(f"  ${item['threshold']:>10,.2f} or above: {prob_pct:>5.1f}% probability")

    # Analyze range markets to find most likely range
    print(f"\n{'='*80}")
    print("RANGE MARKET ANALYSIS")
    print(f"{'='*80}\n")

    range_data = []
    for ticker, info in range_markets.items():
        if ticker in orderbooks:
            prices = calculate_best_prices(orderbooks[ticker])
            if prices['yes_prob'] is not None:
                range_data.append({
                    'lower': info['lower'],
                    'upper': info['upper'],
                    'yes_prob': prices['yes_prob'],
                    'ticker': ticker,
                    'subtitle': info['subtitle']
                })

    # Sort by probability (highest first)
    range_data.sort(key=lambda x: x['yes_prob'], reverse=True)

    print("Most Likely Price Ranges (Highest Probability):")
    for item in range_data[:10]:
        prob_pct = item['yes_prob'] * 100
        mid_price = (item['lower'] + item['upper']) / 2
        print(f"  ${item['lower']:>10,.2f} - ${item['upper']:>10,.2f} (mid: ${mid_price:>10,.2f}): {prob_pct:>5.1f}%")

    # Calculate weighted average price from ranges
    total_weight = sum(item['yes_prob'] for item in range_data)
    if total_weight > 0:
        weighted_avg = sum((item['lower'] + item['upper']) / 2 * item['yes_prob'] for item in range_data) / total_weight
    else:
        weighted_avg = None

    # Final summary
    print(f"\n{'='*80}")
    print("BITCOIN PRICE ESTIMATE")
    print(f"{'='*80}\n")

    if range_data:
        top_range = range_data[0]
        mid_price = (top_range['lower'] + top_range['upper']) / 2
        print(f"Most Likely Range: ${top_range['lower']:,.2f} - ${top_range['upper']:,.2f}")
        print(f"Range Midpoint: ${mid_price:,.2f}")
        print(f"Probability: {top_range['yes_prob']*100:.1f}%")

    if weighted_avg:
        print(f"\nWeighted Average Price: ${weighted_avg:,.2f}")

    if implied_price:
        print(f"50% Threshold Price: ${implied_price:,.2f}")

    print(f"\nTimestamp: {datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"{'='*80}\n")


def main():
    asyncio.run(get_live_bitcoin_price())


if __name__ == "__main__":
    main()

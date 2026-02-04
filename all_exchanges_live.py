#!/usr/bin/env python3
"""
All Exchanges BRTI Proxy - Combined Live Updates
Updates all three exchange prices in a single file
"""

import asyncio
import json
import ssl
from datetime import datetime
from collections import deque

import websockets

try:
    import certifi
    CERT_PATH = certifi.where()
except ImportError:
    CERT_PATH = None


OUTPUT_FILE = "all_exchanges_live.txt"

# Exchange data storage
exchange_data = {
    'coinbase': {'price': None, 'bid': None, 'ask': None, 'spread': None, 'timestamp': None, 'last_update': 0},
    'kraken': {'price': None, 'bid': None, 'ask': None, 'spread': None, 'timestamp': None, 'last_update': 0},
    'bitstamp': {'price': None, 'bid': None, 'ask': None, 'spread': None, 'timestamp': None, 'last_update': 0},
    'gemini': {'price': None, 'bid': None, 'ask': None, 'spread': None, 'timestamp': None, 'last_update': 0}
}

# BRTI snapshot (1 Hz sampling)
last_snapshot_brti = None
last_snapshot_timestamp = None
last_processed_second = -1
snapshot_history = deque(maxlen=60)


def get_ssl_context():
    """Create SSL context for secure WebSocket connection"""
    ssl_context = ssl.create_default_context()
    if CERT_PATH:
        ssl_context.load_verify_locations(CERT_PATH)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    return ssl_context


def write_to_file():
    """Write all exchange data to file including weighted BRTI"""
    import time
    lines = []

    for exchange in ['coinbase', 'kraken', 'bitstamp', 'gemini']:
        data = exchange_data[exchange]
        if data['price'] is not None:
            line = f"[{data['timestamp']}] {exchange.upper()}: ${data['price']:,.2f} | Bid: ${data['bid']:,.2f} | Ask: ${data['ask']:,.2f} | Spread: ${data['spread']:.2f}"
        else:
            line = f"{exchange.upper()}: Connecting..."
        lines.append(line)

    # Calculate weighted BRTI
    now = time.time()
    active_weights = 0
    weighted_sum = 0

    # Weights: Coinbase 40%, Kraken 35%, Bitstamp 15%, Gemini 10%
    weights = {'coinbase': 0.40, 'kraken': 0.35, 'bitstamp': 0.15, 'gemini': 0.10}

    staleness_limit = 30  # 30 seconds for all exchanges

    for exchange, weight in weights.items():
        data = exchange_data[exchange]
        if data['bid'] is not None and data['ask'] is not None:
            # Check staleness (omit if older than 30 seconds)
            if now - data['last_update'] < staleness_limit:
                mid_price = (data['bid'] + data['ask']) / 2
                weighted_sum += mid_price * weight
                active_weights += weight

    # Add weighted BRTI line
    lines.append("")
    if active_weights > 0:
        final_brti = weighted_sum / active_weights
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        lines.append(f"[{timestamp}] WEIGHTED BRTI (CB:40% KR:35% BS:15% GM:10%): ${final_brti:,.2f}")

        # Display last snapshot (updated by brti_recorder at 1 Hz)
        if last_snapshot_brti is not None and last_snapshot_timestamp is not None:
            lines.append(f"[{last_snapshot_timestamp}] 1-SECOND SNAPSHOT BRTI: ${last_snapshot_brti:,.2f}")
            # Rolling averages (10/20/30/60s)
            avg_map = {}
            for window in (10, 20, 30, 60):
                if len(snapshot_history) < window:
                    lines.append(f"{window}-SECOND SNAPSHOT AVG: Collecting... ({len(snapshot_history)}/{window})")
                else:
                    avg = sum(list(snapshot_history)[-window:]) / window
                    avg_map[window] = avg
                    lines.append(f"{window}-SECOND SNAPSHOT AVG: ${avg:,.2f}")

            # Diff between 60s and 10s when both are available
            if 10 in avg_map and 60 in avg_map:
                diff = avg_map[10] - avg_map[60]
                lines.append(f"10s-60s AVG DIFF: ${diff:,.2f}")
        else:
            lines.append("1-SECOND SNAPSHOT BRTI: Waiting for snapshot...")
    else:
        lines.append("WEIGHTED BRTI: Waiting for data...")

    content = '\n'.join(lines) + '\n'

    with open(OUTPUT_FILE, 'w') as f:
        f.write(content)


async def coinbase_feed():
    """Subscribe to Coinbase ticker"""
    import time
    url = "wss://ws-feed.exchange.coinbase.com"

    async with websockets.connect(url, ssl=get_ssl_context()) as ws:
        subscribe_msg = {
            "type": "subscribe",
            "product_ids": ["BTC-USD"],
            "channels": ["ticker"]
        }
        await ws.send(json.dumps(subscribe_msg))

        async for message in ws:
            data = json.loads(message)

            if data.get('type') == 'ticker' and 'best_bid' in data and 'best_ask' in data:
                best_bid = float(data['best_bid'])
                best_ask = float(data['best_ask'])
                mid_price = (best_bid + best_ask) / 2
                spread = best_ask - best_bid

                exchange_data['coinbase'] = {
                    'price': mid_price,
                    'bid': best_bid,
                    'ask': best_ask,
                    'spread': spread,
                    'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    'last_update': time.time()
                }
                write_to_file()


async def kraken_feed():
    """Subscribe to Kraken ticker (v1 API)"""
    import time
    url = "wss://ws.kraken.com"

    # Track last price to avoid updating on duplicate messages
    last_bid = None
    last_ask = None

    async with websockets.connect(url, ssl=get_ssl_context()) as ws:
        subscribe_msg = {
            "event": "subscribe",
            "pair": ["XBT/USD"],
            "subscription": {"name": "ticker"}
        }
        await ws.send(json.dumps(subscribe_msg))

        async for message in ws:
            data = json.loads(message)

            # Kraken v1 sends ticker data as lists
            if isinstance(data, list):
                ticker_data = data[1]

                # Check if bid and ask are present
                if 'b' in ticker_data and 'a' in ticker_data:
                    bid_price = float(ticker_data['b'][0])
                    ask_price = float(ticker_data['a'][0])

                    # Only update if price has actually changed
                    if bid_price != last_bid or ask_price != last_ask:
                        last_bid = bid_price
                        last_ask = ask_price

                        mid_price = (bid_price + ask_price) / 2
                        spread = ask_price - bid_price

                        # Use local time for consistency with other exchanges
                        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

                        exchange_data['kraken'] = {
                            'price': mid_price,
                            'bid': bid_price,
                            'ask': ask_price,
                            'spread': spread,
                            'timestamp': timestamp_str,
                            'last_update': time.time()
                        }
                        write_to_file()


async def gemini_feed():
    """Subscribe to Gemini market data"""
    import time
    url = "wss://api.gemini.com/v1/marketdata/BTCUSD?top_of_book=true"

    latest_bid = None
    latest_ask = None

    async with websockets.connect(url, ssl=get_ssl_context()) as ws:
        async for message in ws:
            data = json.loads(message)

            if data.get('type') == 'update':
                events = data.get('events', [])

                for event in events:
                    if event.get('type') == 'change':
                        side = event.get('side')
                        price = event.get('price')
                        reason = event.get('reason')

                        if reason in ('top-of-book', 'initial'):
                            if side == 'bid':
                                latest_bid = float(price)
                            elif side == 'ask':
                                latest_ask = float(price)

                if latest_bid is not None and latest_ask is not None:
                    mid_price = (latest_bid + latest_ask) / 2
                    spread = latest_ask - latest_bid

                    exchange_data['gemini'] = {
                        'price': mid_price,
                        'bid': latest_bid,
                        'ask': latest_ask,
                        'spread': spread,
                        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        'last_update': time.time()
                    }
                    write_to_file()


async def bitstamp_feed():
    """Subscribe to Bitstamp order book"""
    import time
    url = "wss://ws.bitstamp.net"

    async with websockets.connect(url, ssl=get_ssl_context()) as ws:
        subscribe_msg = {
            "event": "bts:subscribe",
            "data": {
                "channel": "order_book_btcusd"
            }
        }
        await ws.send(json.dumps(subscribe_msg))

        async for message in ws:
            data = json.loads(message)

            if data.get('event') == 'data' and data.get('channel') == 'order_book_btcusd':
                order_data = data.get('data', {})
                bids = order_data.get('bids', [])
                asks = order_data.get('asks', [])

                if bids and asks:
                    bid_price = float(bids[0][0])
                    ask_price = float(asks[0][0])
                    mid_price = (bid_price + ask_price) / 2
                    spread = ask_price - bid_price

                    exchange_data['bitstamp'] = {
                        'price': mid_price,
                        'bid': bid_price,
                        'ask': ask_price,
                        'spread': spread,
                        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        'last_update': time.time()
                    }
                    write_to_file()


async def brti_recorder():
    """Record BRTI snapshot at 1 Hz (once per second)"""
    import time
    global last_snapshot_brti, last_snapshot_timestamp, last_processed_second

    print("Starting BRTI Snapshot Recorder (1 Hz)...")

    while True:
        # Get current time
        now_dt = datetime.now()
        current_second = now_dt.second

        # Check if we have entered a NEW second
        if current_second != last_processed_second:
            # THE SNAPSHOT - happens exactly once per second

            # Read current state of order books
            cb_data = exchange_data['coinbase']
            kr_data = exchange_data['kraken']
            gm_data = exchange_data['gemini']

            # Calculate weighted average (only if data is fresh < 30s)
            now_time = time.time()
            weighted_sum = 0
            active_weights = 0

            weights = {'coinbase': 0.40, 'kraken': 0.35, 'bitstamp': 0.15, 'gemini': 0.10}

            staleness_limit = 30  # 30 seconds for all exchanges

            for exchange, weight in weights.items():
                data = exchange_data[exchange]
                if data['bid'] is not None and data['ask'] is not None:
                    # Check staleness (omit if older than 30 seconds)
                    if now_time - data['last_update'] < staleness_limit:
                        mid_price = (data['bid'] + data['ask']) / 2
                        weighted_sum += mid_price * weight
                        active_weights += weight

            if active_weights > 0:
                final_brti = weighted_sum / active_weights
                last_snapshot_brti = final_brti
                last_snapshot_timestamp = now_dt.strftime("%Y-%m-%d %H:%M:%S.000")
                snapshot_history.append(final_brti)

            # Mark this second as processed
            last_processed_second = current_second

        # Sleep 10ms to catch second change promptly
        await asyncio.sleep(0.01)


async def main():
    """Run all three feeds concurrently"""
    print(f"Starting all exchanges feed...")
    print(f"Output file: {OUTPUT_FILE}")
    print("Press Ctrl+C to stop\n")

    # Run all feeds + snapshot recorder in parallel
    await asyncio.gather(
        coinbase_feed(),
        kraken_feed(),
        gemini_feed(),
        bitstamp_feed(),
        brti_recorder()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")

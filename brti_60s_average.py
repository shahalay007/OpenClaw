#!/usr/bin/env python3
"""
BRTI Proxy - 60 Second Moving Average
Tracks prices and calculates rolling 60-second average
"""

import asyncio
import json
import ssl
from datetime import datetime, timedelta
from collections import deque

import websockets

try:
    import certifi
    CERT_PATH = certifi.where()
except ImportError:
    CERT_PATH = None


COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"
OUTPUT_FILE = "brti_60s_average.txt"


def get_ssl_context():
    """Create SSL context for secure WebSocket connection"""
    ssl_context = ssl.create_default_context()
    if CERT_PATH:
        ssl_context.load_verify_locations(CERT_PATH)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    return ssl_context


async def brti_60s_average():
    """
    Stream BRTI and calculate 60-second moving average
    """

    print(f"Starting BRTI 60-second moving average calculator...")
    print(f"Output file: {OUTPUT_FILE}")
    print("Will start showing average after 60 seconds of data collection...")
    print("Press Ctrl+C to stop\n")

    # Store (timestamp, price) tuples for last 60 seconds
    price_history = deque()

    async with websockets.connect(COINBASE_WS_URL, ssl=get_ssl_context()) as ws:
        # Subscribe to ticker channel
        subscribe_msg = {
            "type": "subscribe",
            "product_ids": ["BTC-USD"],
            "channels": ["ticker"]
        }

        await ws.send(json.dumps(subscribe_msg))

        update_count = 0
        start_time = None

        try:
            async for message in ws:
                data = json.loads(message)
                msg_type = data.get('type')

                # Only process ticker updates with bid/ask data
                if msg_type == 'ticker' and 'best_bid' in data and 'best_ask' in data:
                    update_count += 1

                    best_bid = float(data['best_bid'])
                    best_ask = float(data['best_ask'])
                    mid_price = (best_bid + best_ask) / 2
                    spread = best_ask - best_bid

                    now = datetime.now()
                    if start_time is None:
                        start_time = now

                    # Add current price to history
                    price_history.append((now, mid_price))

                    # Remove prices older than 60 seconds
                    cutoff_time = now - timedelta(seconds=60)
                    while price_history and price_history[0][0] < cutoff_time:
                        price_history.popleft()

                    # Calculate average if we have data
                    if len(price_history) > 0:
                        avg_price = sum(p[1] for p in price_history) / len(price_history)
                    else:
                        avg_price = mid_price

                    timestamp = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    elapsed = (now - start_time).total_seconds()

                    # Build output line
                    if elapsed < 60:
                        # Still collecting initial data
                        line = (f"[{timestamp}] CURRENT: ${mid_price:,.2f} | "
                                f"Collecting data... ({elapsed:.0f}s / 60s) | "
                                f"Samples: {len(price_history)}\n")
                    else:
                        # We have 60 seconds of data, show average
                        line = (f"[{timestamp}] CURRENT: ${mid_price:,.2f} | "
                                f"60s AVG: ${avg_price:,.2f} | "
                                f"Bid: ${best_bid:,.2f} | Ask: ${best_ask:,.2f} | "
                                f"Spread: ${spread:.2f} | Samples: {len(price_history)}\n")

                    # Write to file
                    with open(OUTPUT_FILE, 'w') as f:
                        f.write(line)

                    # Print to console
                    print(f"\r{line.strip()}", end='', flush=True)

        except KeyboardInterrupt:
            print(f"\n\nStopped after {update_count} updates")


def main():
    try:
        asyncio.run(brti_60s_average())
    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Kraken BRTI Proxy - Single Line Update
Continuously updates a single line in a file with the latest Kraken BTC/USD price
"""

import asyncio
import json
import ssl
from datetime import datetime

import websockets

try:
    import certifi
    CERT_PATH = certifi.where()
except ImportError:
    CERT_PATH = None


KRAKEN_WS_URL = "wss://ws.kraken.com/v2"
OUTPUT_FILE = "kraken_live_price.txt"


def get_ssl_context():
    """Create SSL context for secure WebSocket connection"""
    ssl_context = ssl.create_default_context()
    if CERT_PATH:
        ssl_context.load_verify_locations(CERT_PATH)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    return ssl_context


async def kraken_single_line_updater():
    """
    Stream Kraken ticker and update a single line in file
    """

    print(f"Starting Kraken single-line updater...")
    print(f"Output file: {OUTPUT_FILE}")
    print("Press Ctrl+C to stop\n")

    async with websockets.connect(KRAKEN_WS_URL, ssl=get_ssl_context()) as ws:
        # Subscribe to ticker channel for BTC/USD
        subscribe_msg = {
            "method": "subscribe",
            "params": {
                "channel": "ticker",
                "symbol": ["BTC/USD"],
                "event_trigger": "bbo"  # Update on best bid/offer changes
            }
        }

        await ws.send(json.dumps(subscribe_msg))

        update_count = 0

        try:
            async for message in ws:
                data = json.loads(message)

                # Handle subscription status
                if data.get('method') == 'subscribe':
                    if data.get('success'):
                        print("✓ Subscribed to Kraken BTC/USD ticker\n")
                    continue

                # Process ticker updates (ignore heartbeats)
                if data.get('channel') == 'ticker' and data.get('type') in ('snapshot', 'update'):
                    ticker_data = data.get('data', [])

                    if not ticker_data or len(ticker_data) == 0:
                        continue

                    # Get first ticker in data array
                    tick = ticker_data[0]

                    # Extract bid/ask data
                    bid = tick.get('bid')
                    ask = tick.get('ask')

                    if bid is None or ask is None:
                        continue

                    update_count += 1

                    bid_price = float(bid)
                    ask_price = float(ask)
                    mid_price = (bid_price + ask_price) / 2
                    spread = ask_price - bid_price

                    # Get last trade price if available
                    last_price = tick.get('last')

                    # Use Kraken's timestamp if available, otherwise use local time
                    kraken_timestamp = tick.get('timestamp')
                    if kraken_timestamp:
                        timestamp_str = kraken_timestamp.replace('T', ' ').replace('Z', '')[:-3]
                    else:
                        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

                    # Write single line to file (overwrite each time)
                    line = f"[{timestamp_str}] KRAKEN BRTI: ${mid_price:,.2f} | Bid: ${bid_price:,.2f} | Ask: ${ask_price:,.2f} | Spread: ${spread:.2f}\n"

                    with open(OUTPUT_FILE, 'w') as f:
                        f.write(line)

                    # Print to console for monitoring
                    print(f"\r{line.strip()}", end='', flush=True)

        except KeyboardInterrupt:
            print(f"\n\nStopped after {update_count} updates")


def main():
    try:
        asyncio.run(kraken_single_line_updater())
    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    main()

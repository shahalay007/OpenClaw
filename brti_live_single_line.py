#!/usr/bin/env python3
"""
BRTI Proxy - Single Line Update
Continuously updates a single line in a file with the latest BRTI price
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


COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"
OUTPUT_FILE = "brti_live_price.txt"


def get_ssl_context():
    """Create SSL context for secure WebSocket connection"""
    ssl_context = ssl.create_default_context()
    if CERT_PATH:
        ssl_context.load_verify_locations(CERT_PATH)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    return ssl_context


async def brti_single_line_updater():
    """
    Stream BRTI and update a single line in file
    """

    print(f"Starting BRTI single-line updater...")
    print(f"Output file: {OUTPUT_FILE}")
    print("Press Ctrl+C to stop\n")

    async with websockets.connect(COINBASE_WS_URL, ssl=get_ssl_context()) as ws:
        # Subscribe to ticker channel
        subscribe_msg = {
            "type": "subscribe",
            "product_ids": ["BTC-USD"],
            "channels": ["ticker"]
        }

        await ws.send(json.dumps(subscribe_msg))

        update_count = 0

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

                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

                    # Write single line to file (overwrite each time)
                    line = f"[{timestamp}] BRTI: ${mid_price:,.2f} | Bid: ${best_bid:,.2f} | Ask: ${best_ask:,.2f} | Spread: ${spread:.2f}\n"

                    with open(OUTPUT_FILE, 'w') as f:
                        f.write(line)

                    # Print to console for monitoring
                    print(f"\r{line.strip()}", end='', flush=True)

        except KeyboardInterrupt:
            print(f"\n\nStopped after {update_count} updates")


def main():
    try:
        asyncio.run(brti_single_line_updater())
    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    main()

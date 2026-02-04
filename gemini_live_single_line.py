#!/usr/bin/env python3
"""
Gemini BRTI Proxy - Single Line Update
Continuously updates a single line in a file with the latest Gemini BTC/USD price
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


# Gemini WebSocket URL with top_of_book parameter for best bid/ask
GEMINI_WS_URL = "wss://api.gemini.com/v1/marketdata/BTCUSD?top_of_book=true"
OUTPUT_FILE = "gemini_live_price.txt"


def get_ssl_context():
    """Create SSL context for secure WebSocket connection"""
    ssl_context = ssl.create_default_context()
    if CERT_PATH:
        ssl_context.load_verify_locations(CERT_PATH)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    return ssl_context


async def gemini_single_line_updater():
    """
    Stream Gemini market data and update a single line in file
    """

    print(f"Starting Gemini single-line updater...")
    print(f"Output file: {OUTPUT_FILE}")
    print("Press Ctrl+C to stop\n")

    # Track latest bid and ask (Gemini sends them separately)
    latest_bid = None
    latest_ask = None

    async with websockets.connect(GEMINI_WS_URL, ssl=get_ssl_context()) as ws:
        update_count = 0

        try:
            async for message in ws:
                data = json.loads(message)

                # Gemini sends different event types
                event_type = data.get('type')

                # Process changes that include bid/ask updates
                if event_type == 'update':
                    events = data.get('events', [])

                    # Look for 'change' events which contain bid/ask updates
                    for event in events:
                        if event.get('type') == 'change':
                            side = event.get('side')
                            price = event.get('price')
                            reason = event.get('reason')

                            # Track top of book updates or initial values
                            if reason in ('top-of-book', 'initial'):
                                if side == 'bid':
                                    latest_bid = float(price)
                                elif side == 'ask':
                                    latest_ask = float(price)

                    # If we have both bid and ask, update the file
                    if latest_bid is not None and latest_ask is not None:
                        update_count += 1

                        mid_price = (latest_bid + latest_ask) / 2
                        spread = latest_ask - latest_bid

                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

                        # Write single line to file (overwrite each time)
                        line = f"[{timestamp}] GEMINI BRTI: ${mid_price:,.2f} | Bid: ${latest_bid:,.2f} | Ask: ${latest_ask:,.2f} | Spread: ${spread:.2f}\n"

                        with open(OUTPUT_FILE, 'w') as f:
                            f.write(line)

                        # Print to console for monitoring
                        print(f"\r{line.strip()}", end='', flush=True)

        except KeyboardInterrupt:
            print(f"\n\nStopped after {update_count} updates")


def main():
    try:
        asyncio.run(gemini_single_line_updater())
    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    main()

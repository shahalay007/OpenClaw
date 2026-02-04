#!/usr/bin/env python3
"""
BRTI Proxy - Bitcoin Reference Rate Index from Coinbase
Uses ticker channel (order book quotes) to calculate mid-price
Mid-Price = (Best Bid + Best Ask) / 2

This is the closest proxy to the official BRTI calculation.
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


def get_ssl_context():
    """Create SSL context for secure WebSocket connection"""
    ssl_context = ssl.create_default_context()
    if CERT_PATH:
        ssl_context.load_verify_locations(CERT_PATH)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    return ssl_context


async def brti_proxy_stream():
    """
    Stream BRTI Proxy (Mid-Price) from Coinbase

    BRTI uses order book quotes (best bid/ask), not trades.
    Mid-Price = (Best Bid + Best Ask) / 2

    This is more accurate for the Bitcoin Reference Rate Index than individual trades.
    """

    print(f"\n{'='*80}")
    print("BRTI PROXY - BITCOIN REFERENCE RATE INDEX (MID-PRICE)")
    print(f"{'='*80}\n")
    print(f"Connecting to Coinbase WebSocket at {COINBASE_WS_URL}...")

    async with websockets.connect(COINBASE_WS_URL, ssl=get_ssl_context()) as ws:
        # Subscribe to TICKER channel for best bid/ask quotes
        # This is what BRTI actually uses (not individual trades)
        subscribe_msg = {
            "type": "subscribe",
            "product_ids": ["BTC-USD"],
            "channels": ["ticker"]
        }

        await ws.send(json.dumps(subscribe_msg))
        print("✓ Subscribed to BTC-USD ticker channel (Order Book Quotes)\n")
        print(f"{'='*80}")
        print("STREAMING BRTI PROXY (Press Ctrl+C to stop)")
        print(f"{'='*80}\n")

        update_count = 0

        # Listen for messages
        try:
            async for message in ws:
                data = json.loads(message)
                msg_type = data.get('type')

                # Subscription confirmation
                if msg_type == 'subscriptions':
                    channels = data.get('channels', [])
                    print(f"✓ Subscription confirmed: {channels}\n")
                    continue

                # Ticker updates with bid/ask data
                if msg_type == 'ticker':
                    # We only care about updates that have bid/ask data
                    if 'best_bid' in data and 'best_ask' in data:
                        update_count += 1

                        best_bid = float(data['best_bid'])
                        best_ask = float(data['best_ask'])

                        # THE FORMULA: Mid-Price (BRTI Proxy)
                        mid_price = (best_bid + best_ask) / 2

                        # Calculate spread (useful for risk assessment)
                        spread = best_ask - best_bid
                        spread_bps = (spread / mid_price) * 10000  # Spread in basis points

                        # Get last trade price for comparison
                        last_price = float(data.get('price', 0))

                        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                        print(f"[{timestamp}] Bid: ${best_bid:,.2f} | Ask: ${best_ask:,.2f} | "
                              f">> BRTI PROXY: ${mid_price:,.2f} << | Spread: ${spread:.2f} ({spread_bps:.1f} bps)")

                        if last_price > 0:
                            price_diff = mid_price - last_price
                            print(f"           Last Trade: ${last_price:,.2f} | "
                                  f"Mid vs Last: ${price_diff:+.2f}")

                        print()

        except KeyboardInterrupt:
            print(f"\n\nStopping BRTI stream... (Received {update_count} updates)")
        except Exception as e:
            print(f"\n❌ Error: {e}")


async def get_single_brti_snapshot():
    """Get a single BRTI proxy snapshot and exit"""

    print(f"\nFetching BRTI Proxy (Mid-Price) from Coinbase...")

    async with websockets.connect(COINBASE_WS_URL, ssl=get_ssl_context()) as ws:
        subscribe_msg = {
            "type": "subscribe",
            "product_ids": ["BTC-USD"],
            "channels": ["ticker"]
        }

        await ws.send(json.dumps(subscribe_msg))

        # Wait for first ticker message with bid/ask data
        while True:
            message = await ws.recv()
            data = json.loads(message)

            if data.get('type') == 'ticker':
                if 'best_bid' in data and 'best_ask' in data:
                    best_bid = float(data['best_bid'])
                    best_ask = float(data['best_ask'])

                    # Calculate Mid-Price (BRTI Proxy)
                    mid_price = (best_bid + best_ask) / 2

                    spread = best_ask - best_bid
                    spread_bps = (spread / mid_price) * 10000

                    last_price = float(data.get('price', 0))
                    time_str = data.get('time')
                    timestamp = datetime.fromisoformat(time_str.replace('Z', '+00:00'))

                    print(f"\n{'='*80}")
                    print(f"BRTI PROXY (MID-PRICE): ${mid_price:,.2f}")
                    print(f"{'='*80}")
                    print(f"Best Bid:     ${best_bid:,.2f}")
                    print(f"Best Ask:     ${best_ask:,.2f}")
                    print(f"Spread:       ${spread:.2f} ({spread_bps:.1f} bps)")
                    if last_price > 0:
                        print(f"Last Trade:   ${last_price:,.2f}")
                        print(f"Mid vs Trade: ${mid_price - last_price:+.2f}")
                    print(f"Timestamp:    {timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} UTC")
                    print(f"{'='*80}\n")
                    break


def main():
    """Main entry point - streams BRTI proxy continuously"""
    try:
        asyncio.run(brti_proxy_stream())
    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    import sys

    # Use --once flag to get a single BRTI snapshot and exit
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        asyncio.run(get_single_brti_snapshot())
    else:
        main()

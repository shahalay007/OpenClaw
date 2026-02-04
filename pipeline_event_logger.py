#!/usr/bin/env python3
"""
Complete pipeline: Fetch active event → Log live bid/ask for all markets
"""

import argparse
import asyncio
import base64
import json
import os
import subprocess
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from fetch_active_event_simple import get_active_event_for_series
from kalshi_sdk import API_KEY_ID, PRIVATE_KEY_PEM

KALSHI_WS_URL = os.getenv("KALSHI_WS_URL", "wss://api.elections.kalshi.com/trade-api/ws/v2")


def sign_message(message: str) -> str:
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


def normalize_levels(levels):
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


def update_best_prices(orderbooks, ticker):
    book = orderbooks.get(ticker)
    if not book:
        return None
    yes_bids = book.get("yes") or []
    no_bids = book.get("no") or []
    best_yes_bid = yes_bids[-1][0] if yes_bids else None
    best_no_bid = no_bids[-1][0] if no_bids else None
    yes_ask = (100 - best_no_bid) if best_no_bid is not None else None
    no_ask = (100 - best_yes_bid) if best_yes_bid is not None else None
    yes_bid = best_yes_bid
    no_bid = best_no_bid
    return {
        "yes_buy": yes_bid,
        "yes_sell": yes_ask,
        "no_buy": no_bid,
        "no_sell": no_ask,
    }


async def stream_all_markets(tickers, out_path):
    """Stream orderbook data for multiple markets"""
    orderbooks = {}
    last_printed = {}

    while True:
        timestamp = str(int(time.time() * 1000))
        message = timestamp + "GET" + "/trade-api/ws/v2"
        signature = sign_message(message)
        headers = {
            "KALSHI-ACCESS-KEY": API_KEY_ID,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp
        }

        last_message_ts = time.time()
        try:
            async with websockets.connect(KALSHI_WS_URL, additional_headers=headers) as ws:
                subscribe_msg = {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta"],
                        "market_tickers": tickers,
                    }
                }
                await ws.send(json.dumps(subscribe_msg))

                while True:
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    except asyncio.TimeoutError:
                        if time.time() - last_message_ts > 30:
                            break
                        continue

                    last_message_ts = time.time()
                    try:
                        data = json.loads(message)
                        msg_type = data.get("type")
                        if msg_type == "subscribed":
                            continue

                        if msg_type == "orderbook_snapshot":
                            msg = data.get("msg", {})
                            t = msg.get("market_ticker")
                            if t not in tickers:
                                continue
                            orderbooks[t] = {
                                "yes": normalize_levels(msg.get("yes")),
                                "no": normalize_levels(msg.get("no")),
                            }
                        elif msg_type == "orderbook_delta":
                            msg = data.get("msg", {})
                            t = msg.get("market_ticker")
                            side = msg.get("side")
                            price = msg.get("price")
                            delta = msg.get("delta")
                            if t not in tickers or side not in ("yes", "no") or price is None or delta is None:
                                continue
                            book = orderbooks.setdefault(t, {"yes": [], "no": []})
                            levels = book[side]

                            # Find current quantity at this price level
                            current_qty = 0
                            for lvl in levels:
                                if lvl[0] == price:
                                    current_qty = lvl[1]
                                    break

                            # Apply delta to get new quantity
                            new_qty = current_qty + delta

                            if new_qty <= 0:
                                # Remove level
                                levels[:] = [lvl for lvl in levels if lvl[0] != price]
                            else:
                                # Update or add level
                                updated = False
                                for idx, lvl in enumerate(levels):
                                    if lvl[0] == price:
                                        levels[idx] = [float(price), new_qty]
                                        updated = True
                                        break
                                if not updated:
                                    levels.append([float(price), new_qty])
                            levels.sort(key=lambda item: item[0])
                        else:
                            continue

                        # Update prices for the affected ticker
                        if msg_type in ("orderbook_snapshot", "orderbook_delta"):
                            t = msg.get("msg", {}).get("market_ticker")
                            if t:
                                prices = update_best_prices(orderbooks, t)
                                if not prices:
                                    continue

                                if last_printed.get(t) != prices:
                                    last_printed[t] = prices
                                    ts = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                                    line = (
                                        f"{ts} {t} "
                                        f"yes_buy={prices['yes_buy']}¢ yes_sell={prices['yes_sell']}¢ "
                                        f"no_buy={prices['no_buy']}¢ no_sell={prices['no_sell']}¢\n"
                                    )
                                    with open(out_path, "a") as f:
                                        f.write(line)
                                    print(line, end="")
                    except Exception:
                        continue
        except Exception:
            await asyncio.sleep(1)


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline: Fetch active event and log all market bid/ask prices"
    )
    parser.add_argument("--series", default="KXBTC", help="Series ticker (default: KXBTC)")
    parser.add_argument("--out", help="Output log file (default: {event_ticker}_all_markets.log)")
    args = parser.parse_args()

    print(f"\n{'='*80}")
    print("KALSHI EVENT LOGGER PIPELINE")
    print('='*80)

    # Step 1: Fetch active event
    print(f"\nStep 1: Fetching active event for series {args.series}...")
    event = get_active_event_for_series(args.series)

    if not event:
        print("No active event found. Exiting.")
        return

    event_ticker = event['event_ticker']
    markets = event['markets']

    print(f"\n✓ Active Event: {event_ticker}")
    print(f"  Title: {event['title']}")
    print(f"  Markets: {len(markets)}")

    # Find specific market if requested
    target_market = None
    for m in markets:
        if "89,250" in m['subtitle']:
            target_market = m
            print(f"\n✓ Found $89,250-$89,499.99 market: {m['ticker']}")
            break

    # Step 2: Prepare tickers list
    tickers = [m['ticker'] for m in markets]

    # Output file
    out_file = args.out or f"{event_ticker.lower()}_all_markets.log"

    print(f"\nStep 2: Starting WebSocket logger for {len(tickers)} markets...")
    print(f"  Output: {out_file}")
    print(f"\n{'='*80}")
    print("LIVE BID/ASK LOG (Press Ctrl+C to stop)")
    print('='*80 + "\n")

    # Step 3: Run logger
    asyncio.run(stream_all_markets(tickers, out_file))


if __name__ == "__main__":
    main()

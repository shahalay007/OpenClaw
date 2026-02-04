#!/usr/bin/env python3
"""
Log live bid/ask updates for markets whose subtitle/title contains 89499 or 89500.
Writes separate files for KXBTC and KXBTCD.
"""

import argparse
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
    yes_ask = (100 - best_no_bid) / 100 if best_no_bid is not None else None
    no_ask = (100 - best_yes_bid) / 100 if best_yes_bid is not None else None
    yes_bid = best_yes_bid / 100 if best_yes_bid is not None else None
    no_bid = best_no_bid / 100 if best_no_bid is not None else None
    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
    }


def match_markets(event, tokens):
    matches = []
    for m in event.get("markets", []):
        title = (m.get("title") or "")
        subtitle = (m.get("subtitle") or "")
        hay = f"{title} {subtitle}".replace(",", "")
        if any(tok in hay for tok in tokens):
            matches.append(m["ticker"])
    return matches


def build_ticker_map(tokens):
    ticker_map = {}
    for series in ("KXBTC", "KXBTCD"):
        event = get_active_event_for_series(series)
        if not event:
            ticker_map[series] = []
            continue
        ticker_map[series] = match_markets(event, tokens)
    return ticker_map


async def stream_and_log(ticker_map, out_dir):
    tickers = sorted({t for ts in ticker_map.values() for t in ts})
    if not tickers:
        print("No matching tickers found.")
        return

    orderbooks = {}
    last_printed = {}

    timestamp = str(int(time.time() * 1000))
    message = timestamp + "GET" + "/trade-api/ws/v2"
    signature = sign_message(message)
    headers = {
        "KALSHI-ACCESS-KEY": API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp
    }

    series_for_ticker = {}
    for series, ts in ticker_map.items():
        for t in ts:
            series_for_ticker[t] = series

    print(f"Subscribing to {len(tickers)} tickers...")
    print(f"Output dir: {out_dir}")

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

        async for message in ws:
            try:
                data = json.loads(message)
                msg_type = data.get("type")
                if msg_type == "subscribed":
                    continue

                if msg_type == "orderbook_snapshot":
                    msg = data.get("msg", {})
                    ticker = msg.get("market_ticker")
                    if not ticker:
                        continue
                    orderbooks[ticker] = {
                        "yes": normalize_levels(msg.get("yes")),
                        "no": normalize_levels(msg.get("no")),
                    }
                elif msg_type == "orderbook_delta":
                    msg = data.get("msg", {})
                    ticker = msg.get("market_ticker")
                    side = msg.get("side")
                    price = msg.get("price")
                    qty = msg.get("quantity")
                    if ticker is None or side not in ("yes", "no") or price is None:
                        continue
                    book = orderbooks.setdefault(ticker, {"yes": [], "no": []})
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
                else:
                    continue

                prices = update_best_prices(orderbooks, ticker)
                if not prices:
                    continue

                prev = last_printed.get(ticker)
                if prev != prices:
                    last_printed[ticker] = prices
                    ts = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    series = series_for_ticker.get(ticker, "UNKNOWN")
                    out_path = os.path.join(out_dir, f"{series.lower()}_89499_89500.log")
                    line = (
                        f"{ts} {ticker} "
                        f"yes_bid={prices['yes_bid']} yes_ask={prices['yes_ask']} "
                        f"no_bid={prices['no_bid']} no_ask={prices['no_ask']}\n"
                    )
                    with open(out_path, "a") as f:
                        f.write(line)
            except Exception:
                continue


def main():
    parser = argparse.ArgumentParser(
        description="Log live bid/ask for markets containing 89499 or 89500."
    )
    parser.add_argument(
        "--out-dir",
        default=".",
        help="Directory to write log files."
    )
    args = parser.parse_args()

    tokens = ["89499", "89500", "89,499", "89,500"]
    ticker_map = build_ticker_map(tokens)
    print(ticker_map)
    asyncio.run(stream_and_log(ticker_map, args.out_dir))


if __name__ == "__main__":
    main()

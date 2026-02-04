#!/usr/bin/env python3
"""
Log live bid/ask updates for a single ticker to a dedicated file.
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
        "yes_buy": yes_bid,   # best YES bid (highest price to buy YES)
        "yes_sell": yes_ask,  # best YES ask (lowest price to sell YES, implied from 100-NO_bid)
        "no_buy": no_bid,     # best NO bid (highest price to buy NO)
        "no_sell": no_ask,    # best NO ask (lowest price to sell NO, implied from 100-YES_bid)
    }


async def stream_and_log(ticker, out_path):
    orderbooks = {}
    last_printed = None

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
                        "market_tickers": [ticker],
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
                            if t != ticker:
                                continue
                            yes_levels = normalize_levels(msg.get("yes"))
                            no_levels = normalize_levels(msg.get("no"))
                            orderbooks[ticker] = {
                                "yes": yes_levels,
                                "no": no_levels,
                            }
                        elif msg_type == "orderbook_delta":
                            msg = data.get("msg", {})
                            t = msg.get("market_ticker")
                            side = msg.get("side")
                            price = msg.get("price")
                            delta = msg.get("delta")
                            if t != ticker or side not in ("yes", "no") or price is None or delta is None:
                                continue
                            book = orderbooks.setdefault(ticker, {"yes": [], "no": []})
                            levels = book[side]

                            # Find current quantity at this price level
                            current_qty = 0
                            for lvl in levels:
                                if lvl[0] == price:
                                    current_qty = lvl[1]
                                    break

                            # Apply delta to get new quantity
                            new_qty = current_qty + delta

                            action = "REMOVE" if new_qty <= 0 else ("UPDATE" if current_qty > 0 else "ADD")

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

                        prices = update_best_prices(orderbooks, ticker)
                        if not prices:
                            continue

                        if last_printed != prices:
                            last_printed = prices
                            ts = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                            line = (
                                f"{ts} {ticker} "
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
        description="Log live bid/ask updates for a single ticker."
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    asyncio.run(stream_and_log(args.ticker, args.out))


if __name__ == "__main__":
    main()

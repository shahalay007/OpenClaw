#!/usr/bin/env python3
"""
Restart live_relevant_kxbtcd_markets.py at :01, :16, :31, :46 each hour.
Uses the KXBTC15M series (single-market event).
"""

import asyncio
import base64
import json
import os
import signal
import subprocess
import time
from datetime import datetime

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from fetch_active_event_simple import get_active_event_for_series
from kalshi_sdk import API_KEY_ID, PRIVATE_KEY_PEM

LOG_BASE = "kxbtc15m_live"
LOG_DIR = os.path.abspath(os.path.dirname(__file__))

SERIES_TICKER = "KXBTC15M"
KALSHI_WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"


def sign_message(message: str) -> str:
    private_key = serialization.load_pem_private_key(
        PRIVATE_KEY_PEM.encode(),
        password=None,
    )
    signature = private_key.sign(
        message.encode(),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode()


async def _check_coinbase(timeout=20):
    url = "wss://ws-feed.exchange.coinbase.com"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({
            "type": "subscribe",
            "product_ids": ["BTC-USD"],
            "channels": ["ticker"],
        }))
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(msg)
            if data.get("type") == "ticker" and "best_bid" in data and "best_ask" in data:
                return True


async def _check_kraken(timeout=20):
    url = "wss://ws.kraken.com"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({
            "event": "subscribe",
            "pair": ["XBT/USD"],
            "subscription": {"name": "ticker"},
        }))
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(msg)
            if isinstance(data, list):
                ticker_data = data[1]
                if "b" in ticker_data and "a" in ticker_data:
                    return True


async def _check_bitstamp(timeout=20):
    url = "wss://ws.bitstamp.net"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({
            "event": "bts:subscribe",
            "data": {"channel": "order_book_btcusd"},
        }))
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(msg)
            if data.get("event") == "data":
                payload = data.get("data", {})
                if payload.get("bids") and payload.get("asks"):
                    return True


async def _check_gemini(timeout=20):
    url = "wss://api.gemini.com/v1/marketdata/BTCUSD?top_of_book=true"
    async with websockets.connect(url) as ws:
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(msg)
            if data.get("type") == "update":
                events = data.get("events", [])
                for event in events:
                    if event.get("type") == "change" and event.get("side") in ("bid", "ask"):
                        return True


async def _check_kalshi(timeout=20):
    event = get_active_event_for_series(SERIES_TICKER)
    if not event or not event.get("markets"):
        return False
    ticker = event["markets"][0]["ticker"]

    timestamp = str(int(time.time() * 1000))
    message = timestamp + "GET" + "/trade-api/ws/v2"
    signature = sign_message(message)
    headers = {
        "KALSHI-ACCESS-KEY": API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }

    async with websockets.connect(KALSHI_WS_URL, additional_headers=headers) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "cmd": "subscribe",
            "params": {"channels": ["orderbook_delta"], "market_tickers": [ticker]},
        }))
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(msg)
            if data.get("type") in ("orderbook_snapshot", "orderbook_delta"):
                return True


async def preflight_check():
    results = await asyncio.gather(
        _check_coinbase(),
        _check_kraken(),
        _check_bitstamp(),
        _check_gemini(),
        _check_kalshi(),
        return_exceptions=True,
    )
    return all(r is True for r in results)


def start_all():
    procs = []
    cmd_feeder = [
        "python3",
        "brti_feeder.py",
    ]
    procs.append(subprocess.Popen(cmd_feeder))
    log = os.path.join(LOG_DIR, f"{LOG_BASE}.log")
    cmd_live = [
        "python3",
        "live_kxbtc15m.py",
        "--series",
        SERIES_TICKER,
        "--out",
        log,
    ]
    procs.append(subprocess.Popen(cmd_live))
    return procs


def stop_all(procs):
    for p in procs:
        try:
            p.send_signal(signal.SIGINT)
        except Exception:
            pass
    time.sleep(2)
    for p in procs:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass


def logs_healthy(stamp, stale_seconds=120):
    now_ts = time.time()
    path = os.path.join(LOG_DIR, f"{LOG_BASE}_{stamp}.log")
    if not os.path.exists(path):
        return False
    age = now_ts - os.path.getmtime(path)
    return age <= stale_seconds


def main():
    subprocess.run(["pkill", "-f", "live_relevant_kxbtcd_markets.py"], check=False)
    subprocess.run(["pkill", "-f", "brti_feeder.py"], check=False)
    subprocess.run(["pkill", "-f", "live_kxbtc15m.py"], check=False)

    procs = start_all()
    while True:
        time.sleep(5)
        # Restart if any process exited
        if not procs or any(p.poll() is not None for p in procs):
            stop_all(procs)
            procs = start_all()


if __name__ == "__main__":
    main()

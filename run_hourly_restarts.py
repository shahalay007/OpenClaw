#!/usr/bin/env python3
"""
Restart live_relevant_kxbtcd_markets.py at every hh:01 with fixed configs.
Runs configs on a fixed schedule.
"""

import asyncio
import base64
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timedelta

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from fetch_active_event_simple import get_active_event_for_series
from kalshi_sdk import API_KEY_ID, PRIVATE_KEY_PEM

CONFIGS = [
    (20, 10, 1, "relevant_markets_20_10"),
    (40, 20, 1, "relevant_markets_40_20"),
    (30, 10, 1, "relevant_markets_30_10"),
    (30, 20, 1, "relevant_markets_30_20"),
    (50, 30, 1, "relevant_markets_50_30"),
]

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
    event = get_active_event_for_series("KXBTCD")
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


def next_run_time(now=None):
    now = now or datetime.now()
    target = now.replace(minute=1, second=0, microsecond=0)
    if now >= target:
        target = (now + timedelta(hours=1)).replace(minute=1, second=0, microsecond=0)
    return target


def start_all():
    procs = []
    stamp = datetime.now().strftime("%Y%m%d_%H")
    for entry, exit_, early, log_base in CONFIGS:
        log = f"{log_base}_{stamp}.log"
        cmd = [
            "python3",
            "live_kxbtcd_60m.py",
            "--entry",
            str(entry),
            "--exit",
            str(exit_),
            "--early-exit",
            str(early),
            "--out",
            log,
        ]
        procs.append(subprocess.Popen(cmd))
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
    ok = True
    for _, _, _, log_base in CONFIGS:
        path = f"{log_base}_{stamp}.log"
        if not os.path.exists(path):
            ok = False
            break
        age = now_ts - os.path.getmtime(path)
        if age > stale_seconds:
            ok = False
            break
    return ok


def main():
    # Ensure no previous instances are running
    subprocess.run(["pkill", "-f", "live_relevant_kxbtcd_markets.py"], check=False)

    # Start immediately after a preflight check
    procs = []
    while True:
        try:
            ok = asyncio.run(preflight_check())
        except Exception:
            ok = False
        if ok:
            break
        time.sleep(5)
    procs = start_all()
    current_stamp = datetime.now().strftime("%Y%m%d_%H")
    last_watchdog = time.time()
    while True:
        target = next_run_time()
        while datetime.now() < target:
            time.sleep(5)
            if time.time() - last_watchdog >= 30:
                last_watchdog = time.time()
                if not logs_healthy(current_stamp):
                    if procs:
                        stop_all(procs)
                    while True:
                        try:
                            ok = asyncio.run(preflight_check())
                        except Exception:
                            ok = False
                        if ok:
                            break
                        time.sleep(5)
                    procs = start_all()
                    current_stamp = datetime.now().strftime("%Y%m%d_%H")
        if procs:
            stop_all(procs)
        # Preflight between x:00 and x:01, then start when ready
        while True:
            try:
                ok = asyncio.run(preflight_check())
            except Exception:
                ok = False
            if ok:
                break
            time.sleep(5)
        procs = start_all()
        current_stamp = datetime.now().strftime("%Y%m%d_%H")


if __name__ == "__main__":
    main()

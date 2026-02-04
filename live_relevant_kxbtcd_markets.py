#!/usr/bin/env python3
"""
Live 60s BRTI average -> select relevant KXBTCD markets -> stream bid/ask.
"""

import argparse
import asyncio
import base64
import json
import os
import re
import ssl
import time
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from fetch_active_event_simple import get_active_event_for_series
from kalshi_sdk import API_KEY_ID, PRIVATE_KEY_PEM

try:
    import certifi
    CERT_PATH = certifi.where()
except ImportError:
    CERT_PATH = None

KALSHI_WS_URL = os.getenv("KALSHI_WS_URL", "wss://api.elections.kalshi.com/trade-api/ws/v2")

# Exchange data storage
exchange_data = {
    "coinbase": {"bid": None, "ask": None, "price": None, "spread": None, "bid_vol": None, "ask_vol": None, "timestamp": None, "last_update": 0},
    "kraken": {"bid": None, "ask": None, "price": None, "spread": None, "bid_vol": None, "ask_vol": None, "timestamp": None, "last_update": 0},
    "bitstamp": {"bid": None, "ask": None, "price": None, "spread": None, "bid_vol": None, "ask_vol": None, "timestamp": None, "last_update": 0},
    "gemini": {"bid": None, "ask": None, "price": None, "spread": None, "bid_vol": None, "ask_vol": None, "timestamp": None, "last_update": 0},
}

# VOI snapshot history (60s window)
voi_history = deque(maxlen=60)
ENABLE_VOI = False

# 1 Hz snapshots (60s window)
snapshot_history = deque(maxlen=60)


def get_ssl_context():
    ssl_context = ssl.create_default_context()
    if CERT_PATH:
        ssl_context.load_verify_locations(CERT_PATH)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    return ssl_context


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


def _to_float(val):
    try:
        return float(val)
    except Exception:
        return None


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
    # top 2 levels for bids and derived asks with quantities
    yes_bid_lvls = yes_bids[-2:] if len(yes_bids) >= 2 else yes_bids[:]
    no_bid_lvls = no_bids[-2:] if len(no_bids) >= 2 else no_bids[:]
    yes_ask_lvls = []
    for price, qty in reversed(no_bid_lvls):
        yes_ask_lvls.append([100 - price, qty])
    no_ask_lvls = []
    for price, qty in reversed(yes_bid_lvls):
        no_ask_lvls.append([100 - price, qty])
    return {
        "yes_bid": best_yes_bid,
        "yes_ask": yes_ask,
        "no_bid": best_no_bid,
        "no_ask": no_ask,
        "yes_bid_lvls": yes_bid_lvls,
        "yes_ask_lvls": yes_ask_lvls,
        "no_bid_lvls": no_bid_lvls,
        "no_ask_lvls": no_ask_lvls,
    }


def parse_threshold_from_ticker(ticker: str):
    match = re.search(r"-T(\d+(?:\.\d+)?)$", ticker)
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def select_relevant_markets(price: float, markets):
    levels = []
    for m in markets:
        t = m.get("ticker")
        if not t:
            continue
        level = parse_threshold_from_ticker(t)
        if level is None:
            continue
        levels.append((level, t))

    if len(levels) < 2:
        return []

    levels.sort(key=lambda x: x[0])
    lower = None
    upper = None
    for level, t in levels:
        if level <= price:
            lower = (level, t)
        elif level > price and upper is None:
            upper = (level, t)
            break

    if lower is None:
        return [levels[0][1], levels[1][1]]
    if upper is None:
        return [levels[-2][1], levels[-1][1]]
    return [lower[1], upper[1]]


async def coinbase_feed():
    import json
    url = "wss://ws-feed.exchange.coinbase.com"
    async with websockets.connect(url, ssl=get_ssl_context()) as ws:
        await ws.send(json.dumps({
            "type": "subscribe",
            "product_ids": ["BTC-USD"],
            "channels": ["ticker"],
        }))
        async for message in ws:
            data = json.loads(message)
            if data.get("type") == "ticker" and "best_bid" in data and "best_ask" in data:
                bid = _to_float(data.get("best_bid"))
                ask = _to_float(data.get("best_ask"))
                bid_vol = _to_float(data.get("best_bid_size"))
                ask_vol = _to_float(data.get("best_ask_size"))
                if bid is None or ask is None:
                    continue
                exchange_data["coinbase"] = {
                    "bid": bid,
                    "ask": ask,
                    "price": (bid + ask) / 2,
                    "spread": ask - bid,
                    "bid_vol": bid_vol,
                    "ask_vol": ask_vol,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    "last_update": time.time(),
                }


async def kraken_feed():
    import json
    url = "wss://ws.kraken.com"
    last_bid = None
    last_ask = None
    async with websockets.connect(url, ssl=get_ssl_context()) as ws:
        await ws.send(json.dumps({
            "event": "subscribe",
            "pair": ["XBT/USD"],
            "subscription": {"name": "ticker"},
        }))
        async for message in ws:
            data = json.loads(message)
            if isinstance(data, list):
                ticker_data = data[1]
                if "b" in ticker_data and "a" in ticker_data:
                    bid_price = _to_float(ticker_data["b"][0])
                    ask_price = _to_float(ticker_data["a"][0])
                    if bid_price is None or ask_price is None:
                        continue
                    if bid_price != last_bid or ask_price != last_ask:
                        last_bid = bid_price
                        last_ask = ask_price
                        bid_vol = _to_float(ticker_data["b"][1])
                        ask_vol = _to_float(ticker_data["a"][1])
                        exchange_data["kraken"] = {
                            "bid": bid_price,
                            "ask": ask_price,
                            "price": (bid_price + ask_price) / 2,
                            "spread": ask_price - bid_price,
                            "bid_vol": bid_vol,
                            "ask_vol": ask_vol,
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                            "last_update": time.time(),
                        }


async def gemini_feed():
    import json
    url = "wss://api.gemini.com/v1/marketdata/BTCUSD?top_of_book=true"
    latest_bid = None
    latest_ask = None
    latest_bid_vol = None
    latest_ask_vol = None
    async with websockets.connect(url, ssl=get_ssl_context()) as ws:
        async for message in ws:
            data = json.loads(message)
            if data.get("type") == "update":
                events = data.get("events", [])
                for event in events:
                    if event.get("type") == "change":
                        side = event.get("side")
                        price = event.get("price")
                        reason = event.get("reason")
                        remaining = event.get("remaining")
                        if reason in ("top-of-book", "initial"):
                            if side == "bid":
                                latest_bid = _to_float(price)
                                rem = _to_float(remaining)
                                if rem is not None:
                                    latest_bid_vol = rem
                            elif side == "ask":
                                latest_ask = _to_float(price)
                                rem = _to_float(remaining)
                                if rem is not None:
                                    latest_ask_vol = rem
                if latest_bid is not None and latest_ask is not None:
                    exchange_data["gemini"] = {
                        "bid": latest_bid,
                        "ask": latest_ask,
                        "price": (latest_bid + latest_ask) / 2,
                        "spread": latest_ask - latest_bid,
                        "bid_vol": latest_bid_vol,
                        "ask_vol": latest_ask_vol,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        "last_update": time.time(),
                    }


async def bitstamp_feed():
    import json
    url = "wss://ws.bitstamp.net"
    async with websockets.connect(url, ssl=get_ssl_context()) as ws:
        subscribe_msg = {
            "event": "bts:subscribe",
            "data": {"channel": "order_book_btcusd"},
        }
        await ws.send(json.dumps(subscribe_msg))
        async for message in ws:
            data = json.loads(message)
            if data.get("event") == "data":
                payload = data.get("data", {})
                try:
                    bid = _to_float(payload.get("bids", [[None]])[0][0])
                    ask = _to_float(payload.get("asks", [[None]])[0][0])
                    bid_vol = _to_float(payload.get("bids", [[None, None]])[0][1])
                    ask_vol = _to_float(payload.get("asks", [[None, None]])[0][1])
                except Exception:
                    continue
                if bid is None or ask is None:
                    continue
                exchange_data["bitstamp"] = {
                    "bid": bid,
                    "ask": ask,
                    "price": (bid + ask) / 2,
                    "spread": ask - bid,
                    "bid_vol": bid_vol,
                    "ask_vol": ask_vol,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    "last_update": time.time(),
                }


def _format_price(value):
    return "N/A" if value is None else f"{value:.1f}¢"


def log_trigger(event, avg60, diff, ticker, side, prices):
    ts = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    bid = prices.get("bid")
    ask = prices.get("ask")
    line = (
        f"{ts} {event} diff={diff:,.2f} "
        f"avg60=${avg60:,.2f} {side} {ticker} "
        f"bid={_format_price(bid)} ask={_format_price(ask)}"
    )
    return line


def _format_line(exchange):
    data = exchange_data[exchange]
    if data["price"] is None:
        return f"{exchange.upper()}: Connecting..."
    return (
        f"[{data['timestamp']}] {exchange.upper()}: "
        f"${data['price']:,.2f} | Bid: ${data['bid']:,.2f} | "
        f"Ask: ${data['ask']:,.2f} | Spread: ${data['spread']:.2f}"
    )


def _compute_weighted_brti():
    now_time = time.time()
    weighted_sum = 0
    active_weights = 0
    weights = {"coinbase": 0.40, "kraken": 0.37, "bitstamp": 0.13, "gemini": 0.10}
    staleness_limit = 30  # 30 seconds for all exchanges

    for exchange, weight in weights.items():
        data = exchange_data[exchange]
        if data["bid"] is not None and data["ask"] is not None:
            if now_time - data["last_update"] < staleness_limit:
                mid_price = (data["bid"] + data["ask"]) / 2
                weighted_sum += mid_price * weight
                active_weights += weight

    if active_weights > 0:
        return weighted_sum / active_weights
    return None


def _compute_voi():
    now_time = time.time()
    weights = {"coinbase": 0.40, "kraken": 0.37, "bitstamp": 0.13, "gemini": 0.10}
    staleness_limit = 30
    v_bid_composite = 0.0
    v_ask_composite = 0.0
    has_data = False

    for exchange, weight in weights.items():
        data = exchange_data[exchange]
        if data["bid_vol"] is not None and data["ask_vol"] is not None:
            if now_time - data["last_update"] < staleness_limit:
                v_bid_composite += weight * data["bid_vol"]
                v_ask_composite += weight * data["ask_vol"]
                has_data = True

    if not has_data:
        return None
    denom = v_bid_composite + v_ask_composite
    if denom == 0:
        return 0.0
    return (v_bid_composite - v_ask_composite) / denom


def _write_voi_log():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.000")
    lines = []

    current_voi = voi_history[-1] if voi_history else None
    if current_voi is not None:
        lines.append(f"[{ts}] VOI: {current_voi:+.4f}")
    else:
        lines.append(f"[{ts}] VOI: waiting for data...")

    for window in (10, 20, 30, 60):
        if len(voi_history) >= window:
            avg = sum(list(voi_history)[-window:]) / window
            lines.append(f"VOI {window}s AVG: {avg:+.4f}")
        else:
            lines.append(f"VOI {window}s AVG: Collecting... ({len(voi_history)}/{window})")

    lines.append("")
    now_time = time.time()
    for exchange in ("coinbase", "kraken", "bitstamp", "gemini"):
        data = exchange_data[exchange]
        stale = (now_time - data["last_update"]) > 30
        bv = data["bid_vol"]
        av = data["ask_vol"]
        status = "STALE" if stale else "OK"
        lines.append(f"  {exchange.upper()}: bid_vol={bv} ask_vol={av} [{status}]")

    with open("voi.log", "w") as f:
        f.write("\n".join(lines) + "\n")


def _append_line(out_path, line):
    with open(out_path, "a") as f:
        f.write(line + "\n")


async def _preflight_coinbase(timeout=20):
    url = "wss://ws-feed.exchange.coinbase.com"
    async with websockets.connect(url, ssl=get_ssl_context()) as ws:
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


async def _preflight_kraken(timeout=20):
    url = "wss://ws.kraken.com"
    async with websockets.connect(url, ssl=get_ssl_context()) as ws:
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


async def _preflight_bitstamp(timeout=20):
    url = "wss://ws.bitstamp.net"
    async with websockets.connect(url, ssl=get_ssl_context()) as ws:
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


async def _preflight_gemini(timeout=20):
    url = "wss://api.gemini.com/v1/marketdata/BTCUSD?top_of_book=true"
    async with websockets.connect(url, ssl=get_ssl_context()) as ws:
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(msg)
            if data.get("type") == "update":
                events = data.get("events", [])
                for event in events:
                    if event.get("type") == "change" and event.get("side") in ("bid", "ask"):
                        return True


async def _preflight_kalshi(timeout=20):
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


async def _preflight_check():
    results = await asyncio.gather(
        _preflight_coinbase(),
        _preflight_kraken(),
        _preflight_bitstamp(),
        _preflight_gemini(),
        _preflight_kalshi(),
        return_exceptions=True,
    )
    return all(r is True for r in results)

def _write_output(out_paths, triggers, stats):
    lines = []
    lines.append(_format_line("coinbase"))
    lines.append(_format_line("kraken"))
    lines.append(_format_line("bitstamp"))
    lines.append(_format_line("gemini"))
    lines.append("")

    weighted = stats.get("weighted_brti")
    if weighted is None:
        lines.append("WEIGHTED BRTI: Waiting for data...")
    else:
        lines.append(
            f"[{stats['weighted_ts']}] WEIGHTED BRTI (CB:40% KR:37% BS:13% GM:10%): ${weighted:,.2f}"
        )

    if stats.get("snapshot_ts") is None:
        lines.append("1-SECOND SNAPSHOT BRTI: Waiting for snapshot...")
    else:
        lines.append(f"[{stats['snapshot_ts']}] 1-SECOND SNAPSHOT BRTI: ${stats['snapshot']:,.2f}")
        force_avgs = stats.get("force_avgs")
        if stats.get("fixed_avg60") is not None:
            lines.append(f"60-SECOND SNAPSHOT AVG (AT MARKET START): ${stats['fixed_avg60']:,.2f}")
        for window in (10, 20, 30, 60):
            key = f"avg{window}"
            if stats.get(key) is None and not force_avgs:
                lines.append(f"{window}-SECOND SNAPSHOT AVG: Collecting... ({stats['count']}/{window})")
            else:
                val = stats.get(key)
                if val is not None:
                    lines.append(f"{window}-SECOND SNAPSHOT AVG: ${val:,.2f}")
    diff = stats.get("diff")
    if diff is not None:
        lines.append(f"20s-60s AVG DIFF: ${diff:,.2f}")
        slope60 = stats.get("slope60")
        if slope60 is not None:
            lines.append(f"Slope_60: ${slope60:,.2f}")

    table_lines = stats.get("table_lines")
    if table_lines:
        lines.append("")
        lines.append(
            "TS                          AVG60         DIFF20_60    SLOPE60     "
            "YES_BID2                YES_ASK2                NO_BID2                 NO_ASK2"
        )
        lines.extend(table_lines)

    lines.append("")
    lines.extend(triggers)
    if isinstance(out_paths, (list, tuple)):
        paths = out_paths
    else:
        paths = [out_paths]
    for path in paths:
        if not path:
            continue
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")


async def brti_recorder(market_queue, markets, latest_prices, current_pair, out_path, entry_thresh, exit_thresh, early_exit_thresh, enable_trades=True, enable_thresholds=True, shared_state=None):
    last_processed_second = -1
    last_selected = None
    pos_active = False
    neg_active = False
    allow_pos = True
    allow_neg = True
    pos_entry_ticker = None
    neg_entry_ticker = None
    pos_entry_bid = None
    pos_entry_ask = None
    neg_entry_bid = None
    neg_entry_ask = None
    pos_exit_stage = None
    neg_exit_stage = None
    pos_exit_target = None
    neg_exit_target = None
    pos_exit_fee_on_fill = False
    neg_exit_fee_on_fill = False
    pos_entry_line = None
    pos_entry_side = "YES"
    neg_entry_line = None
    neg_entry_side = "NO"
    trade_counter = 0
    triggers = []
    diff_prev = None
    diff_prev2 = None
    max_logged_in_pos = False
    min_logged_in_neg = False
    last_extreme = None
    allow_no_signal = True
    allow_yes_signal = True
    min_signal_abs_diff = 8.0
    avg60_series = deque(maxlen=6)
    table_lines = deque(maxlen=15 * 60)
    table_active = False
    table_end_ts = None
    stats = {
        "weighted_brti": None,
        "weighted_ts": None,
        "snapshot": None,
        "snapshot_ts": None,
        "avg10": None,
        "avg20": None,
        "avg30": None,
        "avg60": None,
        "diff": None,
        "count": 0,
        "fixed_avg60": None,
    }
    while True:
        now_dt = datetime.now()
        current_second = now_dt.second
        if current_second != last_processed_second:
            if shared_state and shared_state.get("external_mode"):
                ext_stats = shared_state.get("external_stats")
                ext_ex = shared_state.get("external_exchange")
                if ext_ex:
                    for k, v in ext_ex.items():
                        if k in exchange_data and isinstance(v, dict):
                            exchange_data[k].update(v)
                if ext_stats:
                    stats.update(ext_stats)
                # external stats already computed; skip local snapshot updates
                if stats.get("diff") is not None:
                    diff_prev2 = diff_prev
                    diff_prev = stats["diff"]
                if stats.get("avg60") is not None:
                    avg60_series.append(stats["avg60"])
                    if len(avg60_series) >= 6:
                        stats["slope60"] = avg60_series[-1] - avg60_series[0]
                if shared_state and shared_state.get("reset_triggers"):
                    triggers.clear()
                    event_ticker = shared_state.get("event_ticker")
                    if event_ticker:
                        ts = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        triggers.append(f"{ts} Switched to event {event_ticker}")
                    stats["fixed_avg60"] = stats.get("avg60")
                    if shared_state.get("table_active"):
                        table_lines.clear()
                        table_active = True
                        table_end_ts = time.time() + (15 * 60)
                    shared_state["reset_triggers"] = False

                if not enable_thresholds and table_active and table_end_ts is not None and stats.get("avg60") is not None and stats.get("diff") is not None:
                    if time.time() <= table_end_ts:
                        market_ticker = markets[0]["ticker"] if markets else None
                        prices = latest_prices.get(market_ticker, {}) if market_ticker else {}
                        def _fmt(lvls):
                            parts = []
                            for price, qty in (lvls or [])[:2]:
                                parts.append(f"{price:.2f}@{qty}")
                            return ", ".join(parts)
                        ts = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                        table_lines.append(
                            f"{ts:<27} "
                            f"{stats['avg60']:>10,.2f}   "
                            f"{stats['diff']:>9.2f}   "
                            f"{(stats.get('slope60') or 0.0):>8.2f}   "
                            f"{_fmt(prices.get('yes_bid_lvls')):<22} "
                            f"{_fmt(prices.get('yes_ask_lvls')):<22} "
                            f"{_fmt(prices.get('no_bid_lvls')):<23} "
                            f"{_fmt(prices.get('no_ask_lvls'))}"
                        )
                    else:
                        table_active = False
                        table_end_ts = None

                weighted = _compute_weighted_brti()
                if weighted is not None:
                    stats["weighted_brti"] = weighted
                    stats["weighted_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                if not enable_thresholds and not enable_trades and stats.get("diff") is not None:
                    diff = stats["diff"]
                    slope60 = stats.get("slope60")
                    if diff < 0:
                        allow_no_signal = True
                    elif diff > 0:
                        allow_yes_signal = True
                    if diff_prev is not None and diff_prev2 is not None and slope60 is not None:
                        if (allow_no_signal
                                and diff_prev2 > diff_prev > diff
                                and diff_prev2 > 0 and diff_prev > 0 and diff > 0
                                and diff_prev2 >= min_signal_abs_diff
                                and diff >= min_signal_abs_diff
                                and slope60 < 0):
                            ts = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                            market_ticker = markets[0]["ticker"] if markets else "N/A"
                            prices = latest_prices.get(market_ticker, {})
                            triggers.append(
                                f"{ts} MR_NO diff={diff:+.2f} slope60={slope60:+.2f} "
                                f"YES_bid={prices.get('yes_bid')} YES_ask={prices.get('yes_ask')} "
                                f"NO_bid={prices.get('no_bid')} NO_ask={prices.get('no_ask')}"
                            )
                            allow_no_signal = False
                        if (allow_yes_signal
                                and diff_prev2 < diff_prev < diff
                                and diff_prev2 < 0 and diff_prev < 0 and diff < 0
                                and abs(diff_prev2) >= min_signal_abs_diff
                                and diff <= -min_signal_abs_diff
                                and slope60 > 0):
                            ts = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                            market_ticker = markets[0]["ticker"] if markets else "N/A"
                            prices = latest_prices.get(market_ticker, {})
                            triggers.append(
                                f"{ts} MR_YES diff={diff:+.2f} slope60={slope60:+.2f} "
                                f"YES_bid={prices.get('yes_bid')} YES_ask={prices.get('yes_ask')} "
                                f"NO_bid={prices.get('no_bid')} NO_ask={prices.get('no_ask')}"
                            )
                            allow_yes_signal = False
                if table_lines:
                    stats["table_lines"] = list(table_lines)

                alt_path = shared_state.get("alt_out_path") if shared_state else None
                _write_output([out_path, alt_path], triggers, stats)
                last_processed_second = current_second
                await asyncio.sleep(0.01)
                continue
            if shared_state and shared_state.get("reset_triggers"):
                triggers.clear()
                event_ticker = shared_state.get("event_ticker")
                if event_ticker:
                    ts = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    triggers.append(f"{ts} Switched to event {event_ticker}")
                stats["fixed_avg60"] = stats.get("avg60")
                if shared_state.get("table_active"):
                    table_lines.clear()
                    table_active = True
                    table_end_ts = time.time() + (15 * 60)
                shared_state["reset_triggers"] = False
            final_brti = _compute_weighted_brti()
            if final_brti is not None:
                snapshot_history.append(final_brti)
                stats["snapshot"] = final_brti
                stats["snapshot_ts"] = now_dt.strftime("%Y-%m-%d %H:%M:%S.000")
                stats["count"] = len(snapshot_history)

                # VOI snapshot at 1 Hz (disabled)
                if ENABLE_VOI:
                    voi = _compute_voi()
                    if voi is not None:
                        voi_history.append(voi)
                        _write_voi_log()

                relevant = None
                if len(snapshot_history) >= 60:
                    avg60 = sum(snapshot_history) / len(snapshot_history)
                    stats["avg60"] = avg60
                    avg60_series.append(avg60)
                    if len(avg60_series) >= 6:
                        stats["slope60"] = avg60_series[-1] - avg60_series[0]

                    if len(snapshot_history) >= 20:
                        avg20 = sum(list(snapshot_history)[-20:]) / 20
                        stats["avg20"] = avg20
                        diff = avg20 - avg60
                        stats["diff"] = diff

                        # 15m local peak/trough logging (no threshold logic, no trades)
                        if not enable_thresholds and not enable_trades and not table_active:
                            if diff > 0:
                                min_logged_in_neg = False
                            elif diff < 0:
                                max_logged_in_pos = False

                            if diff_prev is not None and diff_prev2 is not None:
                                is_peak = diff_prev2 > diff_prev > diff and diff_prev2 > 0 and diff_prev > 0 and diff > 0
                                is_trough = diff_prev2 < diff_prev < diff and diff_prev2 < 0 and diff_prev < 0 and diff < 0
                                label = "MAX" if is_peak else "MIN"
                                if ((is_peak and not max_logged_in_pos) or (is_trough and not min_logged_in_neg)) and label != last_extreme:
                                    ts = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                                    market_ticker = markets[0]["ticker"] if markets else "N/A"
                                    prices = latest_prices.get(market_ticker, {})
                                    def _fmt_lvls(lvls):
                                        parts = []
                                        for price, qty in (lvls or [])[:2]:
                                            parts.append(f"{price:.2f}¢@{qty}")
                                        return "[" + ", ".join(parts) + "]"
                                    line = (
                                        f"{ts} {label} "
                                        f"60-SECOND SNAPSHOT AVG: ${avg60:,.2f} "
                                        f"20s-60s AVG DIFF: {diff:+.2f} "
                                        f"YES_bid2={_fmt_lvls(prices.get('yes_bid_lvls'))} "
                                        f"YES_ask2={_fmt_lvls(prices.get('yes_ask_lvls'))} "
                                        f"NO_bid2={_fmt_lvls(prices.get('no_bid_lvls'))} "
                                        f"NO_ask2={_fmt_lvls(prices.get('no_ask_lvls'))}"
                                    )
                                    triggers.append(line)
                                    if is_peak:
                                        max_logged_in_pos = True
                                    if is_trough:
                                        min_logged_in_neg = True
                                    last_extreme = label

                        if not enable_thresholds and len(snapshot_history) > 0:
                            hist = list(snapshot_history)
                            stats["avg10"] = sum(hist[-10:]) / min(len(hist), 10)
                            stats["avg20"] = sum(hist[-20:]) / min(len(hist), 20)
                            stats["avg30"] = sum(hist[-30:]) / min(len(hist), 30)
                            stats["avg60"] = sum(hist[-60:]) / min(len(hist), 60)
                            stats["diff"] = stats["avg20"] - stats["avg60"]
                            stats["force_avgs"] = True

                        if not enable_thresholds and table_active and table_end_ts is not None:
                            if time.time() <= table_end_ts:
                                market_ticker = markets[0]["ticker"] if markets else None
                                prices = latest_prices.get(market_ticker, {}) if market_ticker else {}
                                def _fmt(lvls):
                                    parts = []
                                    for price, qty in (lvls or [])[:2]:
                                        parts.append(f"{price:.2f}@{qty}")
                                    return ", ".join(parts)
                                ts = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                                table_lines.append(
                                    f"{ts:<27} "
                                    f"{stats['avg60']:>10,.2f}   "
                                    f"{stats['diff']:>9.2f}   "
                                    f"{(stats.get('slope60') or 0.0):>8.2f}   "
                                    f"{_fmt(prices.get('yes_bid_lvls')):<22} "
                                    f"{_fmt(prices.get('yes_ask_lvls')):<22} "
                                    f"{_fmt(prices.get('no_bid_lvls')):<23} "
                                    f"{_fmt(prices.get('no_ask_lvls'))}"
                                )
                            else:
                                table_active = False
                                table_end_ts = None

                        # 15m trading logic on a single market (no thresholds)
                        if not enable_thresholds and enable_trades:
                            market_ticker = markets[0]["ticker"] if markets else None
                            if market_ticker:
                                prices = latest_prices.get(market_ticker, {})
                                yes_bid = prices.get("yes_bid")
                                yes_ask = prices.get("yes_ask")
                                no_bid = prices.get("no_bid")
                                no_ask = prices.get("no_ask")

                                if diff < 0:
                                    allow_pos = True
                                if diff > 0:
                                    allow_neg = True

                                if not pos_active and diff > entry_thresh:
                                    spread_ok = (yes_bid is not None and yes_ask is not None and (yes_ask - yes_bid) <= 3.0)
                                    if spread_ok and allow_pos:
                                        line = log_trigger("POS_TRIGGER", avg60, diff, market_ticker, "YES", {
                                            "bid": yes_bid,
                                            "ask": yes_ask,
                                        })
                                        pos_entry_line = f"ENTRY  {line}"
                                        _append_line(out_path, pos_entry_line)
                                        triggers.append(pos_entry_line)
                                        pos_entry_bid = yes_bid
                                        pos_entry_ask = yes_ask
                                        pos_active = True
                                        pos_entry_ticker = market_ticker
                                        pos_exit_stage = "ask"
                                        pos_exit_target = yes_ask
                                        pos_exit_fee_on_fill = False
                                        allow_pos = False
                                elif pos_active and diff < exit_thresh:
                                    pass

                                if not neg_active and diff < -entry_thresh:
                                    spread_ok = (no_bid is not None and no_ask is not None and (no_ask - no_bid) <= 3.0)
                                    if spread_ok and allow_neg:
                                        line = log_trigger("NEG_TRIGGER", avg60, diff, market_ticker, "NO", {
                                            "bid": no_bid,
                                            "ask": no_ask,
                                        })
                                        neg_entry_line = f"ENTRY  {line}"
                                        _append_line(out_path, neg_entry_line)
                                        triggers.append(neg_entry_line)
                                        neg_entry_bid = no_bid
                                        neg_entry_ask = no_ask
                                        neg_active = True
                                        neg_entry_ticker = market_ticker
                                        neg_exit_stage = "ask"
                                        neg_exit_target = no_ask
                                        neg_exit_fee_on_fill = False
                                        allow_neg = False
                                elif neg_active and diff > -exit_thresh:
                                    pass

                        if enable_thresholds:
                            relevant = select_relevant_markets(avg60, markets)
                            if relevant and relevant != last_selected:
                                last_selected = relevant
                                current_pair["tickers"] = relevant
                                await market_queue.put(relevant)

                            if stats.get("diff") is not None:
                                diff = stats["diff"]
                                if diff < 0:
                                    allow_pos = True
                                if diff > 0:
                                    allow_neg = True

                                if enable_trades and relevant and not pos_active and diff > entry_thresh:
                                    below = relevant[0]
                                    strike = parse_threshold_from_ticker(below)
                                    in_upper_band = False
                                    if strike is not None:
                                        in_upper_band = (avg60 > strike - 100) and (avg60 < strike + 100)
                                    if in_upper_band:
                                        prices = latest_prices.get(below, {})
                                        yes_bid = prices.get("yes_bid")
                                        yes_ask = prices.get("yes_ask")
                                        spread_ok = (yes_bid is not None and yes_ask is not None and (yes_ask - yes_bid) <= 3.0)
                                        if spread_ok and allow_pos:
                                            line = log_trigger("POS_TRIGGER", avg60, diff, below, "YES", {
                                                "bid": yes_bid,
                                                "ask": yes_ask,
                                            })
                                            pos_entry_line = f"ENTRY  {line}"
                                            _append_line(out_path, pos_entry_line)
                                            triggers.append(pos_entry_line)
                                            pos_entry_bid = yes_bid
                                            pos_entry_ask = yes_ask
                                            pos_active = True
                                            pos_entry_ticker = below
                                            pos_exit_stage = "ask"
                                            pos_exit_target = yes_ask
                                            pos_exit_fee_on_fill = False
                                            allow_pos = False
                                elif enable_trades and pos_active and diff < exit_thresh:
                                    pass

                                if enable_trades and relevant and not neg_active and diff < -entry_thresh:
                                    above = relevant[1]
                                strike = parse_threshold_from_ticker(above)
                                in_lower_band = False
                                if strike is not None:
                                    in_lower_band = (avg60 > strike - 100) and (avg60 < strike + 100)
                                if in_lower_band:
                                    prices = latest_prices.get(above, {})
                                    no_bid = prices.get("no_bid")
                                    no_ask = prices.get("no_ask")
                                    spread_ok = (no_bid is not None and no_ask is not None and (no_ask - no_bid) <= 3.0)
                                    if spread_ok and allow_neg:
                                        line = log_trigger("NEG_TRIGGER", avg60, diff, above, "NO", {
                                            "bid": no_bid,
                                            "ask": no_ask,
                                        })
                                        neg_entry_line = f"ENTRY  {line}"
                                        _append_line(out_path, neg_entry_line)
                                        triggers.append(neg_entry_line)
                                        neg_entry_bid = no_bid
                                        neg_entry_ask = no_ask
                                        neg_active = True
                                        neg_entry_ticker = above
                                        neg_exit_stage = "ask"
                                        neg_exit_target = no_ask
                                        neg_exit_fee_on_fill = False
                                        allow_neg = False
                            elif enable_trades and neg_active and diff > -exit_thresh:
                                pass

                    if len(snapshot_history) >= 30:
                        stats["avg30"] = sum(list(snapshot_history)[-30:]) / 30
                    if not enable_thresholds:
                        stats["force_avgs"] = True
                else:
                    if not enable_thresholds and len(snapshot_history) > 0:
                        hist = list(snapshot_history)
                        stats["avg10"] = sum(hist[-10:]) / min(len(hist), 10)
                        stats["avg20"] = sum(hist[-20:]) / min(len(hist), 20)
                        stats["avg30"] = sum(hist[-30:]) / min(len(hist), 30)
                        stats["avg60"] = sum(hist[-60:]) / min(len(hist), 60)
                        if stats["avg20"] is not None and stats["avg60"] is not None:
                            stats["diff"] = stats["avg20"] - stats["avg60"]
                        stats["force_avgs"] = True
                    else:
                        stats["avg20"] = None
                        stats["avg30"] = None
                        stats["avg60"] = None
                        stats["diff"] = None

                if stats.get("diff") is not None:
                    diff_prev2 = diff_prev
                    diff_prev = stats["diff"]


            weighted = _compute_weighted_brti()
            if weighted is not None:
                stats["weighted_brti"] = weighted
                stats["weighted_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            if table_lines:
                stats["table_lines"] = list(table_lines)

            # Exit checks for open positions
            if enable_trades and pos_active and pos_entry_ticker:
                cur_prices = latest_prices.get(pos_entry_ticker, {})
                cur_bid = cur_prices.get("yes_bid")
                cur_ask = cur_prices.get("yes_ask")
                if pos_entry_bid is not None and cur_bid is not None:
                    diff_now = stats.get("diff")
                    # Final fallback: market exit if ask <= entry_bid
                    if cur_ask is not None and cur_ask <= pos_entry_bid:
                        trade_counter += 1
                        triggers.append(f"Trade {trade_counter}")
                        if pos_entry_line:
                            triggers.append(pos_entry_line)
                        line = log_trigger(
                            "POS_EXIT_MARKET_FALLBACK",
                            stats.get("avg60") or 0.0,
                            diff_now or 0.0,
                            pos_entry_ticker,
                            "YES",
                            {"bid": cur_bid, "ask": cur_ask},
                        )
                        triggers.append(f"EXIT   {line}")
                        triggers.append("exit_reason=market_fallback")
                        sell_price = cur_bid
                        bid_diff = sell_price - pos_entry_bid
                        sell_prob = sell_price / 100.0
                        fee_dollars = 0.07 * sell_prob * (1 - sell_prob)
                        net = bid_diff - fee_dollars * 100.0
                        triggers.append(
                            f"bid_diff={bid_diff:.2f}¢ fee=${fee_dollars:.6f} net={net:.2f}¢ sell_bid={sell_price:.2f}¢"
                        )
                        pos_active = False
                        pos_entry_ticker = None
                        pos_entry_bid = None
                        pos_entry_ask = None
                        pos_exit_stage = None
                        pos_exit_target = None
                        pos_exit_fee_on_fill = False
                        pos_entry_line = None
                    else:
                        # Fill at current target (maker, no fee)
                        if pos_exit_target is not None and cur_bid >= pos_exit_target:
                            trade_counter += 1
                            triggers.append(f"Trade {trade_counter}")
                            if pos_entry_line:
                                triggers.append(pos_entry_line)
                            line = log_trigger(
                                "POS_EXIT_LIMIT_FILLED",
                                stats.get("avg60") or 0.0,
                                diff_now or 0.0,
                                pos_entry_ticker,
                                "YES",
                                {"bid": cur_bid, "ask": cur_ask},
                            )
                            triggers.append(f"EXIT   {line}")
                            if pos_exit_stage == "ask":
                                triggers.append("exit_reason=limit_entry_ask_no_fee")
                            elif pos_exit_stage == "plus_one":
                                triggers.append("exit_reason=limit_plus_one_no_fee")
                            else:
                                triggers.append("exit_reason=limit_entry_bid_no_fee")
                            sell_price = pos_exit_target
                            bid_diff = sell_price - pos_entry_bid
                            fee_dollars = 0.0
                            net = bid_diff - fee_dollars * 100.0
                            triggers.append(
                                f"bid_diff={bid_diff:.2f}¢ fee=${fee_dollars:.6f} net={net:.2f}¢ sell_bid={sell_price:.2f}¢"
                            )
                            pos_active = False
                            pos_entry_ticker = None
                            pos_entry_bid = None
                            pos_entry_ask = None
                            pos_exit_stage = None
                            pos_exit_target = None
                            pos_exit_fee_on_fill = False
                            pos_entry_line = None
                        else:
                            # Stage progression based on diff
                            if diff_now is not None:
                                if pos_exit_stage == "ask" and diff_now < entry_thresh:
                                    target = pos_entry_bid + 1.0
                                    if cur_bid >= target:
                                        trade_counter += 1
                                        triggers.append(f"Trade {trade_counter}")
                                        if pos_entry_line:
                                            triggers.append(pos_entry_line)
                                        line = log_trigger(
                                            "POS_EXIT_LIMIT_PLUS_ONE_IMMEDIATE",
                                            stats.get("avg60") or 0.0,
                                            diff_now,
                                            pos_entry_ticker,
                                            "YES",
                                            {"bid": cur_bid, "ask": cur_ask},
                                        )
                                        triggers.append(f"EXIT   {line}")
                                        triggers.append("exit_reason=limit_plus_one_immediate_fee")
                                        sell_price = target
                                        bid_diff = sell_price - pos_entry_bid
                                        sell_prob = sell_price / 100.0
                                        fee_dollars = 0.07 * sell_prob * (1 - sell_prob)
                                        net = bid_diff - fee_dollars * 100.0
                                        triggers.append(
                                            f"bid_diff={bid_diff:.2f}¢ fee=${fee_dollars:.6f} net={net:.2f}¢ sell_bid={sell_price:.2f}¢"
                                        )
                                        pos_active = False
                                        pos_entry_ticker = None
                                        pos_entry_bid = None
                                        pos_entry_ask = None
                                        pos_exit_stage = None
                                        pos_exit_target = None
                                        pos_exit_fee_on_fill = False
                                        pos_entry_line = None
                                    else:
                                        pos_exit_stage = "plus_one"
                                        pos_exit_target = target
                                        pos_exit_fee_on_fill = False
                                if pos_active and pos_exit_stage in ("ask", "plus_one") and diff_now < exit_thresh:
                                    target = pos_entry_bid
                                    if cur_bid >= target:
                                        trade_counter += 1
                                        triggers.append(f"Trade {trade_counter}")
                                        if pos_entry_line:
                                            triggers.append(pos_entry_line)
                                        line = log_trigger(
                                            "POS_EXIT_LIMIT_BID_IMMEDIATE",
                                            stats.get("avg60") or 0.0,
                                            diff_now,
                                            pos_entry_ticker,
                                            "YES",
                                            {"bid": cur_bid, "ask": cur_ask},
                                        )
                                        triggers.append(f"EXIT   {line}")
                                        triggers.append("exit_reason=limit_bid_immediate_fee")
                                        sell_price = target
                                        bid_diff = sell_price - pos_entry_bid
                                        sell_prob = sell_price / 100.0
                                        fee_dollars = 0.07 * sell_prob * (1 - sell_prob)
                                        net = bid_diff - fee_dollars * 100.0
                                        triggers.append(
                                            f"bid_diff={bid_diff:.2f}¢ fee=${fee_dollars:.6f} net={net:.2f}¢ sell_bid={sell_price:.2f}¢"
                                        )
                                        pos_active = False
                                        pos_entry_ticker = None
                                        pos_entry_bid = None
                                        pos_entry_ask = None
                                        pos_exit_stage = None
                                        pos_exit_target = None
                                        pos_exit_fee_on_fill = False
                                        pos_entry_line = None
                                    else:
                                        pos_exit_stage = "bid"
                                        pos_exit_target = target
                                        pos_exit_fee_on_fill = False

            if enable_trades and neg_active and neg_entry_ticker:
                cur_prices = latest_prices.get(neg_entry_ticker, {})
                cur_bid = cur_prices.get("no_bid")
                cur_ask = cur_prices.get("no_ask")
                if neg_entry_bid is not None and cur_bid is not None:
                    diff_now = stats.get("diff")
                    if cur_ask is not None and cur_ask <= neg_entry_bid:
                        trade_counter += 1
                        triggers.append(f"Trade {trade_counter}")
                        if neg_entry_line:
                            triggers.append(neg_entry_line)
                        line = log_trigger(
                            "NEG_EXIT_MARKET_FALLBACK",
                            stats.get("avg60") or 0.0,
                            diff_now or 0.0,
                            neg_entry_ticker,
                            "NO",
                            {"bid": cur_bid, "ask": cur_ask},
                        )
                        triggers.append(f"EXIT   {line}")
                        triggers.append("exit_reason=market_fallback")
                        sell_price = cur_bid
                        bid_diff = sell_price - neg_entry_bid
                        sell_prob = sell_price / 100.0
                        fee_dollars = 0.07 * sell_prob * (1 - sell_prob)
                        net = bid_diff - fee_dollars * 100.0
                        triggers.append(
                            f"bid_diff={bid_diff:.2f}¢ fee=${fee_dollars:.6f} net={net:.2f}¢ sell_bid={sell_price:.2f}¢"
                        )
                        neg_active = False
                        neg_entry_ticker = None
                        neg_entry_bid = None
                        neg_entry_ask = None
                        neg_exit_stage = None
                        neg_exit_target = None
                        neg_exit_fee_on_fill = False
                        neg_entry_line = None
                    else:
                        if neg_exit_target is not None and cur_bid >= neg_exit_target:
                            trade_counter += 1
                            triggers.append(f"Trade {trade_counter}")
                            if neg_entry_line:
                                triggers.append(neg_entry_line)
                            line = log_trigger(
                                "NEG_EXIT_LIMIT_FILLED",
                                stats.get("avg60") or 0.0,
                                diff_now or 0.0,
                                neg_entry_ticker,
                                "NO",
                                {"bid": cur_bid, "ask": cur_ask},
                            )
                            triggers.append(f"EXIT   {line}")
                            if neg_exit_stage == "ask":
                                triggers.append("exit_reason=limit_entry_ask_no_fee")
                            elif neg_exit_stage == "plus_one":
                                triggers.append("exit_reason=limit_plus_one_no_fee")
                            else:
                                triggers.append("exit_reason=limit_entry_bid_no_fee")
                            sell_price = neg_exit_target
                            bid_diff = sell_price - neg_entry_bid
                            fee_dollars = 0.0
                            net = bid_diff - fee_dollars * 100.0
                            triggers.append(
                                f"bid_diff={bid_diff:.2f}¢ fee=${fee_dollars:.6f} net={net:.2f}¢ sell_bid={sell_price:.2f}¢"
                            )
                            neg_active = False
                            neg_entry_ticker = None
                            neg_entry_bid = None
                            neg_entry_ask = None
                            neg_exit_stage = None
                            neg_exit_target = None
                            neg_exit_fee_on_fill = False
                            neg_entry_line = None
                        else:
                            if diff_now is not None:
                                if neg_exit_stage == "ask" and diff_now > -entry_thresh:
                                    target = neg_entry_bid + 1.0
                                    if cur_bid >= target:
                                        trade_counter += 1
                                        triggers.append(f"Trade {trade_counter}")
                                        if neg_entry_line:
                                            triggers.append(neg_entry_line)
                                        line = log_trigger(
                                            "NEG_EXIT_LIMIT_PLUS_ONE_IMMEDIATE",
                                            stats.get("avg60") or 0.0,
                                            diff_now,
                                            neg_entry_ticker,
                                            "NO",
                                            {"bid": cur_bid, "ask": cur_ask},
                                        )
                                        triggers.append(f"EXIT   {line}")
                                        triggers.append("exit_reason=limit_plus_one_immediate_fee")
                                        sell_price = target
                                        bid_diff = sell_price - neg_entry_bid
                                        sell_prob = sell_price / 100.0
                                        fee_dollars = 0.07 * sell_prob * (1 - sell_prob)
                                        net = bid_diff - fee_dollars * 100.0
                                        triggers.append(
                                            f"bid_diff={bid_diff:.2f}¢ fee=${fee_dollars:.6f} net={net:.2f}¢ sell_bid={sell_price:.2f}¢"
                                        )
                                        neg_active = False
                                        neg_entry_ticker = None
                                        neg_entry_bid = None
                                        neg_entry_ask = None
                                        neg_exit_stage = None
                                        neg_exit_target = None
                                        neg_exit_fee_on_fill = False
                                        neg_entry_line = None
                                    else:
                                        neg_exit_stage = "plus_one"
                                        neg_exit_target = target
                                        neg_exit_fee_on_fill = False
                                if neg_active and neg_exit_stage in ("ask", "plus_one") and diff_now > -exit_thresh:
                                    target = neg_entry_bid
                                    if cur_bid >= target:
                                        trade_counter += 1
                                        triggers.append(f"Trade {trade_counter}")
                                        if neg_entry_line:
                                            triggers.append(neg_entry_line)
                                        line = log_trigger(
                                            "NEG_EXIT_LIMIT_BID_IMMEDIATE",
                                            stats.get("avg60") or 0.0,
                                            diff_now,
                                            neg_entry_ticker,
                                            "NO",
                                            {"bid": cur_bid, "ask": cur_ask},
                                        )
                                        triggers.append(f"EXIT   {line}")
                                        triggers.append("exit_reason=limit_bid_immediate_fee")
                                        sell_price = target
                                        bid_diff = sell_price - neg_entry_bid
                                        sell_prob = sell_price / 100.0
                                        fee_dollars = 0.07 * sell_prob * (1 - sell_prob)
                                        net = bid_diff - fee_dollars * 100.0
                                        triggers.append(
                                            f"bid_diff={bid_diff:.2f}¢ fee=${fee_dollars:.6f} net={net:.2f}¢ sell_bid={sell_price:.2f}¢"
                                        )
                                        neg_active = False
                                        neg_entry_ticker = None
                                        neg_entry_bid = None
                                        neg_entry_ask = None
                                        neg_exit_stage = None
                                        neg_exit_target = None
                                        neg_exit_fee_on_fill = False
                                        neg_entry_line = None
                                    else:
                                        neg_exit_stage = "bid"
                                        neg_exit_target = target
                                        neg_exit_fee_on_fill = False

            alt_path = shared_state.get("alt_out_path") if shared_state else None
            _write_output([out_path, alt_path], triggers, stats)
            last_processed_second = current_second

        await asyncio.sleep(0.01)


async def kalshi_streamer(market_queue, latest_prices):
    orderbooks = {}
    current_markets = None

    while True:
        if current_markets is None:
            current_markets = await market_queue.get()
            market_queue.task_done()

        timestamp = str(int(time.time() * 1000))
        message = timestamp + "GET" + "/trade-api/ws/v2"
        signature = sign_message(message)
        headers = {
            "KALSHI-ACCESS-KEY": API_KEY_ID,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

        last_message_ts = time.time()
        try:
            async with websockets.connect(KALSHI_WS_URL, additional_headers=headers) as ws:
                subscribe_msg = {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta"],
                        "market_tickers": current_markets,
                    },
                }
                await ws.send(json.dumps(subscribe_msg))

                while True:
                    # If new markets are ready, break to reconnect
                    if not market_queue.empty():
                        current_markets = await market_queue.get()
                        market_queue.task_done()
                        orderbooks = {}
                        break

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
                            if t not in current_markets:
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
                            if t not in current_markets or side not in ("yes", "no") or price is None or delta is None:
                                continue
                            book = orderbooks.setdefault(t, {"yes": [], "no": []})
                            levels = book[side]

                            current_qty = 0
                            for lvl in levels:
                                if lvl[0] == price:
                                    current_qty = lvl[1]
                                    break
                            new_qty = current_qty + delta

                            if new_qty <= 0:
                                levels[:] = [lvl for lvl in levels if lvl[0] != price]
                            else:
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

                        if msg_type in ("orderbook_snapshot", "orderbook_delta"):
                            t = msg.get("market_ticker")
                            if t:
                                prices = update_best_prices(orderbooks, t)
                                if not prices:
                                    continue
                                latest_prices[t] = prices
                    except Exception:
                        continue
        except Exception:
            await asyncio.sleep(1)


async def main_async(series, out_path, entry_thresh, exit_thresh, early_exit_thresh, enable_trades=True, enable_thresholds=True, shared_state=None):
    event = get_active_event_for_series(series)
    if not event:
        print("No active event found.")
        return

    markets = event["markets"]
    market_queue = asyncio.Queue()
    latest_prices = {}
    current_pair = {"tickers": None}

    if not enable_thresholds:
        await market_queue.put([m["ticker"] for m in markets])

    await asyncio.gather(
        coinbase_feed(),
        kraken_feed(),
        bitstamp_feed(),
        gemini_feed(),
        brti_recorder(
            market_queue,
            markets,
            latest_prices,
            current_pair,
            out_path,
            entry_thresh,
            exit_thresh,
            early_exit_thresh,
            enable_trades,
            enable_thresholds,
            shared_state,
        ),
        kalshi_streamer(market_queue, latest_prices),
    )


def main():
    parser = argparse.ArgumentParser(description="Select relevant KXBTCD markets using 60s BRTI avg.")
    parser.add_argument("--series", default="KXBTCD", help="Series ticker (default: KXBTCD)")
    parser.add_argument("--out", default="relevant_markets_live_bidask.log", help="Output file")
    parser.add_argument("--entry", type=float, default=20.0, help="Entry threshold for diff (positive)")
    parser.add_argument("--exit", type=float, default=10.0, help="Exit threshold for diff (positive)")
    parser.add_argument("--early-exit", type=float, default=3.0, help="Early exit threshold for bid_diff (cents)")
    parser.add_argument("--no-trades", action="store_true", help="Disable entry/exit logic (logging only)")
    parser.add_argument("--no-thresholds", action="store_true", help="Disable threshold-based market selection/logic")
    parser.add_argument("--no-preflight", action="store_true", help="Skip preflight checks")
    args = parser.parse_args()
    try:
        if not args.no_preflight:
            while True:
                try:
                    ok = asyncio.run(_preflight_check())
                except Exception:
                    ok = False
                if ok:
                    break
                time.sleep(5)
        asyncio.run(main_async(
            args.series,
            args.out,
            args.entry,
            args.exit,
            args.early_exit,
            not args.no_trades,
            not args.no_thresholds,
        ))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

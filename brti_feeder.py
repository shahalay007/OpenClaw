#!/usr/bin/env python3
"""
Continuous BRTI feeder: writes rolling stats + exchange bids/asks to a shared JSON file.
"""

import json
import os
import time
import asyncio

from live_relevant_kxbtcd_markets import (
    coinbase_feed,
    kraken_feed,
    bitstamp_feed,
    gemini_feed,
    exchange_data,
    _compute_weighted_brti,
)

OUT_PATH = os.path.join(os.path.dirname(__file__), "brti_shared.json")

snapshot_history = []


def _write_shared(stats):
    payload = {
        "ts": time.time(),
        "exchange": exchange_data,
        "stats": stats,
    }
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, OUT_PATH)


async def feeder():
    last_second = -1
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
    }
    while True:
        now = time.time()
        current_second = int(now)
        if current_second != last_second:
            last_second = current_second
            weighted = _compute_weighted_brti()
            if weighted is not None:
                snapshot_history.append(weighted)
                stats["snapshot"] = weighted
                stats["snapshot_ts"] = time.strftime("%Y-%m-%d %H:%M:%S.000", time.localtime())
                stats["count"] = len(snapshot_history)
                stats["weighted_brti"] = weighted
                stats["weighted_ts"] = time.strftime("%Y-%m-%d %H:%M:%S.%f", time.localtime())[:-3]

                hist = snapshot_history
                stats["avg10"] = sum(hist[-10:]) / 10 if len(hist) >= 10 else None
                stats["avg20"] = sum(hist[-20:]) / 20 if len(hist) >= 20 else None
                stats["avg30"] = sum(hist[-30:]) / 30 if len(hist) >= 30 else None
                stats["avg60"] = sum(hist[-60:]) / 60 if len(hist) >= 60 else None
                if stats["avg20"] is not None and stats["avg60"] is not None:
                    stats["diff"] = stats["avg20"] - stats["avg60"]
                else:
                    stats["diff"] = None

            _write_shared(stats)
        await asyncio.sleep(0.01)


async def main():
    async def _run_forever(name, coro):
        while True:
            try:
                await coro()
            except Exception:
                await asyncio.sleep(1)

    await asyncio.gather(
        _run_forever("coinbase", coinbase_feed),
        _run_forever("kraken", kraken_feed),
        _run_forever("bitstamp", bitstamp_feed),
        _run_forever("gemini", gemini_feed),
        _run_forever("feeder", feeder),
    )


if __name__ == "__main__":
    asyncio.run(main())

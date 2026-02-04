#!/usr/bin/env python3
"""
15-minute runner for KXBTC15M: data + BRTI snapshots only (no thresholds/trades).
"""

import argparse
import asyncio
import json
import time
import os

from fetch_active_event_simple import get_active_event_for_series
from live_relevant_kxbtcd_markets import (
    brti_recorder,
    kalshi_streamer,
)


def main():
    parser = argparse.ArgumentParser(description="KXBTC15M live data + BRTI snapshots (no trades).")
    parser.add_argument("--out", default="relevant_markets_15m.log", help="Output file")
    parser.add_argument("--series", default="KXBTC15M", help="Series ticker (default: KXBTC15M)")
    parser.add_argument("--entry", type=float, default=20.0, help="Entry threshold for diff (positive)")
    parser.add_argument("--exit", type=float, default=10.0, help="Exit threshold for diff (positive)")
    parser.add_argument("--early-exit", type=float, default=3.0, help="Early exit threshold (unused)")
    parser.add_argument("--no-preflight", action="store_true", help="Skip preflight checks")
    args = parser.parse_args()

    def _next_log_path():
        base_dir = os.path.dirname(args.out)
        counter_path = os.path.join(base_dir, "kxbtc15m_counter.txt")
        counter = 0
        try:
            with open(counter_path, "r") as f:
                counter = int(f.read().strip() or "0")
        except Exception:
            counter = 0
        while True:
            counter += 1
            candidate = os.path.join(base_dir, f"kxbtc15m_{counter}.log")
            if not os.path.exists(candidate):
                try:
                    with open(counter_path, "w") as f:
                        f.write(str(counter))
                except Exception:
                    pass
                return candidate

    async def monitor_events(market_queue, markets_ref, shared_state):
        last_event = None
        while True:
            try:
                event = get_active_event_for_series(args.series)
            except Exception:
                event = None
            if event and event.get("event_ticker") != last_event and event.get("markets"):
                last_event = event.get("event_ticker")
                new_markets = event["markets"]
                markets_ref[:] = new_markets
                shared_state["event_ticker"] = last_event
                shared_state["alt_out_path"] = _next_log_path()
                shared_state["reset_triggers"] = True
                shared_state["table_active"] = True
                await market_queue.put([m["ticker"] for m in new_markets])
            if not event:
                shared_state["event_ticker"] = None
                shared_state["reset_triggers"] = True
                shared_state["table_active"] = True
            await asyncio.sleep(5)

    async def read_brti(shared_state, path):
        while True:
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                shared_state["external_stats"] = data.get("stats")
                shared_state["external_exchange"] = data.get("exchange")
            except Exception:
                pass
            await asyncio.sleep(0.05)

    async def wait_for_brti(path, timeout=30):
        start = time.time()
        while True:
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                stats = data.get("stats") or {}
                exchange = data.get("exchange") or {}
                if stats.get("avg60") is not None and exchange:
                    return
            except Exception:
                pass
            if time.time() - start > timeout:
                return
            await asyncio.sleep(0.1)

    async def run():
        markets = []
        shared_state = {
            "reset_triggers": False,
            "event_ticker": None,
            "table_active": True,
            "external_mode": True,
            "external_stats": None,
            "external_exchange": None,
            "alt_out_path": None,
        }
        market_queue = asyncio.Queue()
        latest_prices = {}
        current_pair = {"tickers": None}
        brti_path = os.path.join(os.path.dirname(__file__), "brti_shared.json")

        await wait_for_brti(brti_path)
        await asyncio.gather(
            brti_recorder(
                market_queue,
                markets,
                latest_prices,
                current_pair,
                args.out,
                args.entry,
                args.exit,
                args.early_exit,
                enable_trades=False,
                enable_thresholds=False,
                shared_state=shared_state,
            ),
            kalshi_streamer(market_queue, latest_prices),
            monitor_events(market_queue, markets, shared_state),
            read_brti(shared_state, brti_path),
        )

    asyncio.run(run())


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
60-minute runner for KXBTCD: full thresholds/trading logic.
"""

import argparse
import asyncio
import time

from live_relevant_kxbtcd_markets import _preflight_check, main_async


def main():
    parser = argparse.ArgumentParser(description="KXBTCD live data + trading logic.")
    parser.add_argument("--out", default="relevant_markets_live_bidask.log", help="Output file")
    parser.add_argument("--series", default="KXBTCD", help="Series ticker (default: KXBTCD)")
    parser.add_argument("--entry", type=float, default=20.0, help="Entry threshold for diff (positive)")
    parser.add_argument("--exit", type=float, default=10.0, help="Exit threshold for diff (positive)")
    parser.add_argument("--early-exit", type=float, default=3.0, help="Early exit threshold for bid_diff (cents)")
    parser.add_argument("--no-preflight", action="store_true", help="Skip preflight checks")
    args = parser.parse_args()

    if not args.no_preflight:
        while True:
            try:
                ok = asyncio.run(_preflight_check())
            except Exception:
                ok = False
            if ok:
                break
            time.sleep(5)

    asyncio.run(
        main_async(
            args.series,
            args.out,
            args.entry,
            args.exit,
            args.early_exit,
            enable_trades=True,
            enable_thresholds=True,
        )
    )


if __name__ == "__main__":
    main()

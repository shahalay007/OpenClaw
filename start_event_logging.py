#!/usr/bin/env python3
"""
Complete Pipeline: Fetch active event and start logging all markets
Uses the proven log_one_ticker.py for each market
"""

import argparse
import subprocess
import time
from fetch_active_event_simple import get_active_event_for_series


def main():
    parser = argparse.ArgumentParser(
        description="Start logging all markets in the active event"
    )
    parser.add_argument("--series", default="KXBTC", help="Series ticker (default: KXBTC)")
    parser.add_argument("--single", help="Log only a specific price range (e.g., '89,250')")
    parser.add_argument("--limit", type=int, help="Limit number of markets to log (default: all)")
    args = parser.parse_args()

    print(f"\n{'='*80}")
    print("KALSHI EVENT LOGGER - COMPLETE PIPELINE")
    print('='*80)

    # Step 1: Fetch active event
    print(f"\n[1/3] Fetching active event for series {args.series}...")
    event = get_active_event_for_series(args.series)

    if not event:
        print("\n❌ No active event found. Exiting.")
        return

    event_ticker = event['event_ticker']
    markets = event['markets']

    print(f"\n✓ Active Event: {event_ticker}")
    print(f"  Title: {event['title']}")
    print(f"  Total Markets: {len(markets)}")

    # Step 2: Filter markets if needed
    selected_markets = []

    if args.single:
        # Find specific market
        for m in markets:
            if args.single in m['subtitle']:
                selected_markets.append(m)
                print(f"\n✓ Found market: {m['ticker']}")
                print(f"  Range: {m['subtitle']}")
                break
        if not selected_markets:
            print(f"\n❌ No market found matching '{args.single}'")
            return
    else:
        # Use all or limited markets
        selected_markets = markets[:args.limit] if args.limit else markets
        print(f"\n✓ Selected {len(selected_markets)} markets to log")

    # Step 3: Start loggers
    print(f"\n[2/3] Starting loggers...")

    processes = []
    log_dir = f"logs_{event_ticker.lower()}"
    subprocess.run(["mkdir", "-p", log_dir], check=False)

    for i, market in enumerate(selected_markets, 1):
        ticker = market['ticker']
        log_file = f"{log_dir}/{ticker.lower()}.log"

        # Start logger in background
        cmd = [
            "python3", "log_one_ticker.py",
            "--ticker", ticker,
            "--out", log_file
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        processes.append((ticker, proc, log_file))

        print(f"  [{i}/{len(selected_markets)}] Started logger for {ticker}")
        time.sleep(0.1)  # Prevent overwhelming the API

    print(f"\n✓ All {len(processes)} loggers started")
    print(f"  Log directory: {log_dir}/")

    # Step 4: Show status
    print(f"\n[3/3] Monitoring (waiting for initial data)...")
    time.sleep(5)

    print(f"\n{'='*80}")
    print("LOGGER STATUS")
    print('='*80 + "\n")

    for ticker, proc, log_file in processes:
        status = "✓ Running" if proc.poll() is None else f"✗ Stopped (code {proc.poll()})"

        # Check log file
        try:
            with open(log_file, 'r') as f:
                lines = f.readlines()
                log_status = f"{len(lines)} entries"
                if lines:
                    last_line = lines[-1].strip()
                    log_status += f" | Latest: {last_line.split(' ', 2)[1] if len(last_line.split()) > 1 else 'N/A'}"
        except FileNotFoundError:
            log_status = "No data yet"

        print(f"{ticker[:25]:<25} {status:<15} {log_status}")

    print(f"\n{'='*80}")
    print("PIPELINE COMPLETE")
    print('='*80)
    print(f"\nLoggers are running in the background.")
    print(f"Log files: {log_dir}/")
    print(f"\nTo view live updates for a specific market:")
    if selected_markets:
        print(f"  tail -f {log_dir}/{selected_markets[0]['ticker'].lower()}.log")
    print(f"\nTo stop all loggers:")
    print(f"  pkill -f 'log_one_ticker.py'")
    print()


if __name__ == "__main__":
    main()

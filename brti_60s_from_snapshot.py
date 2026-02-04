#!/usr/bin/env python3
"""
Compute a rolling 60-second average from the 1-second snapshot BRTI line
inside all_exchanges_live.txt, and print/write every second.

Example: at 09:33:00, average samples from 09:32:01 through 09:33:00.
"""

import time
from collections import deque
from datetime import datetime
from pathlib import Path

SOURCE = Path("all_exchanges_live.txt")
OUTPUT = Path("brti_60s_from_snapshot.txt")


def extract_snapshot_brti(text):
    """Return float BRTI from line like: 1-SECOND SNAPSHOT BRTI: $83,260.94"""
    marker = "1-SECOND SNAPSHOT BRTI:"
    for line in text.splitlines():
        if marker in line:
            try:
                val = line.split(marker, 1)[1].strip()
                val = val.replace("$", "").replace(",", "")
                return float(val)
            except Exception:
                return None
    return None


def main():
    # Store last 60 one-second samples
    samples = deque(maxlen=60)

    print("Starting 60-second average from 1-second snapshot BRTI...")
    print(f"Source: {SOURCE}")
    print(f"Output: {OUTPUT}")
    print("Press Ctrl+C to stop.\n")

    while True:
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")

        if SOURCE.exists():
            content = SOURCE.read_text()
            snapshot = extract_snapshot_brti(content)
        else:
            snapshot = None

        if snapshot is not None:
            samples.append(snapshot)

        if len(samples) < 60:
            line = f"[{ts}] COLLECTING: {len(samples)}/60 samples\n"
        else:
            avg = sum(samples) / len(samples)
            line = f"[{ts}] 60s AVG (t-59 to t): ${avg:,.2f}\n"

        OUTPUT.write_text(line)
        print(line.strip())

        # Sleep to the next second boundary
        time.sleep(1)


if __name__ == "__main__":
    main()

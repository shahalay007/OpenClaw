#!/usr/bin/env python3
"""
Capture the contents of all_exchanges_live.txt for 5 seconds into a new file.
"""

import time
from datetime import datetime
from pathlib import Path

SOURCE = Path("all_exchanges_live.txt")
OUT = Path("all_exchanges_live_5s_snapshot.log")
DURATION_SEC = 5.0
INTERVAL_SEC = 0.5


def main():
    start = time.time()
    lines = []

    while time.time() - start <= DURATION_SEC:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        if SOURCE.exists():
            content = SOURCE.read_text().strip()
        else:
            content = "SOURCE_FILE_MISSING"
        lines.append(f"[{ts}]\n{content}\n")
        time.sleep(INTERVAL_SEC)

    OUT.write_text("\n".join(lines) + "\n")
    print(f"Wrote snapshots to {OUT}")


if __name__ == "__main__":
    main()

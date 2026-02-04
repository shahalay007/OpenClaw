#!/usr/bin/env python3
"""
Check detailed market status and information
"""

import sys
import json
from kalshi_sdk import get_client

def check_market(ticker):
    """Check market status and details"""
    try:
        client = get_client()
        market = client.get_market(ticker)

        print(f"\n{'='*60}")
        print(f"MARKET STATUS: {ticker}")
        print(f"{'='*60}\n")

        # Print all market attributes
        market_dict = market.__dict__ if hasattr(market, '__dict__') else {}

        for key, value in sorted(market_dict.items()):
            if not key.startswith('_'):
                print(f"{key}: {value}")

        print(f"\n{'='*60}\n")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "KXBTC-26JAN2405-B89375"
    check_market(ticker)

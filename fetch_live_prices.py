#!/usr/bin/env python3
"""
Fetch current live bid/ask prices for a ticker using REST API
"""

import sys
from kalshi_sdk import get_client

def fetch_live_prices(ticker):
    """Fetch and display current market prices"""
    try:
        client = get_client()
        market = client.get_market(ticker)

        print(f"\n{'='*60}")
        print(f"LIVE PRICES: {ticker}")
        print(f"{'='*60}")

        # Extract orderbook data
        yes_bid = market.yes_bid if hasattr(market, 'yes_bid') else None
        yes_ask = market.yes_ask if hasattr(market, 'yes_ask') else None
        no_bid = market.no_bid if hasattr(market, 'no_bid') else None
        no_ask = market.no_ask if hasattr(market, 'no_ask') else None

        print(f"\nYES Market:")
        print(f"  Best Bid: {yes_bid}¢" if yes_bid else "  Best Bid: N/A")
        print(f"  Best Ask: {yes_ask}¢" if yes_ask else "  Best Ask: N/A")

        print(f"\nNO Market:")
        print(f"  Best Bid: {no_bid}¢" if no_bid else "  Best Bid: N/A")
        print(f"  Best Ask: {no_ask}¢" if no_ask else "  Best Ask: N/A")

        # Calculate implied prices
        if yes_bid and no_bid:
            implied_yes_ask = 100 - no_bid
            implied_no_ask = 100 - yes_bid
            print(f"\nImplied Prices:")
            print(f"  Implied YES Ask: {implied_yes_ask}¢ (100 - NO bid)")
            print(f"  Implied NO Ask: {implied_no_ask}¢ (100 - YES bid)")

        print(f"\n{'='*60}\n")

        return {
            'yes_bid': yes_bid,
            'yes_ask': yes_ask,
            'no_bid': no_bid,
            'no_ask': no_ask
        }

    except Exception as e:
        print(f"Error fetching prices: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "KXBTC-26JAN2405-B89375"
    fetch_live_prices(ticker)

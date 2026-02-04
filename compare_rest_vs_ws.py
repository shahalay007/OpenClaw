#!/usr/bin/env python3
"""
Compare REST API market data vs what WebSocket might show
"""

import sys
import json
from kalshi_sdk import get_client

def compare_data_sources(ticker):
    """Compare REST API market response structure"""
    try:
        client = get_client()

        # Get full market data
        print(f"\n{'='*60}")
        print(f"REST API RESPONSE FOR: {ticker}")
        print(f"{'='*60}\n")

        market = client.get_market(ticker)

        # Check if there's separate orderbook data
        print("Market attributes that contain 'bid' or 'ask':")
        for key in dir(market):
            if not key.startswith('_') and ('bid' in key.lower() or 'ask' in key.lower()):
                value = getattr(market, key, None)
                print(f"  {key}: {value}")

        print(f"\n{'='*60}")
        print("CHECKING ORDERBOOK ENDPOINT")
        print(f"{'='*60}\n")

        # Try to get orderbook directly
        try:
            # The REST API might have a separate orderbook endpoint
            import requests
            from kalshi_sdk import API_KEY_ID, PRIVATE_KEY_PEM
            import base64
            import time
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding

            # Sign request
            timestamp = str(int(time.time() * 1000))
            message = timestamp + "GET" + f"/trade-api/v2/markets/{ticker}/orderbook"

            private_key = serialization.load_pem_private_key(
                PRIVATE_KEY_PEM.encode(),
                password=None
            )
            signature = private_key.sign(
                message.encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
            sig_b64 = base64.b64encode(signature).decode()

            headers = {
                "KALSHI-ACCESS-KEY": API_KEY_ID,
                "KALSHI-ACCESS-SIGNATURE": sig_b64,
                "KALSHI-ACCESS-TIMESTAMP": timestamp
            }

            response = requests.get(
                f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}/orderbook",
                headers=headers
            )

            if response.status_code == 200:
                data = response.json()
                print(f"Orderbook data:\n{json.dumps(data, indent=2)}")
            else:
                print(f"Orderbook endpoint returned: {response.status_code}")
                print(f"Response: {response.text}")

        except Exception as e:
            print(f"Error fetching orderbook: {e}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "KXBTC-26JAN2405-B89375"
    compare_data_sources(ticker)

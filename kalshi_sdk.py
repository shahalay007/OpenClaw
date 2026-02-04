#!/usr/bin/env python3
"""
Kalshi SDK Setup
Configure and initialize the Kalshi client
"""

import os
import certifi
from kalshi_python_sync import Configuration, KalshiClient

# API Credentials (from environment only)
API_KEY_ID = (os.getenv("KALSHI_API_KEY_ID") or "").strip()
PRIVATE_KEY_PEM = (os.getenv("KALSHI_PRIVATE_KEY_PEM") or "").strip()

# Ensure SSL certs are available for urllib3/requests on macOS
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())


def get_client():
    """
    Initialize and return a configured Kalshi client

    Returns:
        KalshiClient: Configured Kalshi API client
    """
    config = Configuration(
        host="https://api.elections.kalshi.com/trade-api/v2"
    )

    if not API_KEY_ID or not PRIVATE_KEY_PEM:
        raise ValueError("Missing KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY_PEM in environment")

    config.api_key_id = API_KEY_ID
    config.private_key_pem = PRIVATE_KEY_PEM

    return KalshiClient(config)


def test_connection():
    """Test the Kalshi SDK connection"""
    try:
        client = get_client()
        status = client.get_exchange_status()
        print("✓ Connected to Kalshi API")
        print(f"  Exchange Active: {status.exchange_active}")
        print(f"  Trading Active: {status.trading_active}")
        return True
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("KALSHI SDK CONNECTION TEST")
    print("=" * 60)
    test_connection()

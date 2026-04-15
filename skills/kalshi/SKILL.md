---
name: kalshi
description: Read-only Kalshi prediction market integration. Use for viewing markets, checking portfolio positions, analyzing prediction opportunities, and finding high-payoff/high-certainty trades. Triggers on Kalshi, prediction markets, event contracts, or trading recommendations.
---

# Kalshi Prediction Markets

Read-only integration with Kalshi's prediction market API.

## Capabilities

- **Browse markets**: List active events and markets by category
- **Market analysis**: Get prices, volumes, orderbook depth
- **Portfolio view**: Check positions and P&L (requires API key)
- **Trade recommendations**: Find high-certainty, high-payoff opportunities

## Setup

This repo's OpenClaw setup can run fully from vendored Python dependencies bundled with this skill.

For portfolio access (RSA key signing required):

1. Go to [kalshi.com/account/profile](https://kalshi.com/account/profile)
2. Create new API key → save the **Key ID** and download the **private key**
3. Provide credentials using either env vars or a credentials file.

Preferred for this OpenClaw setup:

```bash
export KALSHI_API_KEY_ID="your-key-id-here"
export KALSHI_PRIVATE_KEY_PATH="/data/.openclaw/credentials/kalshi_private_key.pem"
```

Alternative credentials file:

```bash
mkdir -p ~/.kalshi
mv ~/Downloads/your-key-file.txt ~/.kalshi/private_key.pem
chmod 600 ~/.kalshi/private_key.pem
```

4. Create `~/.kalshi/credentials.json`:
```json
{
  "api_key_id": "your-key-id-here",
  "private_key_path": "~/.kalshi/private_key.pem"
}
```

Or run interactive setup:
```bash
python scripts/kalshi_portfolio.py setup
```

The portfolio script resolves credentials in this order:
1. `KALSHI_API_KEY_ID` + `KALSHI_PRIVATE_KEY_PATH`
2. `KALSHI_API_KEY_ID` + `KALSHI_PRIVATE_KEY_PEM`
3. `KALSHI_CREDENTIALS_PATH`
4. `~/.kalshi/credentials.json`

## Scripts

### Market Data (No Auth Required)

```bash
# List trending markets
python scripts/kalshi_markets.py trending

# Search markets by query
python scripts/kalshi_markets.py search "bitcoin"

# Get specific market details
python scripts/kalshi_markets.py market TICKER

# Find high-value opportunities
python scripts/kalshi_markets.py opportunities
```

### Portfolio (Auth Required)

```bash
# View positions
python scripts/kalshi_portfolio.py positions

# View balance
python scripts/kalshi_portfolio.py balance

# Trade history
python scripts/kalshi_portfolio.py history
```

## Opportunity Analysis

The `opportunities` command identifies markets where:
- **High certainty**: Price ≥85¢ YES or ≤15¢ YES (implies 85%+ confidence)
- **Meaningful payoff**: Potential return ≥10% on capital
- **Sufficient liquidity**: Orderbook depth supports reasonable position size

Formula: `expected_value = probability * payoff - (1 - probability) * cost`

A good opportunity has: `EV / cost > 0.1` (10%+ expected return)

## Categories

Kalshi markets span:
- Politics & Elections
- Economics (Fed rates, inflation, GDP)
- Weather & Climate
- Finance (stock prices, crypto)
- Entertainment & Sports
- Science & Tech

## API Reference

See `references/api.md` for endpoint details.

## Important Notes

- This skill is READ-ONLY — no trade execution
- Public endpoints don't require authentication
- Portfolio/balance requires API credentials
- Markets settle in cents (100¢ = $1)
- All times in UTC

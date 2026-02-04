# Kalshi Event Logging Pipeline

## Complete Pipeline: Fetch Active Event → Log Live Bid/Ask for All Markets

This pipeline automatically:
1. Fetches the currently active KXBTC event
2. Gets all markets in that event
3. Starts live bid/ask logging for each market using the fixed WebSocket logger

---

## Quick Start

### Log a Specific Market (e.g., $89,250-$89,499.99)
```bash
python3 start_event_logging.py --single "89,250"
```

### Log ALL Markets in Active Event
```bash
python3 start_event_logging.py
```

### Log Limited Number of Markets
```bash
python3 start_event_logging.py --limit 10
```

---

## What Gets Created

```
logs_kxbtc-26jan2406/
├── kxbtc-26jan2406-b89375.log  (market for $89,250-$89,499.99)
├── kxbtc-26jan2406-b89625.log  (market for $89,500-$89,749.99)
├── kxbtc-26jan2406-b89875.log  (market for $89,750-$89,999.99)
└── ... (one log file per market)
```

---

## Log Format

Each line contains:
```
2026-01-24 05:30:03.200 KXBTC-26JAN2406-B89375 yes_buy=61.0¢ yes_sell=66.0¢ no_buy=34.0¢ no_sell=39.0¢
```

Fields:
- **Timestamp** (EST with milliseconds)
- **Market Ticker**
- **yes_buy**: Best YES bid (highest price to buy YES)
- **yes_sell**: Best YES ask (lowest price to sell YES, implied from 100-NO bid)
- **no_buy**: Best NO bid (highest price to buy NO)
- **no_sell**: Best NO ask (lowest price to sell NO, implied from 100-YES bid)

---

## View Live Updates

### Watch specific market
```bash
tail -f logs_kxbtc-26jan2406/kxbtc-26jan2406-b89375.log
```

### Watch all markets (multiple terminals)
```bash
# Terminal 1
tail -f logs_kxbtc-26jan2406/kxbtc-26jan2406-b89375.log

# Terminal 2
tail -f logs_kxbtc-26jan2406/kxbtc-26jan2406-b89625.log

# ... etc
```

---

## Stop Loggers

```bash
pkill -f 'log_one_ticker.py'
```

---

## Pipeline Components

### 1. `fetch_active_event_simple.py`
- Fetches currently active event for a series
- Returns event data with all market tickers

### 2. `log_one_ticker.py` (FIXED VERSION)
- WebSocket logger using `delta` field (not `quantity`)
- Maintains live orderbook state
- Logs bid/ask changes in real-time

### 3. `start_event_logging.py` (NEW)
- Complete pipeline orchestrator
- Launches individual loggers for each market
- Organizes logs by event

---

## Example Usage

### Example 1: Log Current $89,250-$89,499.99 Market
```bash
python3 start_event_logging.py --single "89,250"
```

**Output:**
```
✓ Active Event: KXBTC-26JAN2406
  Title: Bitcoin price range on Jan 24, 2026 at 6am EST?
  Total Markets: 75

✓ Found market: KXBTC-26JAN2406-B89375
  Range: $89,250 to 89,499.99

Logger Status:
KXBTC-26JAN2406-B89375    ✓ Running       15 entries | Latest: 05:30:03.200

Loggers are running in the background.
Log files: logs_kxbtc-26jan2406/
```

### Example 2: Log All Markets
```bash
python3 start_event_logging.py
```

**Output:**
```
✓ Active Event: KXBTC-26JAN2406
  Total Markets: 75

✓ Selected 75 markets to log

Starting loggers...
  [1/75] Started logger for KXBTC-26JAN2406-T97999.99
  [2/75] Started logger for KXBTC-26JAN2406-T79750
  [3/75] Started logger for KXBTC-26JAN2406-B97875
  ...
  [75/75] Started logger for KXBTC-26JAN2406-B80250

✓ All 75 loggers started
  Log directory: logs_kxbtc-26jan2406/
```

---

## Files

| File | Purpose |
|------|---------|
| `fetch_active_event_simple.py` | Fetch active event from series |
| `log_one_ticker.py` | WebSocket logger (fixed version) |
| `start_event_logging.py` | Complete pipeline orchestrator |
| `current_active_event.json` | Cached event data |
| `logs_<event>/` | Log directory for each event |

---

## Key Fix

The logger now correctly uses the **`delta`** field from Kalshi's WebSocket API:

```python
# OLD (broken):
qty = msg.get("quantity")  # Always None!

# NEW (fixed):
delta = msg.get("delta")   # Actual field from Kalshi
new_qty = current_qty + delta
```

This allows proper orderbook maintenance:
- Positive delta → ADD/UPDATE order
- Negative delta → REDUCE/REMOVE order
- Zero quantity → REMOVE level

---

## Monitoring Tips

### Count total log entries across all markets
```bash
wc -l logs_kxbtc-26jan2406/*.log
```

### Find markets with most activity
```bash
wc -l logs_kxbtc-26jan2406/*.log | sort -rn | head -10
```

### Check logger health
```bash
ps aux | grep "log_one_ticker" | wc -l
```

---

## Troubleshooting

### No data logging?
- Check if event is still active
- Verify loggers are running: `ps aux | grep log_one_ticker`
- Check for errors: `tail -f log_one_ticker.out`

### Too many markets?
- Use `--limit` to log fewer markets
- Focus on specific ranges with `--single`

### Log files too large?
- Loggers only write when prices change
- Use log rotation or periodic cleanup

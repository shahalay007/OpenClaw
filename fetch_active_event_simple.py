#!/usr/bin/env python3
"""
Fetch the currently active/traded event from Kalshi for a given series (e.g., KXBTC)
Uses the event ticker time pattern to identify the active event
"""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from kalshi_sdk import get_client


def parse_event_time(event_ticker):
    """
    Parse the event close time from the event ticker
    Example: KXBTC-26JAN2321 -> Jan 23, 2026 at 9PM (21:00)
    """
    try:
        parts = event_ticker.split('-')
        if len(parts) < 2:
            return None

        date_time_part = parts[1]  # e.g., "26JAN2321"

        # Extract components
        year = int('20' + date_time_part[:2])  # "26" -> 2026
        month_str = date_time_part[2:5]  # "JAN"
        day = int(date_time_part[5:7])  # "23"
        hour = int(date_time_part[7:9])  # "21" (9 PM)
        minute = int(date_time_part[9:11]) if len(date_time_part) >= 11 else 0

        # Month mapping
        months = {
            'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4,
            'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8,
            'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
        }

        month = months.get(month_str)
        if not month:
            return None

        # Create datetime in EST
        event_time = datetime(year, month, day, hour, minute, 0, tzinfo=ZoneInfo("America/New_York"))
        return event_time

    except Exception:
        return None


def get_active_event_for_series(series_ticker="KXBTC"):
    """
    Get the currently active event for a given series ticker

    Args:
        series_ticker: The series ticker to search for (e.g., KXBTC)

    Returns:
        dict: Event data with event_ticker, title, and list of market tickers
    """
    client = get_client()

    # Get current time in EST
    now = datetime.now(ZoneInfo("America/New_York"))

    print(f"\n{'=' * 80}")
    print(f"FETCHING ACTIVE EVENT FOR SERIES: {series_ticker}")
    print(f"Current time (EST): {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print('=' * 80)

    # Fetch events for the series
    print(f"\nFetching events for series {series_ticker}...")
    events_response = client.get_events(
        series_ticker=series_ticker,
        status="open"
    )

    if not events_response or not hasattr(events_response, 'events'):
        print(f"No events found for series {series_ticker}")
        return None

    events = events_response.events
    print(f"✓ Found {len(events)} open events")

    # Find events that are currently active
    active_candidates = []
    open_candidates = []

    for event in events:
        event_ticker = event.event_ticker if hasattr(event, 'event_ticker') else None
        if not event_ticker:
            continue

        open_time = None
        close_time = None
        event_time = None

        # Prefer API-provided times if available
        if hasattr(event, "open_time") and hasattr(event, "close_time"):
            try:
                open_time = event.open_time
                close_time = event.close_time
            except Exception:
                open_time = None
                close_time = None

        # Fallback to parsing from ticker if API times missing
        if open_time is None or close_time is None:
            event_time = parse_event_time(event_ticker)
            if not event_time:
                continue
            # Events open 1 hour before and close at the event time
            open_time = event_time - timedelta(hours=1)
            close_time = event_time
        else:
            event_time = close_time

        print(f"\nEvent: {event_ticker}")
        print(f"  Opens:  {open_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"  Closes: {close_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"  Now:    {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")

        # Check if currently active
        if open_time <= now <= close_time:
            print(f"  Status: ACTIVE ✓")
            active_candidates.append((event, event_ticker, event_time))
        elif hasattr(event, "status") and str(getattr(event, "status", "")).lower() == "open":
            open_candidates.append((event, event_ticker, event_time))
        elif now < open_time:
            print(f"  Status: Not yet open (opens in {(open_time - now).total_seconds() / 60:.1f} minutes)")
        else:
            print(f"  Status: Closed")

    if not active_candidates:
        if open_candidates:
            # Fallback: choose the earliest "open" event if time bounds look off
            open_candidates.sort(key=lambda x: x[2])
            event, event_ticker, _ = open_candidates[0]
            print(f"\n⚠ No time-active event found; using open event: {event_ticker}")
            active_candidates.append((event, event_ticker, open_candidates[0][2]))
        elif events:
            # Fallback: use the first event returned from API
            event = events[0]
            event_ticker = event.event_ticker if hasattr(event, 'event_ticker') else None
            if event_ticker:
                print(f"\n⚠ No time-active event found; using first event: {event_ticker}")
                active_candidates.append((event, event_ticker, parse_event_time(event_ticker) or now))
            else:
                print(f"\n⚠ No currently active event found for series {series_ticker}")
                return None
        else:
            print(f"\n⚠ No currently active event found for series {series_ticker}")
            return None

    # Use the earliest active event (closest to expiry)
    active_candidates.sort(key=lambda x: x[2])
    event, event_ticker, _ = active_candidates[0]

    # Get markets for this event
    print(f"\n✓ Selected active event: {event_ticker}")
    print(f"  Fetching markets...")

    markets_response = client.get_markets(
        event_ticker=event_ticker,
        status="open"
    )

    markets = []
    if markets_response and hasattr(markets_response, 'markets'):
        for market in markets_response.markets:
            markets.append({
                'ticker': market.ticker,
                'title': market.title if hasattr(market, 'title') else '',
                'subtitle': market.subtitle if hasattr(market, 'subtitle') else ''
            })

    active_event = {
        'event_ticker': event_ticker,
        'title': event.title if hasattr(event, 'title') else '',
        'sub_title': event.sub_title if hasattr(event, 'sub_title') else '',
        'markets': markets
    }

    print(f"  Markets: {len(markets)}")

    return active_event


def save_active_event(series_ticker="KXBTC", output_file="current_active_event.json"):
    """
    Fetch and save the currently active event to a JSON file

    Args:
        series_ticker: The series ticker to search for
        output_file: Output filename
    """
    event = get_active_event_for_series(series_ticker)

    if not event:
        print(f"\nNo active event to save")
        return None

    # Add metadata
    output = {
        'as_of_est': datetime.now(ZoneInfo("America/New_York")).strftime('%Y-%m-%d %H:%M:%S %Z'),
        'series': {
            series_ticker: [event]
        }
    }

    # Save to file
    print(f"\nSaving to {output_file}...")
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)

    import os
    file_size = os.path.getsize(output_file) / 1024
    print(f"✓ Saved to {output_file} ({file_size:.1f}KB)")

    return event


if __name__ == "__main__":
    # Fetch and save the active KXBTC event
    event = save_active_event("KXBTC", "current_active_event.json")

    if event:
        print(f"\n{'=' * 80}")
        print("ACTIVE EVENT SUMMARY")
        print('=' * 80)
        print(f"Event: {event['event_ticker']}")
        print(f"Title: {event['title']}")
        print(f"\nMarkets ({len(event['markets'])}):")
        for i, market in enumerate(event['markets'][:10], 1):
            print(f"  {i}. {market['ticker']}")
            print(f"     {market['subtitle']}")
        if len(event['markets']) > 10:
            print(f"  ... and {len(event['markets']) - 10} more markets")
        print('=' * 80)

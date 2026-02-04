#!/usr/bin/env python3
from fetch_active_event_simple import get_active_event_for_series
from kalshi_sdk import get_client
import json

# Get the currently active event (same as the script uses)
event = get_active_event_for_series('KXBTCD')

if event:
    event_ticker = event['event_ticker']
    print(f'Currently Active Event: {event_ticker}')
    print(f'Title: {event["title"]}')
    print(f'Markets: {len(event["markets"])} markets')
    print()

    # Get full event details with all metadata
    client = get_client()
    event_response = client.get_event(event_ticker=event_ticker, with_nested_markets=True)

    event_dict = event_response.event.__dict__ if hasattr(event_response.event, '__dict__') else {}

    print('=' * 80)
    print('EVENT METADATA')
    print('=' * 80)

    for key, value in sorted(event_dict.items()):
        if key == 'markets':
            print(f'{key}: [{len(value)} markets]')
        elif key == 'product_metadata' and value:
            print(f'{key}:')
            if hasattr(value, '__dict__'):
                for k, v in value.__dict__.items():
                    print(f'  {k}: {v}')
            else:
                print(f'  {value}')
        else:
            print(f'{key}: {value}')

    print()
    print('=' * 80)
    print('MARKET METADATA - ALL FIELDS (First Market as Example)')
    print('=' * 80)

    if hasattr(event_response.event, 'markets') and event_response.event.markets:
        market = event_response.event.markets[0]
        market_dict = market.__dict__ if hasattr(market, '__dict__') else {}

        for key, value in sorted(market_dict.items()):
            print(f'{key}: {value}')

        print()
        print('=' * 80)
        print('KEY MARKET INFO FOR ALL MARKETS')
        print('=' * 80)

        for i, m in enumerate(event_response.event.markets[:10], 1):
            print(f'{i}. {m.ticker}')
            print(f'   Strike: ${m.floor_strike:,.2f} | Type: {m.strike_type}')
            print(f'   YES: bid={m.yes_bid}¢ ask={m.yes_ask}¢ | NO: bid={m.no_bid}¢ ask={m.no_ask}¢')
            print(f'   Liquidity: ${m.liquidity_dollars:,.2f} | Open Interest: {m.open_interest_fp}')
            print(f'   Status: {m.status} | Settlement Timer: {m.settlement_timer_seconds}s')
            print()

        if len(event_response.event.markets) > 10:
            print(f'... and {len(event_response.event.markets) - 10} more markets')
else:
    print('No active event found')

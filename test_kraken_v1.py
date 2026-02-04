import asyncio
import json
import ssl
import websockets
from datetime import datetime

try:
    import certifi
    CERT_PATH = certifi.where()
except ImportError:
    CERT_PATH = None

def get_ssl_context():
    ssl_context = ssl.create_default_context()
    if CERT_PATH:
        ssl_context.load_verify_locations(CERT_PATH)
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    return ssl_context

async def test_kraken_v1():
    uri = 'wss://ws.kraken.com'

    print('Testing Kraken v1 API...')

    async with websockets.connect(uri, ssl=get_ssl_context()) as websocket:
        payload = {
            'event': 'subscribe',
            'pair': ['XBT/USD'],
            'subscription': {'name': 'ticker'}
        }
        await websocket.send(json.dumps(payload))

        count = 0
        last_bid = None
        last_ask = None

        async for message in websocket:
            data = json.loads(message)

            if isinstance(data, list):
                ticker_data = data[1]

                if 'b' in ticker_data and 'a' in ticker_data:
                    best_bid = float(ticker_data['b'][0])
                    best_ask = float(ticker_data['a'][0])

                    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                    changed = best_bid != last_bid or best_ask != last_ask

                    print(f'{timestamp} | Bid: {best_bid:.2f} Ask: {best_ask:.2f} | Changed: {changed}')

                    last_bid = best_bid
                    last_ask = best_ask
                    count += 1

                    if count >= 20:
                        break

asyncio.run(test_kraken_v1())

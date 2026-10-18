import requests

import os
token = os.environ.get('WEB_AUTH_TOKEN', '')
headers = {'X-Auth-Token': token}

# Test the rates API
r = requests.get('http://127.0.0.1:8051/api/rates?symbol=EURUSD&timeframe=H1&count=50', headers=headers, timeout=10)
print(f'Rates Status: {r.status_code}')
if r.status_code == 200:
    data = r.json()
    print(f'Rates keys: {list(data.keys())}')
    print(f'Close count: {len(data.get("close", []))}')
    print(f'Times count: {len(data.get("times", []))}')
    if data.get('close'):
        print(f'First close: {data["close"][0]}')
        print(f'Last close: {data["close"][-1]}')
    if data.get('ema50'):
        print(f'EMA50 count: {len(data["ema50"])}')
        print(f'First EMA50: {data["ema50"][0]}')
    if data.get('ema200'):
        print(f'EMA200 count: {len(data["ema200"])}')
    if data.get('rsi'):
        print(f'RSI count: {len(data["rsi"])}')
    if data.get('macd'):
        print(f'MACD count: {len(data["macd"])}')
    if data.get('tick'):
        print(f'Tick: {data["tick"]}')
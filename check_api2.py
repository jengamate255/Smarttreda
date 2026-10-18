import requests

base = 'http://127.0.0.1:8050'
import os
token = os.environ.get('WEB_AUTH_TOKEN', '')
headers = {'X-Auth-Token': token}

endpoints = ['/api/rates?symbol=EURUSD&timeframe=H1&count=100', '/api/bot-analysis?symbol=EURUSD', '/api/live-overlay?symbol=EURUSD']

for ep in endpoints:
    try:
        r = requests.get(base + ep, headers=headers, timeout=5)
        print(f'{ep}: {r.status_code} - {len(r.text)} chars')
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                print(f'  Keys: {list(data.keys())}')
            elif isinstance(data, list):
                print(f'  List length: {len(data)}')
                if data:
                    print(f'  First item keys: {list(data[0].keys()) if isinstance(data[0], dict) else "N/A"}')
    except Exception as e:
        print(f'{ep}: ERROR - {e}')
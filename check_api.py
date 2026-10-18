import requests

base = 'http://127.0.0.1:8050'
import os
token = os.environ.get('WEB_AUTH_TOKEN', '')
headers = {'X-Auth-Token': token}

endpoints = ['/api/health', '/api/account', '/api/positions', '/api/symbols', '/api/chart?symbol=EURUSD&timeframe=H1&count=100']

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
    except Exception as e:
        print(f'{ep}: ERROR - {e}')
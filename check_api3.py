import requests

base = 'http://127.0.0.1:8050'
import os
token = os.environ.get('WEB_AUTH_TOKEN', '')
headers = {'X-Auth-Token': token}

for ep in ['/api/rates?symbol=EURUSD&timeframe=H1&count=100', '/api/bot-analysis?symbol=EURUSD', '/api/live-overlay?symbol=EURUSD']:
    try:
        r = requests.get(base + ep, headers=headers, timeout=5)
        print(f'{ep}: {r.status_code}')
        print(r.text)
    except Exception as e:
        print(f'{ep}: ERROR - {e}')
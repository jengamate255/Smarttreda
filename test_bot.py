import requests

import os
token = os.environ.get('WEB_AUTH_TOKEN', '')
headers = {'X-Auth-Token': token}

r = requests.get('http://127.0.0.1:8051/api/bot-analysis?symbol=EURUSD&timeframe=H1&count=50', headers=headers, timeout=10)
print('Status:', r.status_code)
if r.status_code == 200:
    data = r.json()
    print('Keys:', list(data.keys()))
    print('Trend:', data.get('trend'))
    print('Zones count:', len(data.get('zones', [])))
    print('Patterns count:', len(data.get('patterns', [])))
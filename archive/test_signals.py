#!/usr/bin/env python
import sys
sys.path.insert(0, r'E:\Project\Mt5 Ea')

from web_dashboard import app
from fastapi.testclient import TestClient

client = TestClient(app)

# Test the signals endpoint
res = client.get("/api/signals?symbol=BOOM150&timeframe=H1&count=100")
print(f"Status: {res.status_code}")
if res.status_code == 200:
    data = res.json()
    print(f"Symbol: {data.get('symbol')}")
    print(f"Enabled: {data.get('enabled')}")
    print(f"Number of signals: {len(data.get('signals', []))}")
    print(f"Confidence floor: {data.get('confidence_floor')}")
    print(f"Size multiplier: {data.get('size_multiplier')}")
    if data.get('signals'):
        print(f"First signal: {data['signals'][0]}")
else:
    print(f"Error: {res.text[:200]}")
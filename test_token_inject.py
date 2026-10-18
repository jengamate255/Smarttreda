import requests
import re

r = requests.get('http://127.0.0.1:8051/', timeout=10)
match = re.search(r'auth-token" content="([^"]*)"', r.text)
if match:
    print(f'Token in HTML: {match.group(1)[:20]}...')
else:
    print('No token found in HTML')
    print('First 500 chars:', r.text[:500])
import requests
import re

base = 'http://127.0.0.1:8050'
r = requests.get(base)
match = re.search(r'auth-token" content="([^"]*)"', r.text)
if match:
    token = match.group(1)
    print(f'Token from HTML: "{token}"')
else:
    print('No token found in HTML')
    print('First 500 chars:', r.text[:500])
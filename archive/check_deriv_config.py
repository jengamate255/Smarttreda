import sys
sys.path.insert(0, '.')

from mt5_mcp.config import get_settings

s = get_settings()
print(f'Deriv enabled: {s.deriv.enabled}')
print(f'Deriv ws_url: {s.deriv.ws_url}')
print(f'Deriv app_id: {s.deriv.app_id}')
print(f'Deriv api_token: {"SET" if s.deriv.api_token else "NOT SET"}')
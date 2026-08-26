import sys
sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig

config = DerivConfig(enabled=True, ws_url='wss://ws.derivws.com/websockets/v3', app_id=1089, api_token='')
client = DerivClient(config)
if client.connect():
    print('Connected OK')
    result = client.ticks_history('BOOM900', count=10, style='candles', granularity=60)
    if result and 'candles' in result and len(result['candles']) > 0:
        print(f'BOOM900: {len(result["candles"])} candles')
    else:
        print('No data')
else:
    print(f'Failed: {client.last_error()}')
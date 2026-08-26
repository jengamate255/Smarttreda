import sys
sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig

# Try Deriv WebSocket API without token (public data only)
config = DerivConfig(
    enabled=True,
    ws_url="wss://ws.derivws.com/websockets/v3",
    app_id=1089,
    api_token="",  # Empty for public access
)

client = DerivClient(config)
if client.connect():
    print('Connected to Deriv WebSocket API')
    
    # Get active symbols
    try:
        result = client.active_symbols("basic")
        print(f'Active symbols result keys: {result.keys() if isinstance(result, dict) else "not dict"}')
        if isinstance(result, dict) and 'active_symbols' in result:
            symbols = result['active_symbols']
            print(f'Total active symbols: {len(symbols)}')
            
            # Filter for Boom/Crash
            boom_crash = [s for s in symbols if 'BOOM' in s.get('symbol', '').upper() or 'CRASH' in s.get('symbol', '').upper()]
            print(f'Boom/Crash symbols: {len(boom_crash)}')
            for s in boom_crash[:10]:
                print(f'  {s}')
        else:
            print(f'Full result: {result}')
    except Exception as e:
        print(f'Error getting active symbols: {e}')
    
    # Try getting ticks history for a known symbol
    try:
        result = client.ticks_history("R_50", count=10, style="candles", granularity=60)
        print(f'Ticks history result keys: {result.keys() if isinstance(result, dict) else "not dict"}')
        if isinstance(result, dict) and 'candles' in result:
            print(f'Got {len(result["candles"])} candles for R_50')
            for c in result['candles'][:3]:
                print(f'  {c}')
    except Exception as e:
        print(f'Error getting ticks history: {e}')
else:
    print(f'Failed to connect: {client.last_error()}')
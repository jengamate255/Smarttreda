import sys
sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig

# NEW API TOKEN PAT_E7F8837ADD7ED919F12C8F6EF71BD901A75F752AA60DCD2F3A67B26B40415894
config = DerivConfig(
    enabled=True,
    ws_url="wss://ws.derivws.com/websockets/v3",
    app_id=1089,
    api_token="REDACTED_DERIV_PAT",
)

client = DerivClient(config)
if client.connect():
    print('=== CONNECTED WITH API TOKEN ===')
    print(f'Deriv API Token: {config.api_token[:20]}...')
    
    # Test getting active symbols
    try:
        result = client.active_symbols("basic")
        if isinstance(result, dict) and 'active_symbols' in result:
            symbols = result['active_symbols']
            boom_crash = [s for s in symbols if 'BOOM' in s.get('symbol', '').upper() or 'CRASH' in s.get('symbol', '').upper()]
            print(f'Total active symbols: {len(symbols)}')
            print(f'BOOM/CRASH symbols: {len(boom_crash)}')
            for s in boom_crash[:15]:
                print(f'  {s.get("symbol")}: {s.get("display_name")} - pip: {s.get("pip")}')
        else:
            print(f'active_symbols result: {result}')
    except Exception as e:
        print(f'Error getting symbols: {e}')
    
    # Test ticks_history for BOOM100
    try:
        result = client.ticks_history('BOOM100', count=100, style='candles', granularity=60)
        if result and 'candles' in result and len(result['candles']) > 0:
            print(f'\\nBOOM100 data: {len(result["candles"])} candles')
            c = result['candles'][-1]
            print(f'Last close: {c["close"]}, time: {c["epoch"]}')
        else:
            print('No BOOM100 data')
    except Exception as e:
        print(f'Error BOOM100: {e}')
    
    # Test R_75 with the token
    try:
        result = client.ticks_history('R_75', count=10, style='candles', granularity=60)
        if result and 'candles' in result and len(result['candles']) > 0:
            print(f'\\nR_75 data: {len(result["candles"])} candles')
            c = result['candles'][-1]
            print(f'Last close: {c["close"]}, time: {c["epoch"]}')
        else:
            print('No R_75 data')
    except Exception as e:
        print(f'Error R_75: {e}')
else:
    print(f'Failed to connect: {client.last_error()}')
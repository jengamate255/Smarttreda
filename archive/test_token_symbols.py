import sys
sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig

config = DerivConfig(
    enabled=True,
    ws_url="wss://ws.derivws.com/websockets/v3",
    app_id=1089,
    api_token="REDACTED_DERIV_PAT",
)

client = DerivClient(config)
if client.connect():
    print('=== CONNECTED WITH API TOKEN ===\n')
    
    # Try different product types
    for product_type in ["basic", "financial", "synthetic", "turbo", "boom_crash"]:
        try:
            result = client.active_symbols(product_type)
            if isinstance(result, dict) and 'active_symbols' in result:
                symbols = result['active_symbols']
                boom_crash = [s for s in symbols if 'BOOM' in s.get('symbol', '').upper() or 'CRASH' in s.get('symbol', '').upper()]
                print(f'product_type="{product_type}": {len(symbols)} total symbols, {len(boom_crash)} BOOM/CRASH')
                if boom_crash:
                    for s in boom_crash[:10]:
                        print(f'  {s.get("symbol")}: {s.get("display_name")} - type: {s.get("type")}')
            else:
                print(f'product_type="{product_type}": no active_symbols key, result={str(result)[:100]}')
        except Exception as e:
            print(f'product_type="{product_type}": Error - {type(e).__name__}')
    
    print()
    
    # Test specific symbols we care about
    test_symbols = ['BOOM100', 'BOOM900', 'CRASH100', 'CRASH900', 'R_75', 'R_100', '1HZ10V']
    for sym in test_symbols:
        try:
            result = client.ticks_history(sym, count=5, style='candles', granularity=60)
            if result and 'candles' in result and len(result['candles']) > 0:
                c = result['candles'][-1]
                print(f'{sym}: Last close={c["close"]}, candles={len(result["candles"])}')
            else:
                print(f'{symbol}: No data')
        except Exception as e:
            print(f'{sym}: Error - {type(e).__name__}')
    
    print()
    
    # Test deriv_candles for BOOM900
    from mt5_mcp.tools.deriv import deriv_candles
    try:
        candles = deriv_candles('BOOM900', count=100, granularity=60)
        print(f'BOOM900 via deriv_candles: {len(candles)} candles')
        if candles:
            c = candles[-1]
            print(f'  Last: time={c["time"]}, open={c["open"]}, high={c["high"]}, low={c["low"]}, close={c["close"]}')
    except Exception as e:
        print(f'BOOM900 deriv_candles error: {e}')
    
    # Test CRASH500
    try:
        candles = deriv_candles('CRASH500', count=100, granularity=60)
        print(f'CRASH500 via deriv_candles: {len(candles)} candles')
        if candles:
            c = candles[-1]
            print(f'  Last: time={c["time"]}, open={c["open"]}, high={c["high"]}, low={c["low"]}, close={c["close"]}')
    except Exception as e:
        print(f'CRASH500 deriv_candles error: {e}')
    
else:
    print(f'Failed to connect: {client.last_error()}')
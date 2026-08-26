import sys
sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig

config = DerivConfig(
    enabled=True,
    ws_url="wss://ws.derivws.com/websockets/v3",
    app_id=1089,
    api_token="",
)

client = DerivClient(config)
if client.connect():
    print('Connected to Deriv WebSocket API')
    
    # Try different product types
    for product_type in ["basic", "financial", "financial_stp", "gaming", "synthetic", "multiplier", "vanilla"]:
        try:
            result = client.active_symbols(product_type)
            if isinstance(result, dict) and 'active_symbols' in result:
                symbols = result['active_symbols']
                boom_crash = [s for s in symbols if 'BOOM' in s.get('symbol', '').upper() or 'CRASH' in s.get('symbol', '').upper() or 'JUMP' in s.get('symbol', '').upper() or 'STEP' in s.get('symbol', '').upper()]
                if boom_crash:
                    print(f'\n=== {product_type.upper()} ===')
                    print(f'Total symbols: {len(symbols)}')
                    print(f'Boom/Crash/Jump/Step: {len(boom_crash)}')
                    for s in boom_crash:
                        print(f'  {s.get("symbol")}: {s.get("display_name")} (pip={s.get("pip")}, lot={s.get("lot")})')
                else:
                    print(f'{product_type}: {len(symbols)} symbols, 0 Boom/Crash')
        except Exception as e:
            print(f'{product_type}: Error - {e}')
    
    # Try getting data for known Boom/Crash symbols
    print('\n=== Testing known Boom/Crash symbols ===')
    test_symbols = ["BOOM1000", "BOOM900", "BOOM600", "BOOM500", "BOOM300", "BOOM200", "BOOM150", "BOOM100", "BOOM99", "BOOM50",
                    "CRASH1000", "CRASH900", "CRASH600", "CRASH500", "CRASH300", "CRASH200", "CRASH150", "CRASH100", "CRASH99", "CRASH50"]
    
    for sym in test_symbols:
        try:
            result = client.ticks_history(sym, count=5, style="candles", granularity=60)
            if isinstance(result, dict) and 'candles' in result and len(result['candles']) > 0:
                c = result['candles'][-1]
                print(f'{sym}: Last close={c["close"]}, time={c["epoch"]}')
        except Exception as e:
            pass  # Symbol might not exist
else:
    print(f'Failed to connect: {client.last_error()}')
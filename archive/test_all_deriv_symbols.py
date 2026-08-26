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
if not client.connect():
    print(f'Failed to connect: {client.last_error()}')
    sys.exit(1)

print('Connected to Deriv WebSocket API\n')

# Test ALL known Deriv synthetic indices
all_symbols = [
    # R-series (volatility indices)
    "R_10", "R_25", "R_50", "R_75", "R_100",
    # 1HZ volatility (1Hz)
    "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",
    # Boom/Crash
    "BOOM1000", "BOOM900", "BOOM600", "BOOM500", "BOOM300", "BOOM200", "BOOM150", "BOOM100", "BOOM99", "BOOM50",
    "CRASH1000", "CRASH900", "CRASH600", "CRASH500", "CRASH300", "CRASH200", "CRASH150", "CRASH100", "CRASH99", "CRASH50",
]

print(f'Testing {len(all_symbols)} symbols:\n')

working = []
for sym in all_symbols:
    try:
        result = client.ticks_history(sym, count=5, style="candles", granularity=60)
        if isinstance(result, dict) and 'candles' in result and len(result['candles']) > 0:
            c = result['candles'][-1]
            print(f'OK {sym}: Last close={c["close"]}, time={c["epoch"]}')
            working.append(sym)
        else:
            print(f'NO {sym}: No data')
    except Exception as e:
        print(f'ERR {sym}: Error - {type(e).__name__}')

print(f'\n=== WORKING SYMBOLS: {len(working)} ===')
for s in working:
    print(f'  {s}')
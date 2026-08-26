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

# Test known volatility pairs: 75 and 100
# These could be BOOM75, CRASH75, BOOM100, CRASH100 or similar
test_symbols = [
    "BOOM75", "CRASH75", "BOOM100", "CRASH100",
    "R_75", "R_100",  # Deriv often uses R_ prefix
]

print(f'Testing {len(test_symbols)} symbols:\n')

for sym in test_symbols:
    try:
        result = client.ticks_history(sym, count=10, style="candles", granularity=60)
        if isinstance(result, dict) and 'candles' in result and len(result['candles']) > 0:
            c = result['candles'][-1]
            print(f'{sym}: Last close={c["close"]}, time={c["epoch"]}, count={len(result["candles"])}')
        else:
            print(f'{sym}: No data or unexpected format')
    except Exception as e:
        print(f'{sym}: Error - {type(e).__name__}: {e}')

# Also try the standard Deriv synthetic indices
print('\n=== Trying standard Deriv synthetic indices ===')
deriv_symbols = ["R_100", "R_75", "R_50", "R_10", "R_1"]
for sym in deriv_symbols:
    try:
        result = client.ticks_history(sym, count=5, style="candles", granularity=60)
        if isinstance(result, dict) and 'candles' in result and len(result['candles']) > 0:
            c = result['candles'][-1]
            print(f'{sym}: Last close={c["close"]}, time={c["epoch"]}, count={len(result["candles"])}')
        else:
            print(f'{sym}: No data')
    except Exception as e:
        print(f'{sym}: Error - {type(e).__name__}')
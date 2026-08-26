import sys
sys.path.insert(0, '.')

from mt5_mcp.tools.deriv import deriv_candles

# Test candles for R_75 and R_100
symbols = ['R_75', 'R_100']

for sym in symbols:
    try:
        candles = deriv_candles(sym, count=200, granularity=60)  # 200 M1 bars
        print(f'{sym}: {len(candles)} candles')
        if candles:
            # Show first and last
            c = candles[0]
            print(f'  First: time={c["time"]}, open={c["open"]}, high={c["high"]}, low={c["low"]}, close={c["close"]}')
            c = candles[-1]
            print(f'  Last: time={c["time"]}, open={c["open"]}, high={c["high"]}, low={c["low"]}, close={c["close"]}')
    except Exception as e:
        print(f'{sym}: Error - {e}')

# Also test BOOM100 and CRASH100 for comparison
print()
for sym in ['BOOM100', 'CRASH100']:
    try:
        candles = deriv_candles(sym, count=200, granularity=60)
        print(f'{sym}: {len(candles)} candles')
        if candles:
            c = candles[0]
            print(f'  First: time={c["time"]}, open={c["open"]}, high={c["high"]}, low={c["low"]}, close={c["close"]}')
            c = candles[-1]
            print(f'  Last: time={c["time"]}, open={c["open"]}, high={c["high"]}, low={c["low"]}, close={c["close"]}')
    except Exception as e:
        print(f'{sym}: Error - {e}')
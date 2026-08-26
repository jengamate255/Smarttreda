import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig
from mt5_mcp.indicators import atr as _atr

config = DerivConfig(enabled=True, ws_url='wss://ws.derivws.com/websockets/v3', app_id=1089, api_token='')
client = DerivClient(config)
client.connect()

FOCUS_SYMBOLS = ["BOOM900", "BOOM1000", "CRASH500"]

print('Testing monitoring loop...\n')

for cycle in range(3):  # Just 3 cycles for test
    start = time.time()
    for symbol in FOCUS_SYMBOLS:
        try:
            result = client.ticks_history(symbol, count=60, style='candles', granularity=60)
            if not isinstance(result, dict) or 'candles' not in result or len(result['candles']) < 30:
                print(f'{symbol}: BELOW_THRESHOLD')
                continue
            
            df = pd.DataFrame(result['candles'])
            df['close'] = df['close'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            
            close = df['close'].iloc[-1]
            high = df['high'].iloc[-1]
            low = df['low'].iloc[-1]
            atr_val = _atr(df, period=14).iloc[-1]
            
            if atr_val <= 0:
                print(f'{symbol}: atr<=0')
                continue
            
            spike_up = symbol.startswith('BOOM')
            
            if spike_up:
                max_h_idx = int(np.argmax(df['high'].astype(float).values))
                spike_h = df['high'].astype(float).values[max_h_idx]
                spike_l = np.min(df['low'].astype(float).values[:max_h_idx + 1]) if max_h_idx > 0 else df['low'].astype(float).values[0]
                direction = "SHORT"
                entry = spike_h - (spike_h - spike_l) * 0.382
                sl = spike_h + atr_val * 2
                tp = entry - (sl - entry) * 1.5
            else:
                min_l_idx = int(np.argmin(df['low'].astype(float).values))
                spike_l = df['low'].astype(float).values[min_l_idx]
                spike_h = np.max(df['high'].astype(float).values[:min_l_idx + 1]) if min_l_idx > 0 else df['high'].astype(float).values[0]
                direction = "LONG"
                entry = spike_l + (spike_h - spike_l) * 0.382
                sl = spike_l - atr_val * 2
                tp = entry + (sl - entry) * 1.5
            
            current_price = close
            age = len(df) - 1 - (max_h_idx if spike_up else min_l_idx)
            
            broken = (spike_up and current_price >= spike_h) or (not spike_up and current_price <= spike_l)
            outside = abs(current_price - entry) > atr_val * 1.5
            
            if broken:
                status = "BROKEN"
                confidence = 0
            elif outside:
                status = "OUTSIDE_BUFFER"
                confidence = 0
            else:
                confidence = 60.0 + (abs(spike_h - spike_l) / atr_val) * 3.0
                confidence = min(confidence, 95.0)
                status = "ACTIONABLE"
            
            print(f'{symbol}: {status} conf={confidence:.1f} age={age} entry={entry:.2f} sl={sl:.2f} tp={tp:.2f} price={current_price:.2f}')
            
        except Exception as e:
            print(f'{symbol}: ERROR {str(e)[:30]}')
    
    elapsed = time.time() - start
    print(f'Cycle {cycle+1} done in {elapsed:.1f}s')
    if cycle < 2:
        time.sleep(5)  # Short pause between cycles
    
print('\nTest complete')
"
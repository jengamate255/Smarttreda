import sys
import time
import json
import datetime
from collections import defaultdict

sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig
from mt5_mcp.indicators import atr as _atr

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

# Focus symbols: BOOM900, BOOM1000, CRASH500 (top performers from backtest)
FOCUS_SYMBOLS = ["BOOM900", "BOOM1000", "CRASH500", "BOOM500", "CRASH200"]

print('=== DERIV SPIKE MONITOR DASHBOARD ===')
print(f'Started: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
print(f'Symbols: {", ".join(FOCUS_SYMBOLS)}')
print(f'{"="*60}')
print(f'{"Symbol":<12} {"Dir":<6} {"Conf":>5} {"Entry":>8} {"SL":>8} {"TP":>8} {"Price":>8} {"Age":>4} {"Status":<12}')
print(f'{"-"*12} {"-"*6} {"-"*5} {"-"*8} {"-"*8} {"-"*8} {"-"*8} {"-"*4} {"-"*12}')

try:
    while True:
        # Check each focus symbol
        for symbol in FOCUS_SYMBOLS:
            try:
                result = client.ticks_history(symbol, count=60, style="candles", granularity=60)
                if not isinstance(result, dict) or 'candles' not in result or len(result['candles']) < 30:
                    print(f'\r{" "*60}', end='')
                    print(f'\r{symbol:<12} {"N/A":<6} {"N/A":>5} {"N/A":>8} {"N/A":>8} {"N/A":>8} {"N/A":>8} {"N/A":>4} {"NO DATA":<12}', end='')
                    continue
                
                candles = result['candles']
                df = __import__('pandas').DataFrame(candles)
                df['time'] = pd.to_datetime(df['epoch'], unit='s', utc=True)
                df = df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close'})
                close = df['close'].astype(float).iloc[-1]
                high = df['high'].astype(float).iloc[-1]
                low = df['low'].astype(float).iloc[-1]
                
                # Simple spike detection
                atr_val = __import__('mt5_mcp.indicators').atr(df, period=14).iloc[-1]
                
                if atr_val > 0 and symbol.startswith("BOOM"):
                    # Short spike
                    highs = df['high'].astype(float).values
                    lows = df['low'].astype(float).values
                    closes = df['close'].astype(float).values
                    max_high_idx = int(__import__('numpy').argmax(highs))
                    spike_high = highs[max_high_idx]
                    spike_low = np.min(lows[:max_high_idx + 1]) if max_high_idx > 0 else lows[0]
                    direction = "SHORT"
                    entry = spike_high - (spike_high - spike_low) * 0.382
                    sl = spike_high + atr_val * 2
                    tp = entry - (sl - entry) * 1.5
                    age = len(df) - 1 - max_high_idx
                    ratio = (spike_high - spike_low) / atr_val
                    status = "ACTIONABLE" if abs(close - entry) <= atr_val * 1.5 else "OUTSIDE_BUFFER"
                    
                elif atr_val > 0 and symbol.startswith("CRASH"):
                    # Long spike
                    min_low_idx = int(__import__('numpy').argmin(lows))
                    spike_low = lows[min_low_idx]
                    spike_high = np.max(highs[:min_low_idx + 1]) if min_low_idx > 0 else highs[0]
                    direction = "LONG"
                    entry = spike_low + (spike_high - spike_low) * 0.382
                    sl = spike_low - atr_val * 2
                    tp = entry + (tp - entry) * 1.5 if 'tp' in dir() else entry + (sl - entry) * 1.5
                    age = len(df) - 1 - min_low_idx
                    ratio = (spike_high - spike_low) / atr_val
                    status = "ACTIONABLE" if abs(close - entry) <= atr_val * 1.5 else "OUTSIDE_BUFFER"
                else:
                    direction = "---"
                    entry = close
                    sl = close
                    tp = close
                    age = 0
                    ratio = 0
                    status = "CHECKING"
                
                # Format and print
                print(f'\r{f"{symbol:<12} {direction:<6} {min(confidence,95):>5.1f} if confidence else "":>5} {entry:>8.2f} {sl:>8.2f} {tp:>8.2f} {close:>8.2f} {age:>4.0f} {status:<12}', end='')
                
            except Exception as e:
                print(f'\r{" "*80}', end='')
                print(f'\r{symbol:<12} {"ERR":<6} {"N/A":>5} {"N/A":>8} {"N/A":>8} {"N/A":>8} {"N/A":>8} {"N/A":>4} {e}<12}', end='')
        
        # Print newline every 30 seconds
        time.sleep(30)
        print()  # New line after each cycle
        
except KeyboardInterrupt:
    print(f'\n\n=== Dashboard stopped at {datetime.datetime.now().strftime("%H:%M:%S")} ===')
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timezone

sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig
from mt5_mcp.indicators import atr, rsi

# All working Deriv BOOM/CRASH symbols
all_symbols = [
    "BOOM1000", "BOOM900", "BOOM600", "BOOM500", "BOOM200", "BOOM100", "BOOM99", "BOOM50",
    "CRASH1000", "CRASH900", "CRASH600", "CRASH500", "CRASH200", "CRASH100", "CRASH99", "CRASH50",
]

# Strategy parameters (from boom_crash_strategy.py)
MIN_SPIKE_ATR = 5.0
RET_LEVELS = (0.382, 0.5)
SL_ATR_BUFFER = 2.0
ENTRY_BUFFER_ATRS = 1.5
DEFAULT_RR = 1.5
LOOKBACK_BARS = 60

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

print('Connected to Deriv WebSocket API')
print(f'Scanning {len(all_symbols)} symbols for spikes...\n')

results = []

for symbol in all_symbols:
    try:
        # Fetch M1 candles (LOOKBACK_BARS)
        result = client.ticks_history(symbol, count=LOOKBACK_BARS, style="candles", granularity=60)
        if not isinstance(result, dict) or 'candles' not in result or len(result['candles']) < 30:
            continue
        
        candles = result['candles']
        df = pd.DataFrame(candles)
        df['time'] = pd.to_datetime(df['epoch'], unit='s', utc=True)
        df = df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close'})
        df = df[['time', 'open', 'high', 'low', 'close']].copy()
        
        # Calculate ATR
        atr_series = atr(df, period=14)
        current_atr = float(atr_series.iloc[-1])
        if current_atr <= 0:
            continue
        
        # Detect spike
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        lookback = len(df)
        
        spike_up_expected = symbol.startswith("BOOM")
        
        if spike_up_expected:
            max_high_idx = int(np.argmax(highs))
            spike_high = highs[max_high_idx]
            spike_low = np.min(lows[:max_high_idx + 1]) if max_high_idx > 0 else lows[0]
            direction = "short"
            drift_direction = "down"
        else:
            min_low_idx = int(np.argmin(lows))
            spike_low = lows[min_low_idx]
            spike_high = np.max(highs[:min_low_idx + 1]) if min_low_idx > 0 else highs[0]
            direction = "long"
            drift_direction = "up"
        
        spike_size = spike_high - spike_low
        if spike_size < MIN_SPIKE_ATR * current_atr:
            # Still record but mark as below threshold
            results.append({
                'symbol': symbol,
                'direction': direction,
                'confidence': 0,
                'spike_size': spike_size,
                'atr': current_atr,
                'ratio': spike_size / current_atr,
                'status': 'BELOW_MIN_SPIKE_ATR',
                'spike_high': spike_high,
                'spike_low': spike_low,
                'spike_age': lookback - 1 - (max_high_idx if spike_up_expected else min_low_idx),
                'current_price': closes[-1],
            })
            continue
        
        extreme_idx = max_high_idx if spike_up_expected else min_low_idx
        spike_age = lookback - 1 - extreme_idx
        current_price = closes[-1]
        
        # Entry / SL / TP
        sign = -1.0 if spike_up_expected else 1.0
        if spike_up_expected:
            entry_38 = spike_high - spike_size * RET_LEVELS[0]
            entry_50 = spike_high - spike_size * RET_LEVELS[1]
            sl_price = spike_high + current_atr * SL_ATR_BUFFER
        else:
            entry_38 = spike_low + spike_size * RET_LEVELS[0]
            entry_50 = spike_low + spike_size * RET_LEVELS[1]
            sl_price = spike_low - current_atr * SL_ATR_BUFFER
        
        level = entry_38
        retracement = RET_LEVELS[0]
        if abs(current_price - entry_50) < abs(current_price - entry_38):
            level = entry_50
            retracement = RET_LEVELS[1]
        
        risk = abs(sl_price - level)
        tp_price = level + sign * risk * DEFAULT_RR
        
        # Check if broken or outside buffer
        broken = (spike_up_expected and current_price >= spike_high) or (not spike_up_expected and current_price <= spike_low)
        outside_buffer = abs(current_price - level) > current_atr * ENTRY_BUFFER_ATRS
        
        if broken:
            confidence = 0.0
            status = "BROKEN_EXTREME"
        elif outside_buffer:
            confidence = 0.0
            status = "OUTSIDE_BUFFER"
        else:
            confidence = 60.0 + (spike_size / current_atr) * 3.0
            confidence = min(confidence, 95.0)
            status = "ACTIONABLE"
        
        results.append({
            'symbol': symbol,
            'direction': direction,
            'confidence': confidence,
            'entry': level,
            'sl': sl_price,
            'tp': tp_price,
            'spike_high': spike_high,
            'spike_low': spike_low,
            'spike_size': spike_size,
            'atr': current_atr,
            'ratio': spike_size / current_atr,
            'spike_age': spike_age,
            'current_price': current_price,
            'retracement': retracement,
            'status': status,
        })
        
    except Exception as e:
        print(f'Error scanning {symbol}: {type(e).__name__}: {e}')
        continue

# Sort by confidence
results.sort(key=lambda x: x['confidence'], reverse=True)

print('=== ACTIONABLE SPIKE SIGNALS (confidence > 0) ===')
actionable = [r for r in results if r['confidence'] > 0]
if actionable:
    for r in actionable:
        print(f"{r['symbol']:<10} {r['direction'].upper():<5} | Conf: {r['confidence']:.1f}% | Entry: {r['entry']:.2f} | SL: {r['sl']:.2f} | TP: {r['tp']:.2f} | Spike: {r['spike_high']:.2f}-{r['spike_low']:.2f} | Age: {r['spike_age']} bars | Ratio: {r['ratio']:.1f}xATR")
else:
    print('None found')

print()
print('=== WATCH-ONLY / BELOW THRESHOLD ===')
watch = [r for r in results if r['confidence'] == 0]
for r in watch:
    print(f"{r['symbol']:<10} {r['direction'].upper():<5} | {r['status']:<15} | Price: {r['current_price']:.2f} | Spike: {r['spike_high']:.2f}-{r['spike_low']:.2f} | Size: {r['spike_size']:.2f} | ATR: {r['atr']:.2f} | Ratio: {r['ratio']:.1f}x | Age: {r['spike_age']} bars")

print()
print('=== SUMMARY ===')
print(f'Total scanned: {len(results)}')
print(f'Actionable: {len(actionable)}')
print(f'Watch-only: {len(watch)}')
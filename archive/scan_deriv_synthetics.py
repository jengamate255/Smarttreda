import sys
import pandas as pd
import numpy as np
from datetime import datetime, timezone

sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig
from mt5_mcp.indicators import atr, rsi

# Configuration from boom_crash_strategy.py
BOOM_CRASH_SYMBOLS = [
    "BOOM1000", "BOOM900", "BOOM600", "BOOM500", "BOOM300", "BOOM200", "BOOM150", "BOOM100", "BOOM99", "BOOM50",
    "CRASH1000", "CRASH900", "CRASH600", "CRASH500", "CRASH300", "CRASH200", "CRASH150", "CRASH100", "CRASH99", "CRASH50"
]

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
print(f'Scanning {len(BOOM_CRASH_SYMBOLS)} Boom/Crash symbols...\n')

signals = []

for symbol in BOOM_CRASH_SYMBOLS:
    try:
        # Fetch M1 candles (60 bars for lookback)
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
            reason = "watch-only: spike extreme broken"
        elif outside_buffer:
            confidence = 0.0
            reason = "watch-only: price outside entry buffer"
        else:
            confidence = 60.0 + (spike_size / current_atr) * 3.0
            confidence = min(confidence, 95.0)
            reason = f"{direction.upper()} after {spike_age} bar {'up' if spike_up_expected else 'down'} spike, retrace {retracement:.0%}, ATR {current_atr:.3f}"
        
        signals.append({
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
            'spike_age': spike_age,
            'current_price': current_price,
            'retracement': retracement,
            'reason': reason,
            'broken': broken,
            'outside_buffer': outside_buffer,
        })
        
    except Exception as e:
        print(f'Error scanning {symbol}: {e}')
        continue

# Sort by confidence
signals.sort(key=lambda x: x['confidence'], reverse=True)

print("=== ACTIONABLE SIGNALS (confidence > 0) ===")
actionable = [s for s in signals if s['confidence'] > 0]
if actionable:
    for s in actionable:
        print(f"{s['symbol']}: {s['direction'].upper()} | Conf: {s['confidence']:.1f}% | Entry: {s['entry']:.2f} | SL: {s['sl']:.2f} | TP: {s['tp']:.2f} | Age: {s['spike_age']} bars | {s['reason']}")
else:
    print("None")

print("\n=== WATCH-ONLY SIGNALS ===")
watch = [s for s in signals if s['confidence'] == 0]
for s in watch:
    status = "BROKEN" if s['broken'] else "OUTSIDE_BUFFER"
    print(f"{s['symbol']}: {s['direction'].upper()} | {status} | Price: {s['current_price']:.2f} | Entry: {s['entry']:.2f} | Spike: {s['spike_high']:.2f}-{s['spike_low']:.2f} | Age: {s['spike_age']} bars")

print("\n=== NO SIGNAL / INSUFFICIENT DATA ===")
no_signal = [s for s in BOOM_CRASH_SYMBOLS if s not in [sig['symbol'] for sig in signals]]
for s in no_signal:
    print(f"{s}: No qualifying spike pattern")
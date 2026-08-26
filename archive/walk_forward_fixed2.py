import sys
import pandas as pd
import numpy as np
from datetime import datetime, timezone

sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig
from mt5_mcp.tools.deriv import deriv_candles
from mt5_mcp.indicators import atr as _atr

# API Token
API_TOKEN = "REDACTED_DERIV_PAT"

config = DerivConfig(
    enabled=True,
    ws_url="wss://ws.derivws.com/websockets/v3",
    app_id=1089,
    api_token=API_TOKEN,
)

client = DerivClient(config)
if not client.connect():
    print(f'Failed to connect: {client.last_error()}')
    sys.exit(1)

# Focus symbols
SYMBOLS = ['BOOM100', 'CRASH100']
WINDOW_DAYS = 3
STEP_DAYS = 1
TOTAL_DAYS = 10

print(f'=== DERIV WALK-FORWARD ===')
print(f'Symbols: {", ".join(SYMBOLS)}')
print(f'Total days: {TOTAL_DAYS}')
print(f'{"="*50}')

results = []

for start_day in range(0, TOTAL_DAYS - WINDOW_DAYS, STEP_DAYS):
    train_end = start_day + WINDOW_DAYS
    test_day = start_day + WINDOW_DAYS + 1
    
    print(f'\nWindow {start_day+1}-{start_day+WINDOW_DAYS} (train) | Test day {test_day}')
    
    # Get training data
    train_atr = {}
    for sym in SYMBOLS:
        try:
            candles = deriv_candles(sym, count=400, granularity=60)
            if candles and len(candles) > 100:
                df = pd.DataFrame(candles)
                # Column names from deriv_candles: time, open, high, low, close, volume
                # 'time' is epoch seconds
                df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
                df = df.rename(columns={'time': 'datetime', 'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close'})
                df[['open','high','low','close']] = df[['open','high','low','close']].astype(float)
                df = df[['datetime', 'open', 'high', 'low', 'close']].copy()
                atr_val = _atr(df, period=14).iloc[-1]
                train_atr[sym] = atr_val
                print(f'  {sym}: ATR={atr_val:.2f}')
            else:
                print(f'  {sym}: insufficient data')
        except Exception as e:
            print(f'  {sym}: Error - {str(e)[:30]}')
    
    # Get test data
    test_data = {}
    for sym in SYMBOLS:
        try:
            candles = deriv_candles(sym, count=200, granularity=60)
            if candles and len(candles) > 50:
                df = pd.DataFrame(candles)
                df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
                df = df.rename(columns={'time': 'datetime', 'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close'})
                df[['open','high','low','close']] = df[['open','high','low','close']].astype(float)
                df = df[['datetime', 'open', 'high', 'low', 'close']].copy()
                test_data[sym] = df
                print(f'  {sym}: {len(candles)} test candles')
            else:
                print(f'  {sym}: insufficient test data')
        except Exception as e:
            print(f'  {sym}: Error - {str(e)[:30]}')
    
    # Signal detection on test data
    for sym in SYMBOLS:
        if sym not in test_data or sym not in train_atr:
            continue
        
        df = test_data[sym]
        current_atr = _atr(df, period=14).iloc[-1]
        if current_atr <= 0:
            continue
        
        # Simple spike detection at last bar
        lookback = 30
        if len(df) < lookback + 5:
            continue
        
        idx = len(df) - 5
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        
        spike_up_expected = sym.startswith('BOOM')
        rel_idx = idx - lookback
        
        if spike_up_expected:
            max_h_rel = int(np.argmax(highs[rel_idx:idx+1]))
            max_h_idx = rel_idx + max_h_rel
            spike_h = highs[max_h_idx]
            spike_l = np.min(lows[rel_idx:max_h_idx+1]) if max_h_rel > 0 else lows[rel_idx]
            direction = "short"
        else:
            min_l_rel = int(np.argmin(lows[rel_idx:idx+1]))
            min_l_idx = rel_idx + min_l_rel
            spike_l = lows[min_l_idx]
            spike_h = np.max(highs[rel_idx:min_l_idx+1]) if min_l_rel > 0 else highs[rel_idx]
            direction = "long"
        
        spike_size = spike_h - spike_l
        if spike_size < 2.5 * current_atr:
            continue
        
        sign = -1.0 if spike_up_expected else 1.0
        if spike_up_expected:
            entry = spike_h - spike_size * 0.382
            sl = spike_h + train_atr[sym] * 2.0
        else:
            entry = spike_l + spike_size * 0.382
            sl = spike_l - current_atr * 2.0
        
        # Retracement level
        level = entry
        if abs(current_price - (spike_l + spike_size * 0.5)) < abs(current_price - (spike_l + spike_size * 0.382)):
            level = spike_l + spike_size * 0.5
        
        risk = abs(sl - level)
        tp = level + sign * risk * 1.5
        
        # Check outcome
        remaining = df.iloc[idx+1:]
        if len(remaining) == 0:
            continue
        
        remaining_high = remaining['high'].max()
        remaining_low = remaining['low'].min()
        remaining_close = remaining['close'].iloc[-1]
        
        sl_hit = tp_hit = False
        exit_price = remaining_close
        exit_reason = 'time'
        
        if direction == 'long':
            if remaining_low <= sl: sl_hit = True; exit_price = sl; exit_reason = 'sl'
            elif remaining_high >= tp: tp_hit = True; exit_price = tp; exit_reason = 'tp'
        else:
            if remaining_high >= sl: sl_hit = True; exit_price = sl; exit_reason = 'sl'
            elif remaining_low <= tp: tp_hit = True; exit_price = tp; exit_reason = 'tp'
        
        sign_mult = 1 if direction == 'long' else -1
        pnl = (exit_price - entry) * sign_mult
        
        results.append({
            'symbol': sym,
            'train_window': f'days {start_day+1}-{start_day+WINDOW_DAYS}',
            'test_day': f'day {test_day}',
            'direction': direction,
            'entry': entry,
            'sl': sl,
            'tp': tp,
            'pnl': pnl,
            'spike_size': spike_size,
        })
    
    # Window summary
    window_results = [r for r in results if r['train_window'] == f'days {start_day+1}-{start_day+WINDOW_DAYS}']
    if window_results:
        wins = sum(1 for r in window_results if r['pnl'] > 0)
        print(f'Window {start_day+1}-{start_day+WINDOW_DAYS}: {len(window_results)} signals, {wins}/{len(window_results)} win rate, ${sum(r["pnl"] for r in window_results):.2f} net')

print(f'\n=== SUMMARY ===')
if results:
    df = pd.DataFrame(results)
    wins = (df['pnl'] > 0).sum()
    total = len(df)
    print(f'Total signals: {total}, Wins: {wins}, WR: {wins/total*100:.1f}%')
    print(f'Net PnL: ${df["pnl"].sum():.2f}')
    print(f'Avg PnL: ${df["pnl"].mean():.2f}')
    
    print(f'\nBy symbol:')
    for sym in SYMBOLS:
        sd = df[df['symbol'] == sym]
        if len(sd) > 0:
            w = (sd['pnl'] > 0).sum()
            print(f'  {sym}: {len(sd)} signals, WR={w}/{len(sd)} = {(w/len(sd)*100):.1f}%, Net=${sd["pnl"].sum():.2f}')
else:
    print('No signals found')
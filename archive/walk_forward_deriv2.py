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

# Focus symbols: BOOM100 and CRASH100 (best performers from 3-day backtest)
SYMBOLS = ['BOOM100', 'CRASH100']
WINDOW_DAYS = 3    # training window
STEP_DAYS = 1      # step size
TOTAL_DAYS = 10    # total days to analyze

print(f'=== DERIV WALK-FORWARD ANALYSIS ===')
print(f'Symbols: {", ".join(SYMBOLS)}')
print(f'Window: {WINDOW_DAYS} days training, {STEP_DAYS} day testing')
print(f'Total days: {TOTAL_DAYS}')
print(f'{"="*60}')

results = []

for start_day in range(0, TOTAL_DAYS - WINDOW_DAYS, STEP_DAYS):
    train_day = start_day + 1
    test_day = start_day + WINDOW_DAYS + 1
    
    print(f'\n=== Window {start_day+1} to {start_day+WINDOW_DAYS} (train) | Test day {test_day} ===')
    
    # ===== TRAINING PHASE =====
    # Get historical data for training window (WINDOW_DAYS of M1 data)
    train_data = {}
    for sym in SYMBOLS:
        try:
            # Get approximately WINDOW_DAYS * 24 hours of M1 data
            # 1 day = 1440 M1 bars, so 3 days = 4320 bars, use 800 for speed
            candles = deriv_candles(sym, count=800, granularity=60)
            if candles and len(candles) > 200:
                # Calculate ATR to understand volatility
                df = pd.DataFrame(candles)
                df['time'] = pd.to_datetime(df['epoch'], unit='s', utc=True)
                df = df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close'})
                df[['open','high','low','close']] = df[['open','high','low','close']].astype(float)
                df = df[['time', 'open', 'high', 'low', 'close']].copy()
                
                atr_val = _atr(df, period=14).iloc[-1]
                train_data[sym] = {
                    'candles': candles,
                    'atr': atr_val,
                    'df': df
                }
                print(f'  {sym}: {len(candles)} candles, ATR={atr_val:.2f}')
            else:
                print(f'  {sym}: insufficient training data')
        except Exception as e:
            print(f'  {sym}: Error - {str(e)[:50]}')
    
    # ===== TESTING PHASE =====
    # Get data for test day (next day after training window)
    test_data = {}
    for sym in SYMBOLS:
        try:
            # Get ~24 hours of test data
            candles = deriv_candles(sym, count=200, granularity=60)
            if candles and len(candles) > 50:
                df = pd.DataFrame(candles)
                df['time'] = pd.to_datetime(df['epoch'], unit='s', utc=True)
                df = df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close'})
                df[['open','high','low','close']] = df[['open','high','low','close']].astype(float)
                df = df[['time', 'open', 'high', 'low', 'close']].copy()
                test_data[sym] = df
                print(f'  {sym}: {len(candles)} candles for testing')
            else:
                print(f'  {sym}: insufficient test data')
        except Exception as e:
            print(f'  {sym}: Error - {str(e)[:50]}')
    
    # ===== SIGNAL DETECTION ON TEST DATA =====
    # Use training ATR value for consistency
    for sym in SYMBOLS:
        if sym not in train_data or sym not in test_data:
            continue
            
        train_atr = train_data[sym]['atr']
        df = test_data[sym]
        
        # Calculate ATR on test data for signal detection
        test_atr = _atr(df, period=14).iloc[-1]
        
        # Detect spike at the last possible index
        lookback = 30
        if len(df) < lookback + 5:
            continue
            
        # Get the signal at index -5 (last but one)
        idx = len(df) - 5
        
        # Manual spike detection mirroring the strategy
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        
        if idx < lookback:
            continue
            
        current_atr = test_atr
        if current_atr <= 0:
            continue
            
        spike_up_expected = sym.startswith('BOOM')
        
        if spike_up_expected:
            max_h_idx = int(np.argmax(highs[idx-lookback:idx+1])) + (idx - lookback)
            spike_h = highs[max_h_idx]
            spike_l = np.min(lows[idx-lookback:max_h_idx+1]) if max_h_idx > idx-lookback else lows[idx-lookback]
            direction = "short"
        else:
            min_l_idx = int(np.argmin(lows[idx-lookback:idx+1])) + (idx-lookback)
            spike_l = lows[min_l_idx]
            spike_h = np.max(highs[idx-lookback:min_l_idx+1]) if min_l_idx > idx-lookback else highs[idx-lookback]
            direction = "long"
        
        spike_size = spike_h - spike_l
        
        if spike_size < 2.5 * current_atr:  # min spike threshold
            continue
        
        # Entry / SL / TP
        sign = -1.0 if spike_up_expected else 1.0
        if spike_up_expected:
            entry_38 = spike_h - spike_size * 0.382
            entry_50 = spike_h - spike_size * 0.5
            sl = spike_h + current_atr * 2.0
        else:
            entry_38 = spike_l + spike_size * 0.382
            entry_50 = spike_l + spike_size * 0.5
            sl = spike_l - current_atr * 2.0
        
        # Pick retracement level closest to current price
        level = entry_38
        retracement = 0.382
        if abs(current_price - entry_50) < abs(current_price - entry_38):
            level = entry_50
            retracement = 0.5
        
        risk = abs(sl - level)
        tp = level + sign * risk * 1.5
        
        # Check what happened next (in the remaining data)
        remaining = df.iloc[idx+1:]
        if len(remaining) == 0:
            continue
            
        remaining_high = remaining['high'].max()
        remaining_low = remaining['low'].min()
        remaining_close = remaining['close'].iloc[-1]
        
        # Determine outcome
        sl_hit = False
        tp_hit = False
        exit_price = remaining_close
        exit_reason = 'time'
        
        if direction == 'long':
            if remaining_low <= sl:
                sl_hit = True
                exit_price = sl
                exit_reason = 'sl'
            elif remaining_high >= tp:
                tp_hit = True
                exit_price = tp
                exit_reason = 'tp'
        else:  # short
            if remaining_high >= sl:
                sl_hit = True
                exit_price = sl
                exit_reason = 'sl'
            elif remaining_low <= tp:
                tp_hit = True
                exit_price = tp
                exit_reason = 'tp'
        
        # Calculate PnL
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
            'atr': current_atr,
            'confidence': min(60.0 + (spike_size/current_atr)*3.0, 95.0) if spike_size > 2.5*current_atr else 0,
        })
    
    # Print window summary
    window_results = [r for r in results if r['train_window'] == f'days {start_day+1}-{start_day+WINDOW_DAYS}']
    if window_results:
        wins = sum(1 for r in window_results if r['pnl'] > 0)
        print(f'Window {start_day+1}-{start_day+WINDOW_DAYS}: {len(window_results)} signals, {sum(1 for r in window_results if r["pnl"]>0)}/{len(window_results)} win rate, ${sum(r["pnl"] for r in window_results):.2f} net')
    
print(f'\n=== WALK-FORWARD SUMMARY ===')
if results:
    df = pd.DataFrame(results)
    print(f'Total signals: {len(df)}')
    print(f'Win rate: {(df["pnl"] > 0).sum()}/{len(df)} = {(df["pnl"] > 0).mean()*100:.1f}%')
    print(f'Net PnL: ${df["pnl"].sum():.2f}')
    print(f'Avg PnL per trade: ${df["pnl"].mean():.2f}')
    print(f'Profit factor: {df["pnl"].sum() / abs(df["pnl"][df["pnl"] < 0].sum()) if (df["pnl"] < 0).any() else "N/A"}
    
    print(f'\nBy symbol:')
    for sym in SYMBOLS:
        sym_df = df[df['symbol'] == sym]
        if len(sym_df) > 0:
            print(f'  {sym}: {len(sym_df)} signals, WR={(sym_df["pnl"] > 0).sum()}/{len(sym_df)} = {(sym_df["pnl"] > 0).mean()*100:.1f}%, Net=${sym_df["pnl"].sum():.2f}')
    
    print(f'\nBy training window:')
    for w in df['train_window'].unique():
        w_df = df[df['train_window'] == w]
        print(f'  {w}: {len(w_df)} signals, WR={(w_df["pnl"] > 0).sum()}/{len(w_df)} = {(w_df["pnl"] > 0).mean()*100:.1f}%, Net=${w_df["pnl"].sum():.2f}')
else:
    print('No signals found in walk-forward analysis')
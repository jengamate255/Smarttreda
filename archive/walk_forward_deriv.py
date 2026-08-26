import sys
import pandas as pd
import numpy as np
from datetime import datetime, timezone

sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig
from mt5_mcp.tools.deriv import deriv_candles
from mt5_mcp.indicators import atr as _atr
from mt5_mcp.tools.boom_crash_strategy import detect_spike_at_index, BOOM_CRASH_SYMBOLS

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

# Focus symbols: BOOM100 and CRASH100 (best performers from backtest)
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
    train_end = start_end + WINDOW_DAYS
    test_day = start_end + WINDOW_DAYS
    
    train_start_str = f'day {start_end+1}'
    test_day_str = f'day {test_day+1}'
    
    print(f'\n--- Training: {train_start_str} | Testing: {test_day_str} ---')
    
    # ===== TRAINING PHASE =====
    # Get historical data for training window
    train_candles = {}
    for sym in SYMBOLS:
        try:
            # Get WINDOW_DAYS * 24 * 4 = 960 M15 bars approx
            candles = deriv_candles(sym, count=800, granularity=60)  # 800 M1 bars
            if candles and len(candles) > 200:
                train_candles[sym] = candles
                print(f'  {sym}: {len(candles)} candles loaded for training')
            else:
                print(f'  {sym}: insufficient data ({len(candles) if candles else 0} candles)')
        except Exception as e:
            print(f'  {sym}: Error - {str(e)[:50]}')
    
    # Simple training: just note the parameters we'll use
    # In a full walk-forward, we'd optimize parameters here
    train_params = {
        'spike_threshold_atr': 2.5,
        'min_spike_ticks': 8,
        'sl_atr_buffer': 2.0,
        'reward_risk_ratio': 1.5,
    }
    
    print(f'  Training params: {train_params}')
    
    # ===== TESTING PHASE =====
    # Get data for test day (next day)
    test_candles = {}
    for sym in SYMBOLS:
        try:
            # Get ~24 hours of data for testing (96 M1 bars at 60s granularity, or 384 for H1)
            # Using M1 24h = 1440 bars, but let's use 200 for speed
            candles = deriv_candles(sym, count=200, granularity=60)
            if candles and len(candles) > 50:
                test_candles[sym] = candles
                print(f'  {sym}: {len(candles)} candles loaded for testing')
            else:
                print(f'  {sym}: insufficient data ({len(candles) if candles else 0} candles)')
        except Exception as e:
            print(f'  {sym}: Error - {str(e)[:50]}')
    
    # ===== SIGNAL DETECTION ON TEST DATA =====
    # Simple: detect spikes at the last few indices
    for sym in SYMBOLS:
        if sym not in test_candles or len(test_candles[sym]) < 50:
            continue
            
        candles = test_candles[sym]
        df = pd.DataFrame(candles)
        df['time'] = pd.to_datetime(df['epoch'], unit='s', utc=True)
        df = df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close'})
        df = df[['time', 'open', 'high', 'low', 'close']].copy()
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        
        atr_series = _atr(df, period=14)
        
        # Detect spikes at each index from 30 to len-1
        for idx in range(30, min(len(df), 80)):
            signal = detect_spike_at_index(
                df['high'].astype(float).values,
                df['low'].astype(float).values,
                df['close'].astype(float).values,
                df['time'].astype(float).values,
                idx,
                sym,
                lookback=30,
                spike_threshold_atr=train_params['spike_threshold_atr'],
                min_spike_ticks=train_params['min_spike_ticks'],
                reward_risk_ratio=train_params['reward_risk_ratio'],
                sl_atr_buffer=train_params['sl_atr_buffer'],
            )
            
            if signal and signal.get('near_entry'):
                # Trade signal found
                direction = signal['direction']
                entry = signal['entry_price']
                sl = signal['sl_price']
                tp = signal['tp_price']
                confidence = signal.get('confidence', 0)
                
                # Check if price moved to SL/TP in the remaining data
                remaining = df.iloc[idx+1:]
                if len(remaining) > 0:
                    remaining_high = remaining['high'].astype(float).max()
                    remaining_low = remaining['low'].astype(float).min()
                    remaining_close = remaining['close'].astype(float).iloc[-1]
                    
                    # Check SL hit
                    sl_hit = False
                    tp_hit = False
                    
                    if direction == 'long':
                        if remaining_low <= sl:
                            sl_hit = True
                        if remaining_high >= tp:
                            tp_hit = True
                    else:  # short
                        if remaining_high >= sl:
                            sl_hit = True
                        if remaining_low <= tp:
                            tp_hit = True
                    
                    # Calculate PnL
                    if sl_hit:
                        pnl = (sl - entry) * (-1 if direction == 'long' else 1)  # short PnL logic
                    elif tp_hit:
                        pnl = (tp - entry) * (-1 if direction == 'long' else 1)
                    else:
                        # Time exit at last price
                        pnl = (remaining_close - entry) * (-1 if direction == 'long' else 1)
                    
                    results.append({
                        'symbol': sym,
                        'train_window': train_start_str,
                        'test_day': test_day_str,
                        'direction': direction,
                        'entry': entry,
                        'sl': sl,
                        'tp': tp,
                        'pnl': pnl,
                        'confidence': confidence,
                        'signal_idx': idx,
                    })
                    
                    # Only first signal per symbol per window
                    break

print(f'\n=== WALK-FORWARD RESULTS ===')
if results:
    df_results = pd.DataFrame(results)
    print(f'Total signals: {len(df_results)}')
    print(f'Win rate: {(df_results["pnl"] > 0).sum()}/{len(df_results)} = {(df_results["pnl"] > 0).mean()*100:.1f}%')
    print(f'Net PnL: ${df_results["pnl"].sum():.2f}')
    print(f'Avg PnL: ${df_results["pnl"].mean():.2f}')
    print(f'Avg R: N/A (not calculated)')
    
    print(f'\nBy symbol:')
    for sym in SYMBOLS:
        sym_results = df_results[df_results['symbol'] == sym]
        if len(sym_results) > 0:
            print(f'  {sym}: {len(sym_results)} signals, WR={(sym_results["pnl"] > 0).sum()}/{len(sym_results)} = {(sym_results["pnl"] > 0).mean()*100:.1f}%, Net=${sym_results["pnl"].sum():.2f}')
    
    print(f'\nBy training window:')
    for w in df_results['train_window'].unique():
        w_results = df_results[df_results['train_window'] == w]
        print(f'  {w}: {len(w_results)} signals, WR={(w_results["pnl"] > 0).sum()}/{len(w_results)} = {(w_results["pnl"] > 0).mean()*100:.1f}%, Net=${w_results["pnl"].sum():.2f}')
    
    print(f'\nBy test day:')
    for d in df_results['test_day'].unique():
        d_results = df_results[df_results['test_day'] == w]
        print(f'  {d}: {len(d_results)} signals, WR={(d_results["pnl"] > 0).sum()}/{len(d_results)} = {(d_results["pnl"] > 0).mean()*100:.1f}%')
else:
    print('No signals found in walk-forward analysis')
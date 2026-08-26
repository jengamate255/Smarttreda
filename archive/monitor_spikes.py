import sys
import time
import json
import datetime
from collections import defaultdict

sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig
from mt5_mcp.indicators import atr, rsi
from mt5_mcp.tools.boom_crash_strategy import detect_spike, BOOM_CRASH_SYMBOLS

# Configuration
SYMBOLS_TO_WATCH = [
    "BOOM900", "BOOM1000", "BOOM500", "BOOM200",
    "CRASH900", "CRASH1000", "CRASH500", "CRASH200",
]

MONITOR_INTERVAL = 60  # seconds between checks
MIN_SPIKE_ATR = 5.0
LOOKBACK = 60

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

print(f'=== SPIKE MONITOR STARTED ===')
print(f'Monitoring {len(SYMBOLS_TO_WATCH)} symbols every {MONITOR_INTERVAL}s')
print(f'{"="*60}')

# Paper trading state
paper_trades = []
daily_pnl = 0.0
trade_count = 0
win_trades = 0
loss_trades = 0

# Performance tracking
signal_counts = {"ACTIONABLE": 0, "WATCH-ONLY": 0, "BELOW_THRESHOLD": 0}

try:
    while True:
        start_time = time.time()
        
        all_symbols = SYMBOLS_TO_WATCH + BOOM_CRASH_SYMBOLS
        signals = detect_spike.__wrapped__ if hasattr(detect_spike, '__wrapped__') else None
        
        # Check each symbol
        for symbol in all_symbols:
            try:
                # Fetch M1 candles
                result = client.ticks_history(symbol, count=LOOKBACK, style="candles", granularity=60)
                if not isinstance(result, dict) or 'candles' not in result or len(result['candles']) < 30:
                    signal_counts["BELOW_THRESHOLD"] += 1
                    continue
                
                candles = result['candles']
                df = pd.DataFrame(candles)
                df['time'] = pd.to_datetime(df['epoch'], unit='s', utc=True)
                df = df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close'})
                df = df[['time', 'open', 'high', 'low', 'close']].copy()
                
                close = df['close'].astype(float)
                high = df['high'].astype(float)
                low = df['low'].astype(float)
                
                # Calculate ATR
                atr_series = atr(df, period=14)
                current_atr = float(atr_series.iloc[-1])
                
                if current_atr <= 0:
                    signal_counts["BELOW_THRESHOLD"] += 1
                    continue
                
                # Detect spike (mirroring live logic)
                spike_up_expected = symbol.startswith("BOOM")
                
                highs = df['high'].values
                lows = df['low'].values
                closes_arr = df['close'].values
                lookback = len(df)
                
                if spike_up_expected:
                    max_high_idx = int(np.argmax(highs))
                    spike_high = highs[max_high_idx]
                    spike_low = np.min(lows[:max_high_idx + 1]) if max_high_idx > 0 else lows[0]
                    direction = "short"
                else:
                    min_low_idx = int(np.argmin(lows))
                    spike_low = lows[min_low_idx]
                    spike_high = np.max(highs[:min_low_idx + 1]) if min_low_idx > 0 else highs[0]
                    direction = "long"
                
                spike_size = spike_high - spike_low
                
                if spike_size < MIN_SPIKE_ATR * current_atr:
                    signal_counts["BELOW_THRESHOLD"] += 1
                    continue
                
                extreme_idx = max_high_idx if spike_up_expected else min_low_idx
                spike_age = lookback - 1 - extreme_idx
                current_price = closes_arr[-1]
                
                # Entry / SL / TP
                sign = -1.0 if spike_up_expected else 1.0
                if spike_up_expected:
                    entry_38 = spike_high - spike_size * 0.382
                    entry_50 = spike_high - spike_size * 0.5
                    sl_price = spike_high + current_atr * 2.0
                else:
                    entry_38 = spike_low + spike_size * 0.382
                    entry_50 = spike_low + spike_size * 0.5
                    sl_price = spike_low - current_atr * 2.0
                
                level = entry_38
                retracement = 0.382
                if abs(current_price - entry_50) < abs(current_price - entry_38):
                    level = entry_50
                    retracement = 0.5
                
                risk = abs(sl_price - level)
                tp_price = level + sign * risk * 1.5
                
                # Classification
                broken = (spike_up_expected and current_price >= spike_high) or (not spike_up_expected and current_price <= spike_low)
                outside_buffer = abs(current_price - level) > current_atr * 1.5
                
                if broken:
                    confidence = 0
                    status = "BROKEN"
                    signal_counts["WATCH-ONLY"] += 1  # count as watch
                elif outside_buffer:
                    confidence = 0
                    status = "OUTSIDE_BUFFER"
                    signal_counts["WATCH-ONLY"] += 1
                else:
                    confidence = 60.0 + (spike_size / current_atr) * 3.0
                    confidence = min(confidence, 95.0)
                    status = "ACTIONABLE"
                    signal_counts["ACTIONABLE"] += 1
                
                # Check if we already have a trade for this symbol
                existing = [t for t in paper_trades if t['symbol'] == symbol and t['status'] in ['OPEN', 'FILLED']]
                if existing:
                    continue  # Skip if already have open trade for this symbol
                
                # Create paper trade record
                trade = {
                    'symbol': symbol,
                    'direction': direction,
                    'entry_price': level,
                    'sl_price': sl_price,
                    'tp_price': tp_price,
                    'entry_time': datetime.datetime.now(),
                    'spike_high': spike_high,
                    'spike_low': spike_low,
                    'spike_size': spike_size,
                    'atr': current_atr,
                    'spike_age': spike_age,
                    'current_price': current_price,
                    'retracement': retracement,
                    'status': 'OPEN',
                    'signal_confidence': confidence,
                }
                
                paper_trades.append(trade)
                
            except Exception as e:
                print(f'Error monitoring {symbol}: {e}')
                continue
        
        # Check existing trades for exit
        for trade in paper_trades:
            if trade['status'] != 'OPEN':
                continue
            
            # Check SL/TP hit (simplified: check if current price crossed SL/TP)
            # In real implementation, would check real-time price
            # For now, just mark as time-exit after 30 min
            hold_time = (datetime.datetime.now() - trade['entry_time']).seconds / 60
            
            if hold_time > 30:  # 30 min max hold
                trade['status'] = 'TIME'
                trade['exit_price'] = trade['current_price']
                trade['exit_reason'] = 'time'
                # Calculate PnL
                sign = 1 if trade['direction'] == 'long' else -1
                trade['pnl'] = (trade['exit_price'] - trade['entry_price']) * sign * 1.0  # 1.0 lot equiv
                if trade['pnl'] > 0:
                    win_trades += 1
                else:
                    loss_trades += 1
                daily_pnl += trade['pnl']
                trade_count += 1
        
        # Cleanup closed trades
        paper_trades[:] = [t for t in paper_trades if t['status'] in ['OPEN'] or (t['status'] in ['TIME', 'SL', 'TP'] and len(paper_trades) < 50)]
        
        # Print summary every 5 minutes
        elapsed = time.time() - start_time
        if int(time.time()) % 300 == 0:  # Every 5 min
            print(f'\n--- {datetime.datetime.now().strftime("%H:%M:%S")} ---')
            print(f'Actionable: {signal_counts["ACTIONABLE"]} | Watch: {signal_counts["WATCH-ONLY"]} | Below: {signal_counts["BELOW_THRESHOLD"]}')
            print(f'Open trades: {len([t for t in paper_trades if t["status"] == "OPEN"])}')
            if paper_trades:
                open_trades = [t for t in paper_trades if t['status'] == 'OPEN']
                print(f'Top signal: {open_trades[0]["symbol"]} {open_trades[0]["direction"].upper()} conf={open_trades[0]["signal_confidence"]:.1f}%')
        
        # Sleep until next interval
        sleep_time = MONITOR_INTERVAL - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)
            
except KeyboardInterrupt:
    print('\n=== STOPPING MONITOR ===')
    # Final summary
    final_open = len([t for t in paper_trades if t['status'] == 'OPEN'])
    final_closed = len(paper_trades) - final_open
    print(f'=== FINAL SUMMARY ===')
    print(f'Total signals generated: {sum(signal_counts.values())}')
    print(f'Actionable: {signal_counts["ACTIONABLE"]}')
    print(f'Watch-only: {signal_counts["WATCH-ONLY"]}')
    print(f'Closed trades: {final_closed}')
    print(f'Open trades: {final_open}')
    if final_closed > 0:
        wins = sum(1 for t in paper_trades if t['status'] in ['TP', 'SL'] and t.get('pnl', 0) > 0)
        losses = sum(1 for t in paper_trades if t['status'] in ['TP', 'SL'] and t.get('pnl', 0) <= 0)
        print(f'Win rate: {wins}/{final_closed} = {wins/final_closed*100:.1f}%')
        print(f'Net PnL: ${sum(t.get("pnl", 0) for t in paper_trades if t.get("pnl")):.2f}')
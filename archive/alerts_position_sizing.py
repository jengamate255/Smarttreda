import sys
import time
import json
import datetime
import numpy as np

sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig
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

# Top 3 symbols from backtest analysis
TOP_SYMBOLS = ["BOOM900", "BOOM1000", "CRASH600"]

# Position sizing based on R multiples from backtest
# Range: 0.057 to 0.673 R per trade
# Typical risk: 1% of account per trade
# Position size calculation: (account_size * risk_pct) / (stop_distance_in_pips * pip_value)

R_MULTIPLES = {
    "BOOM900": 0.673,   # From backtest: 0.673 R per trade
    "BOOM1000": 0.308,  # From backtest: 0.308 R per trade  
    "CRASH600": 0.231,  # From backtest: 0.231 R per trade
}

ACCOUNT_SIZE = 10000  # Example account size
RISK_PER_TRADE_PCT = 1.0  # 1% of account at risk per trade

print(f'=== AUTOMATED ALERTS & POSITION SIZING ===')
print(f'Started: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
print(f'Account: ${ACCOUNT_SIZE:,}')
print(f'Risk per trade: {RISK_PER_TRADE_PCT}%')
print(f'{"="*60}')

# Paper trade tracking
paper_trades = []
daily_pnl = 0.0
trade_log = []

signal_stats = {"ACTIONABLE": 0, "WATCH-ONLY": 0, "BELOW": 0}

try:
    while True:
        start = time.time()
        
        for symbol in TOP_SYMBOLS:
            try:
                result = client.ticks_history(symbol, count=60, style="candles", granularity=60)
                if not isinstance(result, dict) or 'candles' not in result or len(result['candles']) < 30:
                    signal_stats["BELOW"] += 1
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
                    signal_stats["BELOW"] += 1
                    continue
                
                spike_up = symbol.startswith("BOOM")
                
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
                    confidence = 0
                    status = "BROKEN"
                    signal_stats["WATCH-ONLY"] += 1
                elif outside:
                    confidence = 0
                    status = "OUTSIDE_BUFFER"
                    signal_stats["WATCH-ONLY"] += 1
                else:
                    confidence = 60.0 + (abs(spike_h - spike_l) / atr_val) * 3.0
                    confidence = min(confidence, 95.0)
                    status = "ACTIONABLE"
                    signal_stats["ACTIONABLE"] += 1
                
                # Check for existing open trade
                has_open = any(t['symbol'] == symbol and t['status'] == 'OPEN' for t in paper_trades)
                if has_open:
                    continue
                
                # Position sizing based on R multiple
                r_multiple = R_MULTIPLES.get(symbol, 0.2)
                risk_amount = ACCOUNT_SIZE * (RISK_PER_TRADE_PCT / 100)
                # Stop distance in terms of account risk
                stop_distance_pips = abs(entry - sl)
                # Simplified: risk per trade = stop_distance * pip_value * lots
                # For Deriv synthetic indices, pip is typically 1.0 for whole numbers
                # Let's use: lots = risk_amount / stop_distance
                # But we need pip value - for Deriv synthetic indices, typical pip value is $1 per lot for whole number indices
                # Let's use a simplified position size:
                stop_distance = abs(entry - sl)
                if stop_distance > 0:
                    lots = risk_amount / stop_distance
                else:
                    lots = 0.01  # minimum lot
                
                # Create paper trade
                trade = {
                    'symbol': symbol,
                    'direction': direction,
                    'entry_price': entry,
                    'sl_price': sl,
                    'tp_price': tp,
                    'entry_time': datetime.datetime.now(),
                    'spike_high': spike_h if spike_up else spike_l,
                    'spike_low': spike_l if spike_up else spike_h,
                    'spike_size': abs(spike_h - spike_l),
                    'atr': atr_val,
                    'current_price': current_price,
                    'spike_age': len(df) - 1 - (max_h_idx if spike_up else min_l_idx),
                    'status': 'OPEN',
                    'signal_confidence': confidence,
                    'signal_time': datetime.datetime.now(),
                    'lots': lots,
                    'risk_amount': risk_amount,
                }
                paper_trades.append(trade)
                
                # Print alert
                print(f'\n[ALERT] {symbol}: {status} conf={confidence:.1f}%')
                print(f'  Direction: {direction} | Entry: {entry:.2f} | SL: {sl:.2f} | TP: {tp:.2f}')
                print(f'  Position: {lots:.2f} lots | Risk: ${risk_amount:.2f} | R multiple: {r_multiple:.3f}')
                print(f'  Spike: {spike_h:.2f}-{spike_l:.2f} size={abs(spike_h-spike_l):.2f} age={age} bars')
                
            except Exception as e:
                print(f'Error {symbol}: {str(e)[:30]}')
                continue
        
        # Check existing trades for exit (30 min hold or SL/TP)
        for trade in paper_trades:
            if trade['status'] != 'OPEN':
                continue
            
            hold_min = (datetime.datetime.now() - trade['entry_time']).seconds / 60
            
            direction_mult = 1 if trade['direction'] == 'LONG' else -1
            price_diff = (trade['current_price'] - trade['entry_price']) * direction_mult
            
            sl_hit = price_diff <= (trade['sl_price'] - trade['entry_price']) * direction_mult
            tp_hit = price_diff >= (trade['tp_price'] - trade['entry_price']) * direction_mult
            
            if hold_min > 30 or sl_hit or tp_hit:
                trade['status'] = 'CLOSED'
                if sl_hit:
                    trade['exit_reason'] = 'SL'
                    trade['exit_price'] = trade['sl_price']
                elif tp_hit:
                    trade['exit_reason'] = 'TP'
                    trade['exit_price'] = trade['tp_price']
                else:
                    trade['exit_reason'] = 'TIME'
                    trade['exit_price'] = trade['current_price']
                
                sign = 1 if trade['direction'] == 'LONG' else -1
                trade['pnl'] = (trade['exit_price'] - trade['entry_price']) * sign
                daily_pnl += trade['pnl']
                trade_log.append(trade)
        
        # Cleanup
        paper_trades[:] = [t for t in paper_trades if t['status'] == 'OPEN'] + \
                          [t for t in paper_trades if t['status'] == 'CLOSED' and len([x for x in paper_trades if x['status'] == 'CLOSED']) < 50]
        
        # Print stats every 60 seconds
        if int(time.time()) % 60 < 5:
            open_count = len([t for t in paper_trades if t['status'] == 'OPEN'])
            closed_count = len(paper_trades) - open_count
            print(f'\n[{datetime.datetime.now().strftime("%H:%M:%S")}] '
                  f'Actionable:{signal_stats["ACTIONABLE"]} | '
                  f'Watch:{signal_stats["WATCH-ONLY"]} | '
                  f'Below:{signal_stats["BELOW"]} | '
                  f'Open:{open_count} | '
                  f'Closed:{closed_count} | '
                  f'Daily PnL:${daily_pnl:.2f} | '
                  f'Trades:{len(trade_log)}')
        
        # Sleep 60s, check every 5s
        for _ in range(12):
            if time.time() - start > 60:
                break
            time.sleep(5)
            
except KeyboardInterrupt:
    print(f'\n\n=== ALERTS STOPPED ===')
    print(f'Total trades logged: {len(trade_log)}')
    print(f'Daily PnL: ${daily_pnl:.2f}')
    if trade_log:
        wins = sum(1 for t in trade_log if t.get('pnl', 0) > 0)
        losses = sum(1 for t in trade_log if t.get('pnl', 0) <= 0)
        print(f'Win rate: {wins}/{len(trade_log)} = {wins/len(trade_log)*100:.1f}%')
        print(f'Best trade: ${max(t.get("pnl", 0) for t in trade_log):.2f}')
        print(f'Worst trade: ${min(t.get("pnl", 0) for t in trade_log):.2f}')
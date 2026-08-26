import sys
sys.path.insert(0, '.')

from backtest_boom_crash import run_backtest

# Test ALL BOOM and CRASH indices
boom_symbols = ['BOOM100', 'BOOM99', 'BOOM50', 'BOOM30', 'BOOM200', 'BOOM150']
crash_symbols = ['CRASH100', 'CRASH99', 'CRASH50', 'CRASH30', 'CRASH200', 'CRASH150']

all_symbols = boom_symbols + crash_symbols

print(f'Testing {len(all_symbols)} volatility indices (5 days each)\n')

result = run_backtest(
    symbols=all_symbols, 
    days=5, 
    spike_threshold_atr=2.5,
    min_spike_ticks=8,
    max_hold_bars=120,
    fill_deadline_bars=30,
    min_bars_between_trades=30,
    reward_risk_ratio=1.5,
    sl_atr_buffer=2.0,
)

m = result['metrics']
if m.get('total_trades'):
    print('=== COMPLETE VOLATILITY INDEX BACKTEST ===')
    print(f'Total trades: {m["total_trades"]}')
    print(f'Win rate: {m["win_rate"]}%')
    print(f'Profit factor: {m["profit_factor"]:.2f}')
    print(f'Net USD: ${m["net_usd"]:.2f}')
    print(f'Avg R: {m["avg_r"]:.3f}')
    print(f'Avg win: ${m["avg_win_usd"]:.2f}')
    print(f'Avg loss: ${m["avg_loss_usd"]:.2f}')
    print(f'Avg hold: {m["avg_hold_minutes"]} minutes')
    print()
    
    print('=== BY SYMBOL ===')
    for sym, d in sorted(result['by_symbol'].items(), key=lambda x: x[1]['win_rate'], reverse=True):
        pnl_color = '+' if d['net_usd'] >= 0 else ''
        print(f'{sym:<10} {d["trades"]:>4} trades {d["win_rate"]:>5.1f}% WR ${d["net_usd"]:>7.2f} net {d["avg_r"]:>6.3f} avg R')
    
    print()
    print('=== BOOM vs CRASH SUMMARY ===')
    boom_results = {k: v for k, v in result['by_symbol'].items() if 'BOOM' in k.upper()}
    crash_results = {k: v for k, v in result['by_symbol'].items() if 'CRASH' in k.upper()}
    
    if boom_results:
        total_trades = sum(d['trades'] for d in boom_results.values())
        total_wins = sum(int(d['trades'] * d['win_rate']/100) for d in boom_results.values())  # approx
        total_net = sum(d['net_usd'] for d in boom_results.values())
        avg_r = sum(d['avg_r'] * d['trades'] for d in boom_results.values()) / total_trades if total_trades > 0 else 0
        print(f'BOOM indices: {total_trades} trades, ~{sum(boom_results.values(), {}).get("win_rate",0):.1f}% WR, ${total_net:.2f} net, {avg_r:.3f} avg R')
    
    if crash_results:
        total_trades = sum(d['trades'] for d in crash_results.values())
        total_net = sum(d['net_usd'] for d in crash_results.values())
        avg_r = sum(d['avg_r'] * d['trades'] for d in crash_results.values()) / total_trades if total_trades > 0 else 0
        print(f'CRASH indices: {total_trades} trades, ${total_net:.2f} net, {avg_r:.3f} avg R')
    
    print()
    print('=== RANKING (by profitability) ===')
    sorted_symbols = sorted(result['by_symbol'].items(), key=lambda x: x[1]['net_usd'], reverse=True)
    for i, (sym, d) in enumerate(sorted_symbols, 1):
        print(f'{i}. {sym:<10} {d["trades"]:>3} trades {d["win_rate"]:>5.1f}% WR ${d["net_usd"]:>7.2f} net {d["avg_r"]:>6.3f} avg R')
else:
    print('No trades')
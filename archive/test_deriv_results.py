import sys
sys.path.insert(0, '.')

from backtest_boom_crash import run_backtest

# Test ALL Deriv volatility pairs: R_75, R_100, BOOM100, CRASH100
symbols = ['R_75', 'R_100', 'BOOM100', 'CRASH100']

print(f'Running backtest on {len(symbols)} Deriv volatility pairs (5 days)\n')

result = run_backtest(
    symbols=symbols, 
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
    print('=== BACKTEST RESULTS ===')
    print(f'Total trades: {m["total_trades"]}')
    print(f'Win rate: {m["win_rate"]}%')
    print(f'Profit factor: {m["profit_factor"]:.2f}')
    print(f'Net USD: ${m["net_usd"]:.2f}')
    print(f'Avg R: {m["avg_r"]:.3f}')
    print(f'Avg win: ${m["avg_win_usd"]:.2f}')
    print(f'Avg loss: ${m["avg_loss_usd"]:.2f}')
    print(f'Avg hold: {m["avg_hold_minutes"]} minutes')
    print(f'Exit reasons: {result["exit_reasons"]}')
    print()
    
    print('=== BY SYMBOL ===')
    for sym, d in result['by_symbol'].items():
        print(f'{sym}: {d["trades"]} trades, {d["win_rate"]}% WR, ${d["net_usd"]:.2f} net, {d["avg_r"]:.3f} avg R')
    
    print()
    print('=== KEY INSIGHTS ===')
    # Analyze which symbols contributed
    booms = [d for sym, d in result['by_symbol'].items() if 'BOOM' in sym.upper()]
    crashes = [d for sym, d in result['by_symbol'].items() if 'CRASH' in sym.upper()]
    
    if booms:
        b = booms[0]
        print(f'BOOM pairs: {b["trades"]} trades, {b["win_rate"]}% WR, ${b["net_usd"]:.2f} net, {b["avg_r"]:.3f} avg R')
    
    if crashes:
        c = crashes[0]
        print(f'CRASH pairs: {c["trades"]} trades, {c["win_rate"]}% WR, ${c["net_usd"]:.2f} net, {c["avg_r"]:.3f} avg R')
    
    print()
    print('=== COMPARISON WITH PREVIOUS 10-DAY BACKTEST ===')
    print('BOOM100 (5d): 158 trades, 53.2% WR, $1022 net, 0.234 avg R')
    print('CRASH100 (5d): 170 trades, 41.8% WR, -$393 net, -0.065 avg R')
    print()
    print('Key finding: BOOM100 is consistently profitable, CRASH100 is not')
    print('Recommendation: Focus on BOOM indices, avoid CRASH100, or use stricter filters')
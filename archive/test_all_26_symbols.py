import sys
sys.path.insert(0, '.')

from backtest_boom_crash import run_backtest

# ALL 26 working Deriv symbols
all_symbols = [
    # R-series (volatility indices)
    "R_10", "R_25", "R_50", "R_75", "R_100",
    # 1HZ volatility (1Hz)
    "1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V",
    # BOOM
    "BOOM1000", "BOOM900", "BOOM600", "BOOM500", "BOOM200", "BOOM100", "BOOM99", "BOOM50",
    # CRASH
    "CRASH1000", "CRASH900", "CRASH600", "CRASH500", "CRASH200", "CRASH100", "CRASH99", "CRASH50",
]

print(f'Running backtest on {len(all_symbols)} Deriv symbols (3 days each)\n')

result = run_backtest(
    symbols=all_symbols, 
    days=3,  # 3 days for speed
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
    print('=== COMPREHENSIVE VOLATILITY BACKTEST ===')
    print(f'Total trades: {m["total_trades"]}')
    print(f'Win rate: {m["win_rate"]}%')
    print(f'Profit factor: {m["profit_factor"]:.2f}')
    print(f'Net USD: ${m["net_usd"]:.2f}')
    print(f'Avg R: {m["avg_r"]:.3f}')
    print(f'Avg win: ${m["avg_win_usd"]:.2f}')
    print(f'Avg loss: ${m["avg_loss_usd"]:.2f}')
    print()
    
    print('=== BY CATEGORY ===')
    for cat_name, cat_symbols in [
        ("R-series", ["R_10", "R_25", "R_50", "R_75", "R_100"]),
        ("1HZ", ["1HZ10V", "1HZ25V", "1HZ50V", "1HZ75V", "1HZ100V"]),
        ("BOOM", ["BOOM1000", "BOOM900", "BOOM600", "BOOM500", "BOOM200", "BOOM100", "BOOM99", "BOOM50"]),
        ("CRASH", ["CRASH1000", "CRASH900", "CRASH600", "CRASH500", "CRASH200", "CRASH100", "CRASH99", "CRASH50"]),
    ]:
        cat_data = {k: v for k, v in result['by_symbol'].items() if k in cat_symbols}
        if cat_data:
            total = sum(d['trades'] for d in cat_data.values())
            net = sum(d['net_usd'] for d in cat_data.values())
            avg_r = sum(d['avg_r'] * d['trades'] for d in cat_data.values()) / total if total > 0 else 0
            wins = sum(1 for d in cat_data.values() if d['net_usd'] > 0)
            status = 'PROFITABLE' if net > 0 else 'LOOSING'
            print(f'{cat_name}: {total} trades, ${net:.2f} net, {avg_r:.3f} avg R, {wins}/{len(cat_data)} profitable symbols ({status})')
    
    print()
    print('=== ALL SYMBOLS RANKED BY NET PROFIT ===')
    for sym, d in sorted(result['by_symbol'].items(), key=lambda x: x[1]['net_usd'], reverse=True):
        status = 'YES' if d['net_usd'] > 0 else 'NO'
        print(f'{sym:<12} {d["trades"]:>4} trades {d["win_rate"]:>5.1f}% WR ${d["net_usd"]:>8.2f} net {d["avg_r"]:>6.3f} avg R {status}')
    
    print()
    print('=== TOP 10 RECOMMENDED ===')
    top10 = sorted(result['by_symbol'].items(), key=lambda x: x[1]['net_usd'], reverse=True)[:10]
    for i, (sym, d) in enumerate(top10, 1):
        print(f'{i}. {sym:<12} {d["trades"]:>3} trades {d["win_rate"]:>5.1f}% WR ${d["net_usd"]:>7.2f} net {d["avg_r"]:>6.3f} avg R')
else:
    print('No trades')
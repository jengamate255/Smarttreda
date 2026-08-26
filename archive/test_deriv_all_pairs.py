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
    print('=== ALL TRADES TABLE ===')
    import pandas as pd
    rows = [
        {
            "symbol": t.symbol,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl_usd": t.pnl_price,
            "risk_usd": t.risk_price,
            "r_multiple": t.r_multiple,
            "exit_reason": t.exit_reason,
            "spike_age": t.spike_age,
        }
        for t in result['trades']
    ]
    df = pd.DataFrame(rows)
    print(df.head(10).to_string())
    print(f'... ({len(df)} total trades)')
else:
    print('No trades')
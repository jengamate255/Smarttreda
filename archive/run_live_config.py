from backtest_boom_crash import run_backtest

# Test LIVE STRATEGY config (MIN_SPIKE_ATR=5.0, more conservative)
symbols = ['CRASH200', 'BOOM1000', 'BOOM500', 'BOOM99']
result = run_backtest(
    symbols=symbols, 
    days=10, 
    spike_threshold_atr=5.0,  # Live strategy uses 5.0
    min_spike_ticks=10,
    sl_atr_buffer=2.0,  # Live uses SL_ATR_BUFFER=2.0
    reward_risk_ratio=1.5,  # Live uses DEFAULT_RR=1.5
    max_spike_age=30
)

m = result['metrics']
if m.get('total_trades'):
    print('LIVE STRATEGY CONFIG (thr=5.0, buf=2.0, rr=1.5, age=30)')
    print('Trades:', m['total_trades'], 'WR:', str(m['win_rate']) + '%', 'PF:', round(m['profit_factor'], 2), 'Net: $' + str(round(m['net_usd'], 2)), 'Avg R:', round(m['avg_r'], 3))
    print('Avg win $' + str(round(m['avg_win_usd'], 2)) + ' / avg loss $' + str(round(m['avg_loss_usd'], 2)) + ' / avg hold ' + str(m['avg_hold_minutes']) + 'm')
    print('Exit reasons:', result['exit_reasons'])
    print()
    for sym, d in result['by_symbol'].items():
        print('  ' + sym + ': ' + str(d['trades']) + ' trades, ' + str(d['win_rate']) + '% WR, $' + str(round(d['net_usd'], 2)) + ' net, ' + str(round(d['avg_r'], 3)) + ' avg R')
else:
    print('No trades')
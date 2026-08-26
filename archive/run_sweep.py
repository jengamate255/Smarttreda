from backtest_boom_crash import sweep

symbols = ['CRASH200', 'BOOM1000', 'BOOM500', 'BOOM99']
result = sweep(symbols=symbols, days=10)

print('Sweep grid:', result['grid_size'], 'configs evaluated')
print()
print('Top 12 configs by avg R:')
for r in result['results'][:12]:
    p = r['params']
    print('  thr=' + str(p['spike_threshold_atr']) + ' buf=' + str(p['sl_atr_buffer']) + ' rr=' + str(p['reward_risk_ratio']) + ' age=' + str(p['max_spike_age']) + ': ' + str(r['total_trades']) + ' trades, WR ' + str(r['win_rate']) + '%, PF ' + str(round(r['profit_factor'], 2)) + ', $' + str(round(r['net_usd'], 2)) + ', ' + str(round(r['avg_r'], 3)) + ' R')
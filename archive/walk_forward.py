import sys
sys.path.insert(0, '.')

from backtest_boom_crash import run_backtest

# Walk-forward analysis on BOOM100 and CRASH100
# 3-day training, 1-day testing, rolling forward

symbols = ['BOOM100', 'CRASH100']
window = 3  # training days
step = 1    # step size
total_days = 15  # total days to analyze

print(f'=== WALK-FORWARD ANALYSIS ===')
print(f'Symbols: {", ".join(symbols)}')
print(f'Window: {window} days training, {step} day testing')
print(f'Total days: {total_days}')
print(f'{"="*60}')

results = []
for start_day in range(0, total_days - window, step):
    test_day = start_day + window
    print(f'\n--- Training: days {start_day+1} to {start_day+window} | Testing: day {test_day+1} ---')
    
    # Train on window days
    train_result = run_backtest(
        symbols=symbols,
        days=window,
        spike_threshold_atr=2.5,
        min_spike_ticks=8,
        max_hold_bars=120,
        fill_deadline_bars=30,
        min_bars_between_trades=30,
        reward_risk_ratio=1.5,
        sl_atr_buffer=2.0,
    )
    
    # Test on single day
    test_result = run_backtest(
        symbols=symbols,
        days=1,  # just 1 day
        spike_threshold_atr=2.5,
        min_spike_ticks=8,
        max_hold_bars=120,
        fill_deadline_bars=30,
        min_bars_between_trades=30,
        reward_risk_ratio=1.5,
        sl_atr_buffer=2.0,
    )
    
    m_train = train_result['metrics']
    m_test = test_result['metrics']
    
    print(f'  Training: {m_train["total_trades"]} trades, WR={m_train["win_rate"]}%, PF={m_train["profit_factor"]:.2f}, Net=${m_train["net_usd"]:.2f}')
    print(f'  Testing:  {m_test["total_trades"]} trades, WR={m_test["win_rate"]}%, PF={m_test["profit_factor"]:.2f}, Net=${m_test["net_usd"]:.2f}')
    
    results.append({
        'train_start': start_day + 1,
        'train_end': start_day + window,
        'test_day': test_day + 1,
        'train_trades': m_train['total_trades'],
        'train_wr': m_train['win_rate'],
        'train_pf': m_train['profit_factor'],
        'train_net': m_train['net_usd'],
        'test_trades': m_test['total_trades'],
        'test_wr': m_test['win_rate'],
        'test_pf': m_test['profit_factor'],
        'test_net': m_test['net_usd'],
    })

print(f'\n=== WALK-FORWARD SUMMARY ===')
print(f'{"Window":<5} {"Train Trades":<13} {"Train WR":<8} {"Train PF":<6} {"Train Net":<10} {"Test Trades":<13} {"Test WR":<8} {"Test PF":<6} {"Test Net":<10}')
print('-'*80)
for r in results:
    print(f'{r["train_start"]}-{r["train_end"]:<3} {r["train_trades"]:<13} {r["train_wr"]:<8.1f}% {r["train_pf"]:<6.2f} ${r["train_net"]:<10.2f} {r["test_trades"]:<13} {r["test_wr"]:<8.1f}% {r["test_pf"]:<6.2f} ${r["test_net"]:<10.2f}')
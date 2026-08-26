"""Parameter sweep for Boom/Crash strategy."""

from backtest_boom_crash import run_backtest

# Test different configurations
configs = [
    # (rr_ratio, sl_atr_buffer, entry_pct, spike_threshold)
    (1.5, 1.0, 0.5, 3.0),
    (1.5, 1.5, 0.5, 3.0),
    (1.5, 2.0, 0.5, 3.0),
    (2.0, 1.0, 0.5, 3.0),
    (2.0, 1.5, 0.5, 3.0),
    (2.0, 2.0, 0.5, 3.0),
    (2.5, 1.0, 0.5, 3.0),
    (2.5, 1.5, 0.5, 3.0),
    (3.0, 1.0, 0.5, 3.0),
    (3.0, 1.5, 0.5, 3.0),
    # Test different entry levels
    (2.0, 1.5, 0.382, 3.0),
    (2.0, 1.5, 0.618, 3.0),
    # Test higher spike threshold
    (2.0, 1.5, 0.5, 4.0),
    (2.0, 1.5, 0.5, 5.0),
]

print("Running parameter sweep...")
print(f"{'RR':<5} {'SL_ATR':<7} {'Entry':<6} {'Thresh':<7} {'Trades':<7} {'WR%':<6} {'AvgWin':<7} {'AvgLoss':<8} {'PF':<5} {'NetPnL':<8} {'AvgPnL':<7}")
print("-" * 100)

results = []

for rr, sl_atr, entry, thresh in configs:
    try:
        # Modify the backtest_boom_crash.py parameters temporarily
        import backtest_boom_crash as bt
        
        # We need to patch the detect_spike_at_index function
        # For now, just run with different parameters by importing and calling
        # Actually, let's just run the main function with modified params
        pass
    except Exception as e:
        print(f"Error: {e}")

print("Use the main backtest script with modified parameters for each test.")
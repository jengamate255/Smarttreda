import sys
from pathlib import Path
sys.path.insert(0, str(Path('.').resolve()))
from forex_rl.scripts.train_usdjpy_variants import run_variant
r = run_variant('USDJPY', 'B', 1.0, 1.2, 2.5, 0.05, n_bars=5000, n_steps=100000, seed=42)
print('=== FINAL B ===')
print(f"Trades: {r['trades']}")
print(f"Net P&L: {r['net_pnl']:.2f}")
print(f"Sharpe: {r['sharpe']:.3f}")
print(f"PF: {r['pf']:.3f}")
print(f"MaxDD: {r['max_dd']*100:.2f}%")
print(f"WR: {r['win_rate']:.1f}%")
import json
print(json.dumps({k: v for k,v in r.items() if k!='metrics'}, indent=2, default=str))

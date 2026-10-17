"""Train all 4 symbols with fixed reward."""
import sys
import json
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from forex_rl.scripts.train_fast import train_and_evaluate

logging.basicConfig(level=logging.WARNING)

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
results = {}

for sym in SYMBOLS:
    try:
        print(f"\n{'='*50}")
        print(f"Training {sym}...")
        print(f"{'='*50}")
        r = train_and_evaluate(sym, n_bars=5000, n_steps=100000, eval_episodes=10, seed=42)
        results[sym] = r
        print(f"\n{sym}: Sharpe={r['test_metrics'].get('sharpe_ratio', 0):.3f}, "
              f"PF={r['test_metrics'].get('profit_factor', 0):.3f}, "
              f"DD={r['test_metrics'].get('max_drawdown', 0):.2f}%, "
              f"Trades={r['test_metrics'].get('num_trades', 0)}")
    except Exception as e:
        print(f"ERROR: {e}")
        results[sym] = {"error": str(e)}

# Summary
print("\n" + "=" * 60)
print("TRAINING SUMMARY")
print("=" * 60)
for sym, r in results.items():
    if "error" in r:
        print(f"  {sym}: ERROR - {r['error']}")
    else:
        m = r.get("test_metrics", {})
        print(f"  {sym}: Sharpe={m.get('sharpe_ratio', 0):.3f} PF={m.get('profit_factor', 0):.3f} "
              f"DD={m.get('max_drawdown', 0):.2f}% Trades={m.get('num_trades', 0)} "
              f"WinRate={m.get('win_rate', 0):.1f}%")

# Save
with open("forex_rl/models/checkpoints/all_results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
print("\nResults saved to forex_rl/models/checkpoints/all_results.json")

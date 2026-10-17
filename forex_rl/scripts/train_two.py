"""Train EURUSD and GBPUSD sequentially."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from forex_rl.scripts.train_fast import train_and_evaluate

results = {}
for sym in ["EURUSD", "GBPUSD"]:
    try:
        print(f"\n{'='*50}")
        print(f"Training {sym}...")
        print(f"{'='*50}")
        r = train_and_evaluate(sym, n_bars=5000, n_steps=100000, eval_episodes=10, seed=42)
        results[sym] = r
    except Exception as e:
        print(f"ERROR: {e}")
        results[sym] = {"error": str(e)}

print("\n" + "=" * 60)
print("TRAINING SUMMARY — EURUSD & GBPUSD")
print("=" * 60)
for sym, r in results.items():
    if "error" in r:
        print(f"  {sym}: ERROR - {r['error']}")
    else:
        m = r.get("test_metrics", {})
        print(f"  {sym}: Trades={m.get('total_trades', m.get('num_trades', 0))} "
              f"Sharpe={m.get('sharpe_ratio', 0):.3f} "
              f"PF={m.get('profit_factor', 0):.3f} "
              f"DD={m.get('max_drawdown', 0):.2f}% "
              f"WinRate={m.get('win_rate', 0):.1f}%")

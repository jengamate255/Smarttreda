"""Train all 4 symbols with v2 pipeline."""
import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from forex_rl.scripts.train_v2 import train_and_evaluate

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
        m = r.get("test_metrics", {})
        print(f"\n{sym}: Trades={m.get('total_trades', 0)}, Sharpe={m.get('sharpe_ratio', 0):.3f}, "
              f"PF={m.get('profit_factor', 0):.3f}, DD={m.get('max_drawdown', 0):.2f}%, "
              f"WR={m.get('win_rate', 0):.1f}%")
    except Exception as e:
        import traceback
        print(f"ERROR: {e}")
        traceback.print_exc()
        results[sym] = {"error": str(e)}

# Summary
print("\n" + "=" * 60)
print("V2 TRAINING SUMMARY")
print("=" * 60)
for sym, r in results.items():
    if "error" in r:
        print(f"  {sym}: ERROR - {r['error']}")
    else:
        m = r.get("test_metrics", {})
        print(f"  {sym}: Trades={m.get('total_trades', 0):>3d}  "
              f"Sharpe={m.get('sharpe_ratio', 0):>7.3f}  "
              f"PF={m.get('profit_factor', 0):>6.3f}  "
              f"DD={m.get('max_drawdown', 0):>5.2f}%  "
              f"WR={m.get('win_rate', 0):>5.1f}%  "
              f"P&L=${m.get('net_profit', 0):>8.2f}")

out = Path("forex_rl/models/checkpoints")
out.mkdir(parents=True, exist_ok=True)
with open(out / "v2_all_results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)
print("\nResults saved.")

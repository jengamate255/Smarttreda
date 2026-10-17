import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
import logging
logging.basicConfig(level=logging.WARNING)

from forex_rl.scripts.train_v2 import train_and_evaluate
import json

for sym in ["USDJPY", "XAUUSD"]:
    try:
        print(f"\n{'='*50}")
        print(f"Training {sym}...")
        r = train_and_evaluate(sym, n_bars=5000, n_steps=100000, eval_episodes=10, seed=42)
        m = r.get("test_metrics", {})
        print(f"\n{sym}: Trades={m.get('total_trades', 0)}, Sharpe={m.get('sharpe_ratio', 0):.3f}, "
              f"PF={m.get('profit_factor', 0):.3f}, DD={m.get('max_drawdown', 0):.2f}%, "
              f"WR={m.get('win_rate', 0):.1f}%, P&L=${m.get('net_profit', 0):.2f}")
    except Exception as e:
        import traceback
        print(f"ERROR: {e}")
        traceback.print_exc()

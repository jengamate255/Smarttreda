import sys
sys.path.insert(0, "E:/Project/Mt5 Ea")

import MetaTrader5 as mt5
from mt5_mcp.forex_dojo import ForexDojo, DojoConfig

mt5.initialize()
print("MT5 Terminal Connected")

config = DojoConfig(
    symbol="EURUSD",
    timeframe="H1",
    episodes=100,
    max_steps=500,
    initial_balance=10000.0,
    multi_timeframe=True,
)

dojo = ForexDojo(config)
print("Training 100 episodes with inactivity penalty...")
progress = dojo.train()

print(f"\n=== RESULTS ===")
print(f"Skill: {progress.skill_level} ({progress.skill_score:.0f}/100)")
print(f"Win Rate: {progress.overall_win_rate:.0%}")
print(f"Total P&L: ${progress.overall_pnl:.2f}")
print(f"Max DD: {progress.overall_max_dd:.1f}%")
print(f"Trades: {progress.total_trades}")
print(f"Sharpe: {progress.sharpe_ratio:.2f}")
print(f"Profit Factor: {progress.profit_factor:.2f}")
print(f"Test Win Rate: {progress.test_win_rate:.0%}")
print(f"Test P&L: ${progress.test_pnl:.2f}")
print(f"Test Sharpe: {progress.test_sharpe:.2f}")
print(f"Lessons: {progress.lessons_learned}")

mt5.shutdown()

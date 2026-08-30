"""Smoke test for reward and risk modules."""
from forex_rl.src.environment.reward import (
    Portfolio, TradeResult, PortfolioState, MarketState,
    RewardConfig, SimpleProfitReward, RiskAdjustedReward,
    SharpeReward, ParetoReward, TradeSide, ExitReason, MarketRegime,
    create_reward,
)
from forex_rl.src.risk.risk_manager import RiskManager, RiskConfig
from forex_rl.src.risk.position_sizing import (
    fixed_fractional, kelly_criterion, anti_martingale,
    volatility_adjusted, atr_position_size,
)
from forex_rl.src.risk import PositionSizer

# --- Portfolio test ---
p = Portfolio(initial_balance=10000)
p.open_position("EURUSD", TradeSide.BUY, 0.1, 1.1000, 0, sl=1.0950, tp=1.1150)
r = p.close_position(0, 1.1100, 50, ExitReason.TAKE_PROFIT)
assert abs(p.equity - 10000.001) < 1e-6, f"Expected ~10000.001, got {p.equity}"
assert abs(r.pnl - 0.001) < 1e-9, f"Expected pnl~0.001, got {r.pnl}"
assert p.total_trades == 1
print(f"Portfolio: equity={p.equity:.2f}, trades={p.total_trades} OK")

# --- Stats ---
stats = p.stats()
assert "equity" in stats
assert "win_rate" in stats
print(f"Stats keys: {list(stats.keys())} OK")

# --- TradeResult ---
ts = TradeResult(
    entry_price=1.1, exit_price=1.11, side=TradeSide.BUY,
    volume=0.1, bars_held=50, pnl=100, pnl_pct=0.01,
    reason=ExitReason.TAKE_PROFIT,
)
ps = PortfolioState(
    equity=10100, balance=10100, peak_equity=10100,
    drawdown=0.0, max_drawdown=0.005, total_trades=10,
    wins=6, losses=4, open_positions=0,
)
ms = MarketState(
    regime=MarketRegime.TREND_UP, volatility_percentile=0.5,
    atr=0.001, trend_direction=0.8,
)

# --- All reward functions ---
for name in ["simple", "risk_adjusted", "sharpe", "pareto"]:
    rf = create_reward(name)
    reward = rf.compute(ts, ps, ms)
    print(f"{name:15s} reward={reward:.6f}")

# --- Risk manager ---
rm = RiskManager(RiskConfig())
state = {
    "equity": 10000, "balance": 10000, "drawdown": 0.01,
    "max_drawdown": 0.02, "total_trades": 5, "wins": 3, "losses": 2,
    "open_positions": 1, "daily_pnl": -50, "open_risk": 30,
    "consecutive_losses": 0, "bars_since_last_trade": 10,
}
trade = {
    "symbol": "EURUSD", "side": "BUY", "entry_price": 1.1000,
    "sl_price": 1.0950, "tp_price": 1.1150, "lots": 0.1,
    "tick_value": 1.0, "tick_size": 0.0001, "spread_pips": 1.2,
}
ok, reason = rm.can_trade(state, trade)
assert ok, f"Expected approved, got: {reason}"
print(f"RiskManager approved: {reason} OK")

# --- Rejection: daily DD ---
state2 = dict(state, daily_pnl=-250)
ok2, reason2 = rm.can_trade(state2, trade)
assert not ok2, "Expected daily DD rejection"
print(f"Daily DD block: {reason2} OK")

# --- Rejection: max positions ---
state3 = dict(state, open_positions=3)
ok3, reason3 = rm.can_trade(state3, trade)
assert not ok3, "Expected max positions rejection"
print(f"Max positions: {reason3} OK")

# --- Rejection: open risk ---
state4 = dict(state, open_risk=150)
ok4, reason4 = rm.can_trade(state4, trade)
assert not ok4, "Expected open risk rejection"
print(f"Open risk block: {reason4} OK")

# --- Rejection: bad SL direction ---
trade_bad = dict(trade, sl_price=1.1010)
ok5, reason5 = rm.can_trade(state, trade_bad)
assert not ok5, "Expected SL direction rejection"
print(f"SL direction: {reason5} OK")

# --- Position sizing ---
lots = rm.compute_position_size(10000, 1.1, 1.095)
print(f"Position size: {lots} lots OK")

print(f"fixed_fractional: {fixed_fractional(10000, 0.005, 0.005, 1.0)}")
print(f"kelly_criterion:  {kelly_criterion(0.6, 200, 100)}")
print(f"anti_martingale:   {anti_martingale(10000, 0.005, 3)}")
print(f"volatility_adj:   {volatility_adjusted(0.001, 0.001, 0.002, 1.0)}")
print(f"atr_position_size: {atr_position_size(10000, 0.005, 0.001, 2.0, 1.0)}")
print(f"PositionSizer:     {PositionSizer.fixed_fractional(10000, 0.005, 0.005, 1.0)}")

# --- Validate exit ---
pos = {"entry_price": 1.1, "side": "BUY", "sl": 1.095, "tp": 1.115, "volume": 0.1, "bars_held": 50}
assert rm.validate_exit(pos, 1.095, "sl"), "Valid SL exit"
assert rm.validate_exit(pos, 1.115, "tp"), "Valid TP exit"
assert not rm.validate_exit(pos, 1.100, "sl"), "Invalid SL exit"
print("Validate exit OK")

print("\n=== ALL TESTS PASSED ===")

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest import Backtester, BacktestParams, load_frames
from mt5_mcp.client import get_client

client = get_client()
client.ensure_connected()

p = BacktestParams(
    symbol="EURUSD",
    trend_timeframe="H1",
    zone_timeframes=["M15", "H1"],
    confirmation_timeframe="M15",
    trend_fast_ema=20,
    trend_slow_ema=100,
    min_zone_score=35.0,
    min_trade_score=0.0,
    min_confidence_percent=0.0,
    lookback=200,
    risk_percent=0.25,
    tp_min=30.0,
    tp_max=50.0,
    sl_min=15.0,
    sl_max=30.0,
    min_rr=1.5,
    spread_points=15.0,
    start_balance=10000.0,
    bars=6000,
    timeout_bars=60,
)

tfs = sorted({p.trend_timeframe, p.confirmation_timeframe, *p.zone_timeframes})
frames = load_frames(p.symbol, tfs, p.bars)

info = {}
try:
    info = client.symbol_info(p.symbol)
except:
    pass

bt = Backtester(p, frames, info)
result = bt.run()

print("=== EURUSD HF Backtest (Jun-Jul 2026) ===")
print("Trades:", result.n_trades)
print("Win rate:", result.win_rate, "%")
print("Profit factor:", result.profit_factor)
print("Return:", result.total_return_pct, "%")
print("Max DD:", result.max_drawdown_pct, "%")
print("Expectancy:", result.expectancy)
for t in result.trades:
    print(t["entry_time"], t["action"], "entry=", t["entry"], "exit=", t["exit_time"], "pnl=", t["pnl"], "reason=", t["reason"], "RR=", t["rr"])
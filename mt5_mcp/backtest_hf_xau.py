"""HF EA backtest on XAUUSD - matches PriceAction_SD_Trader_HF logic (no trade-score gates)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest import Backtester, BacktestParams, load_frames
from client import get_client

client = get_client()
client.ensure_connected()

# HF params matching the EA
p = BacktestParams(
    symbol="XAUUSD",
    trend_timeframe="H1",
    zone_timeframes=["M15", "H1"],
    confirmation_timeframe="M15",
    trend_fast_ema=20,
    trend_slow_ema=100,
    min_zone_score=35.0,
    min_trade_score=0.0,      # NO score gate (HF EA has none)
    min_confidence_percent=0.0, # NO confidence gate
    lookback=200,
    risk_percent=0.25,
    tp_min=30.0,
    tp_max=50.0,
    sl_min=15.0,
    sl_max=30.0,
    min_rr=1.5,
    spread_points=15.0,
    start_balance=10000.0,
    bars=8000,
    timeout_bars=60,
)

print(f"Loading {p.symbol} frames for {p.bars} bars...")
tfs = sorted({p.trend_timeframe, p.confirmation_timeframe, *p.zone_timeframes})
frames = load_frames(p.symbol, tfs, p.bars)

info = {}
try:
    info = client.symbol_info(p.symbol)
except:
    pass

print("Running backtest...")
bt = Backtester(p, frames, info)
result = bt.run()

print(f"\n=== {p.symbol} HF Backtest ===")
print(f"Trades: {result.n_trades}")
print(f"Win rate: {result.win_rate:.1f}%")
print(f"Profit factor: {result.profit_factor:.2f}")
print(f"Return: {result.total_return_pct:+.2f}%")
print(f"Max DD: {result.max_drawdown_pct:.2f}%")
print(f"Expectancy: {result.expectancy:+.2f}")
print(f"Start: {result.start} -> End: {result.end}")
print(f"Avg RR: {result.avg_rr:.2f}")

for t in result.trades:
    print(f"  {t.entry_time} {t.action} entry={t.entry:.2f} exit={t.exit_time} pnl={t.pnl:+.2f} reason={t.reason}")
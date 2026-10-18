"""Limited backtest of SPX500 with H4/H1 zone timeframe."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

from mt5_mcp.backtest import Backtester, BacktestParams, load_frames
from mt5_mcp.client import get_client

params = BacktestParams(
    symbol="SPX500",
    zone_timeframes=["H4", "H1"],  # H4+H1 zones
    trend_timeframe="H4",
    confirmation_timeframe="H1",
    sl_min=20.0,
    sl_max=30.0,
    tp_min=80.0,
    tp_max=100.0,
    min_rr=3.0,
    min_zone_score=40.0,  # z40
    risk_percent=0.5,  # 0.5%
    bars=2000,  # Limited to avoid timeout
    use_trailing_stop=True,
)

print(f"Running backtest: {params.symbol}")
print(f"Zone TFs: {params.zone_timeframes}")
print(f"Zone level: z{params.min_zone_score}")
print(f"SL: {params.sl_min}-{params.sl_max} pips, TP: {params.tp_min}-{params.tp_max} pips")
print(f"Min RR: {params.min_rr}")
print(f"Bars: {params.bars}")
print()

# Load frames
print("Loading frames...")
client = get_client()
frames = load_frames(params.symbol, params.zone_timeframes + [params.trend_timeframe, params.confirmation_timeframe], params.bars, use_cache=False)
print(f"Loaded {len(frames)} timeframes: {list(frames.keys())}")

# Get symbol info
info = client.symbol_info(params.symbol)
if not info:
    print(f"Symbol {params.symbol} not found")
    sys.exit(1)

# Run backtest
print("\nRunning backtest...")
bt = Backtester(params, frames, info)
result = bt.run()

print("=== BACKTEST RESULTS ===")
print(f"Total trades: {result.n_trades}")
print(f"Wins: {result.wins} | Losses: {result.losses}")
print(f"Win rate: {result.win_rate:.1%}")
print(f"Profit factor: {result.profit_factor:.2f}")
print(f"Net PnL: ${result.net_pnl:.2f}")
print(f"Total return: {result.total_return_pct:.2f}%")
print(f"Average RR: {result.avg_rr:.2f}")
print(f"Max drawdown: {result.max_drawdown_pct:.2f}%")
print(f"Sharpe ratio: {result.sharpe_ratio:.2f}")
print(f"Sortino ratio: {result.sortino_ratio:.2f}")
print()

if result.trades:
    print("=== TRADE LOG (first 10) ===")
    for i, trade in enumerate(result.trades[:10], 1):
        print(f"{i}. {trade.get('action', 'N/A')} @ {trade.get('entry', 0):.2f} | SL: {trade.get('sl', 0):.2f} | TP: {trade.get('tp', 0):.2f} | PnL: ${trade.get('pnl', 0):.2f}")

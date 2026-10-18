import os
os.environ["MT5_MCP_ACCOUNT"] = "DERIV"

from datetime import datetime

from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import get_settings
from mt5_mcp.backtest import Backtester, BacktestParams, load_frames

s = get_settings()
deriv_cfg = s.deriv_mt5
client = MT5Client(deriv_cfg)

if not client.connect():
    print("Failed to connect")
    exit(1)

sym = 'Boom 1000 Index'
print("=" * 60)
print("BOOM 1000 INDEX - ADJUSTED STRATEGY BACKTEST (V75-style)")
print("=" * 60)

if not client.symbol_exists(sym):
    print(f"Symbol {sym} does not exist")
    client.shutdown()
    exit(1)

# Load frames for backtest
frames = load_frames(sym, ['H1', 'H4'], 5000, use_cache=True, allow_mt5=True)
if not frames or 'H1' not in frames or 'H4' not in frames:
    print("Failed to load frames")
    client.shutdown()
    exit(1)

print(f"Loaded frames: H1={len(frames['H1'])}, H4={len(frames['H4'])} bars")

info = client.symbol_info(sym)

# Adjusted params (same tuning family as the V75 run: zone 50 + EMA filter)
params = BacktestParams(
    symbol=sym,
    trend_timeframe='H4',
    zone_timeframes=['H1', 'H4'],
    confirmation_timeframe='H1',
    min_zone_score=50,
    min_trade_score=50,
    min_confidence_percent=60,
    risk_percent=0.5,
    tp_min=80.0,
    tp_max=100.0,
    sl_min=20.0,
    sl_max=30.0,
    min_rr=3.0,
    spread_points=15.0,
    slippage_points=1.0,
    commission_per_lot=7.0,
    start_balance=10000.0,
    bars=5000,
    timeout_bars=100,
    use_trailing_stop=True,
    trailing_arm_r=1.0,
    trailing_atr_mult=1.2,
    use_partial_tp=True,
    partial_tp_r=1.0,
    partial_tp_pct=0.5,
    use_adaptive_timeout=True,
    adaptive_timeout_atr=0.3,
    adaptive_timeout_bars=80,
    use_vol_spread=True,
    vol_spread_base=1.0,
    vol_spread_max=3.0,
    max_consecutive_losses=3,
    cooldown_bars=20,
    regime_filter='ema',
    regime_adx_threshold=25.0,
    regime_atr_high_pct=80.0,
    regime_atr_low_pct=20.0,
    sizing='fixed',
    kelly_fraction=0.5,
    correlation_group=''
)

bt = Backtester(params, frames, info)
result = bt.run()

print("=" * 60)
print("BACKTEST RESULTS - ZONE SCORE 50 + EMA FILTER")
print("=" * 60)
print(f"Trades: {result.n_trades}")
print(f"Win Rate: {result.win_rate:.1f}%")
print(f"Net P&L: ${result.net_pnl:.2f}")
print(f"Profit Factor: {result.profit_factor:.2f}")
print(f"Max DD: {result.max_drawdown_pct:.2f}%")
print(f"Expectancy: ${result.expectancy:.2f}")
print(f"Total Return: {result.total_return_pct:.2f}%")

if result.trades:
    print("\nTrade Details:")
    for i, trade in enumerate(result.trades):
        dur_h = 0.0
        try:
            t0 = datetime.fromisoformat(str(trade.get("entry_time")))
            t1 = datetime.fromisoformat(str(trade.get("exit_time")))
            dur_h = (t1 - t0).total_seconds() / 3600.0
        except (TypeError, ValueError):
            pass
        print(
            f"  {i + 1}. {trade.get('action')} "
            f"Entry={float(trade.get('entry') or 0.0):.5f} "
            f"Exit={float(trade.get('exit_price') or 0.0):.5f} "
            f"P&L=${float(trade.get('pnl') or 0.0):.2f} "
            f"Dur={dur_h:.1f}h Reason={trade.get('reason', '')}"
        )

client.shutdown()
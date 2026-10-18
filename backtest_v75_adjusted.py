import os
os.environ["MT5_MCP_ACCOUNT"] = "DERIV"

from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import get_settings
from mt5_mcp.backtest import Backtester, BacktestParams, load_frames

s = get_settings()
client = MT5Client(s.deriv_mt5)

if not client.connect():
    print("Failed to connect")
    exit(1)

sym = 'Volatility 75 Index'
frames = load_frames(sym, ['H1', 'H4'], 5000, use_cache=True, allow_mt5=True)
if not frames or 'H1' not in frames or 'H4' not in frames:
    print("Failed to load frames")
    client.shutdown()
    exit(1)

info = client.symbol_info(sym)

base = dict(
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
    regime_adx_threshold=25.0,
    regime_atr_high_pct=80.0,
    regime_atr_low_pct=20.0,
    sizing='fixed',
    kelly_fraction=0.5,
    correlation_group='',
)

for label, overrides in [
    ("ema filter", dict(regime_filter='ema')),
    ("no regime filter", dict(regime_filter='')),
    ("no filter, zone 40", dict(regime_filter='', min_zone_score=40)),
]:
    p = BacktestParams(**{**base, **overrides})
    r = Backtester(p, frames, info).run()
    print(
        f"{label:22s} -> trades={r.n_trades:3d} WR={r.win_rate:5.1f}% "
        f"PF={r.profit_factor:5.2f} net=${r.net_pnl:9.2f} DD={r.max_drawdown_pct:5.2f}% "
        f"errors={list(r.errors)[:3]}"
    )

client.shutdown()
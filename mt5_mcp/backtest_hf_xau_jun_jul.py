"""HF EA backtest on XAUUSD for 2026-06-01 to 2026-07-31 (~6000 M15 bars)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest import Backtester, BacktestParams, load_frames
from mt5_mcp.client import get_client

client = get_client()
client.ensure_connected()

# HF params matching PriceAction_SD_Trader_HF EA
p = BacktestParams(
    symbol="XAUUSD",
    trend_timeframe="H1",
    zone_timeframes=["M15", "H1"],
    confirmation_timeframe="M15",
    trend_fast_ema=20,
    trend_slow_ema=100,
    min_zone_score=35.0,
    min_trade_score=0.0,      # NO score gate (HF EA has none)
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
    bars=6000,   # ~61 days of M15 (Jun-Jul 2026)
    timeout_bars=60,
)

print(f"Loading {p.symbol} frames for {p.bars} M15 bars (Jun-Jul 2026)...")
tfs = sorted({p.trend_timeframe, p.confirmation_timeframe, *p.zone_timeframes})
frames = load_frames(p.symbol, tfs, p.bars)

info = {}
try:
    info = client.symbol_info(p.symbol)
except:
    pass

print("Running backtest... (this may take 3-5 minutes)")
bt = Backtester(p, frames, info)
result = bt.run()

print(f"\n=== XAUUSD HF Backtest (Jun-Jul 2026) ===")
print(f"Trades: {result.n_trades}")
print(f"Win rate: {result.win_rate:.1f}%")
print(f"Profit factor: {result.profit_factor:.2f}")
print(f"Return: {result.total_return_pct:+.2f}%")
print(f"Max DD: {result.max_drawdown_pct:.2f}%")
print(f"Expectancy: {result.expectancy:+.2f}")
print(f"Avg RR: {result.avg_rr:.2f}")
print(f"Period: {result.start} -> {result.end}")

for t in result.trades:
    print(f"  {t.entry_time} {t.action} entry={t.entry:.2f} exit={t.exit_time} pnl={t.pnl:+.2f} reason={t.reason}")

if result.n_trades == 0:
    print("\nNo trades - checking gate counts...")
    # Quick gate check
    base = frames["M15"]
    base_times = base["time"].values.astype("datetime64[s]")
    warmup = 500
    n_gate = {"trend_bull": 0, "trend_bear": 0, "zone": 0, "confirm": 0, "both": 0}
    for i in range(warmup, len(base_times), 1):
        t = base_times[i]
        trend_pos = int(np.searchsorted(
            frames["H1"]["time"].values.astype("datetime64[s]"), t, side="right"))
        if trend_pos < 100: continue
        # trend
        from strategy import analyze_trend
        trend = analyze_trend(frames["H1"].iloc[:trend_pos], 20, 100)
        if trend.direction == "bullish":
            n_gate["trend_bull"] += 1; action, zone_type = "BUY", "demand"
        elif trend.direction == "bearish":
            n_gate["trend_bear"] += 1; action, zone_type = "SELL", "supply"
        else:
            continue
        # zone
        price = float(base.iloc[i].close)
        best = None
        for tf in ["M15", "H1"]:
            frame = frames[tf]
            pos = int(np.searchsorted(frame["time"].values.astype("datetime64[s]"), t, side="right"))
            tail = frame.iloc[max(0, pos-200):pos]
            if len(tail) < 60: continue
            from supply_demand import detect_zones
            zones = detect_zones(tail, min_strength=35.0)
            for zone in zones:
                atr_val = float(atr(tail).iloc[-1]) or 1e-9
                chosen, _ = _select_zone([zone], "demand" if action=="BUY" else "supply", price, atr_val, 35.0)
                if chosen:
                    best = chosen; break
            if best: break
        if best:
            n_gate["zone"] += 1
            # confirm
            from price_action import detect_patterns
            from strategy import _best_confirmation
            conf_tail = base.iloc[:int(np.searchsorted(base["time"].values.astype("datetime64[s]"), t, side="right"))]
            if len(conf_tail) >= 60:
                patterns = detect_patterns(conf_tail, check_last=1)
                conf = _best_confirmation(patterns, trend.direction)
                if conf:
                    n_gate["confirm"] += 1
                    n_gate["both"] += 1
    print(f"Gate counts (per bar): trend_bull={n_gate['trend_bull']} trend_bear={n_gate['trend_bear']} zone={n_gate['zone']} confirm={n_gate['confirm']} both={n_gate['both']}")
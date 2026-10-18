import sys
sys.path.insert(0, "E:/Project/Mt5 Ea/mt5_mcp")
from mt5_mcp.backtest import BacktestParams, Backtester, load_frames, _variant_tfs
from mt5_mcp.client import get_client

c = get_client()
c.ensure_connected()

for name, kwargs in [
    ("lowconf", dict(min_zone_score=40, min_trade_score=40, min_confidence_percent=40, max_confidence_percent=74.99, risk_percent=0.10)),
    ("baseline", dict(min_zone_score=40, min_trade_score=70, min_confidence_percent=60, risk_percent=0.5)),
    ("open", dict(min_zone_score=40, min_trade_score=10, min_confidence_percent=10, risk_percent=0.5)),
]:
    p = BacktestParams(symbol="EURUSD", min_rr=3.0, bars=5000, **kwargs)
    info = c.symbol_info("EURUSD") or {}
    frames = load_frames("EURUSD", _variant_tfs(p), p.bars, use_cache=True, allow_mt5=False)
    print(name, "frames", {k: len(v) for k, v in frames.items()}, "info?", bool(info))
    bt = Backtester(p, frames, info)
    r = bt.run()
    print(name, "errors", r.errors, "trades", r.n_trades, "pnl", r.net_pnl, "start", r.start, "end", r.end)

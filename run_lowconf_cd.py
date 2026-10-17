import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, "E:/Project/Mt5 Ea/mt5_mcp")
sys.path.insert(0, "E:/Project/Mt5 Ea")
from run_lowconf_experiment import VARIANTS, SYMBOLS, OUT_DIR, make_params
from mt5_mcp.backtest import Backtester, load_frames, _variant_tfs, save_result
from mt5_mcp.client import get_client

client = get_client()
client.ensure_connected()
tags = {"C_low_micro", "D_vlow_micro"}
for v in VARIANTS:
    if v["tag"] not in tags:
        continue
    out = OUT_DIR / v["tag"]
    out.mkdir(parents=True, exist_ok=True)
    for s in SYMBOLS:
        p = make_params(s, v)
        # exact filename from _variant_name
        from mt5_mcp.backtest import _variant_name
        path = out / f"{_variant_name(p)}.json"
        if path.exists():
            print(f"skip {v['tag']} {s}", flush=True)
            continue
        info = client.symbol_info(s) or {}
        t0 = time.perf_counter()
        try:
            frames = load_frames(s, _variant_tfs(p), p.bars, use_cache=True, allow_mt5=False)
            bt = Backtester(p, frames, info)
            r = bt.run()
            save_result(r, out)
            trades = r.trades or []
            conf = round(sum(t.get("confidence", 0) for t in trades) / len(trades), 1) if trades else 0
            print(
                f'{v["tag"]:14} {s:7} tr={r.n_trades:3d} wr={r.win_rate:5.1f}% '
                f"pf={r.profit_factor:5.2f} pnl={r.net_pnl:+9.2f} "
                f"dd={r.max_drawdown_pct:5.2f}% conf={conf} [{time.perf_counter()-t0:.1f}s]",
                flush=True,
            )
        except Exception:
            traceback.print_exc()
            print(f"FAILED {v['tag']} {s}", flush=True)
print("DONE")

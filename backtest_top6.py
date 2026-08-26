"""5-day backtest on the 6 recommended synthetic pairs."""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest_boom_crash import run_backtest

SYMBOLS = [
    "BOOM200", "BOOM100", "BOOM900",
    "CRASH1000", "CRASH200", "CRASH500",
]

result = run_backtest(
    symbols=SYMBOLS,
    days=5,
    spike_threshold_atr=2.5,
    min_spike_ticks=8,
    max_hold_bars=120,
    fill_deadline_bars=30,
    min_bars_between_trades=30,
    reward_risk_ratio=1.5,
    sl_atr_buffer=2.0,
)

m = result.get("metrics", {})
by_sym = result.get("by_symbol", {})

if not m.get("total_trades"):
    print("No trades — check MT5 terminals")
    if result.get("errors"):
        for e in result["errors"][:10]:
            print("  ", e)
    sys.exit(1)

print("=" * 60)
print("TOP 6 SYMBOLS — 5-DAY BACKTEST")
print("=" * 60)
print()
print(f"Total trades : {m['total_trades']}")
print(f"Win rate     : {m['win_rate']:.1f}%")
print(f"Profit factor: {m['profit_factor']:.2f}")
print(f"Net          : ${m['net_usd']:+.2f}")
print(f"Avg R        : {m['avg_r']:+.3f}")
print(f"Avg win      : ${m.get('avg_win_usd', 0):+.2f}")
print(f"Avg loss     : ${m.get('avg_loss_usd', 0):+.2f}")
print(f"Avg hold     : {m.get('avg_hold_minutes', 0)} min")
print(f"Max drawdown : {m.get('max_drawdown_usd', 0):.2f}")
print()

print("--- By symbol (ranked by net) ---")
for sym, d in sorted(by_sym.items(), key=lambda kv: kv[1].get("net_usd", 0), reverse=True):
    trades = d.get("trades", 0)
    wr = d.get("win_rate", 0)
    net = d.get("net_usd", 0)
    r = d.get("avg_r", 0)
    pf = d.get("profit_factor", 0)
    tag = "TRADE" if net > 0 and r > 0.1 else "WATCH"
    print(f"  {sym:<12} {trades:>4} tr  {wr:>5.1f}% WR  ${net:>+9.2f}  {r:>+7.3f} R  PF {pf:>5.2f}  [{tag}]")

print()
import json, datetime
out = ROOT / "backtest" / f"top6_5day_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, default=str)
print(f"Saved to {out}")

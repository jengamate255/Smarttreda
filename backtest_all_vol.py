"""Backtest ALL synthetic volatility + boom/crash pairs.

Fetches M1 history from the live MT5 terminals, runs the spike-drift
strategy on each symbol, and prints a ranked summary.

Usage:  python backtest_all_vol.py              # 5 days, default params
        python backtest_all_vol.py --days 14    # 14 days
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest_boom_crash import run_backtest
from mt5_mcp.common import BOOM_CRASH_SYMBOLS, VOLATILITY_SYMBOLS


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtest all synthetic volatility + boom/crash pairs")
    ap.add_argument("--days", type=int, default=5, help="lookback in days (default 5)")
    ap.add_argument("--spike-thr", type=float, default=2.5, help="spike threshold in ATR units")
    ap.add_argument("--min-ticks", type=int, default=8)
    ap.add_argument("--rr", type=float, default=1.5)
    ap.add_argument("--sl-atr", type=float, default=2.0)
    args = ap.parse_args()

    # Map volatility MT5 names to API-style for the backtest engine.
    # backtest_boom_crash expects BOOM/Crash names; Volatility names are
    # already MT5-style, so pass them through.  The mt5_symbol helper
    # handles both.
    all_symbols = list(BOOM_CRASH_SYMBOLS) + list(VOLATILITY_SYMBOLS)

    print(f"Backtesting {len(all_symbols)} symbols ({len(BOOM_CRASH_SYMBOLS)} boom/crash + {len(VOLATILITY_SYMBOLS)} volatility) over {args.days} days\n")

    result = run_backtest(
        symbols=all_symbols,
        days=args.days,
        spike_threshold_atr=args.spike_thr,
        min_spike_ticks=args.min_ticks,
        max_hold_bars=120,
        fill_deadline_bars=30,
        min_bars_between_trades=30,
        reward_risk_ratio=args.rr,
        sl_atr_buffer=args.sl_atr,
    )

    m = result.get("metrics", {})
    by_sym = result.get("by_symbol", {})

    if not m.get("total_trades"):
        print("No trades generated — check that MT5 terminals are running and symbols have history.")
        if result.get("errors"):
            for e in result["errors"][:10]:
                print("  ", e)
        return 1

    print("=" * 64)
    print("COMPLETE SYNTHETIC BACKTEST")
    print("=" * 64)
    print(f"Period: {args.days} days  |  spike>={args.spike_thr} ATR  min_ticks={args.min_ticks}  RR={args.rr}  SL={args.sl_atr} ATR")
    print()
    print(f"Total trades : {m['total_trades']}")
    print(f"Win rate     : {m['win_rate']:.1f}%")
    print(f"Profit factor: {m['profit_factor']:.2f}")
    print(f"Net          : ${m['net_usd']:+.2f}")
    print(f"Avg R        : {m['avg_r']:+.3f}")
    print(f"Avg win      : ${m.get('avg_win_usd', 0):+.2f}")
    print(f"Avg loss     : ${m.get('avg_loss_usd', 0):+.2f}")
    print(f"Avg hold     : {m.get('avg_hold_minutes', 0)} min")
    print()

    print("--- By symbol (ranked by net) ---")
    for sym, d in sorted(by_sym.items(), key=lambda kv: kv[1].get("net_usd", 0), reverse=True):
        print(
            f"  {sym:<22} {d.get('trades', 0):>4} tr  "
            f"{d.get('win_rate', 0):>5.1f}% WR  "
            f"${d.get('net_usd', 0):>+8.2f}  "
            f"{d.get('avg_r', 0):>+7.3f} R"
        )
    print()

    # Category splits
    def cat(sym: str) -> str:
        u = sym.upper()
        if "BOOM" in u:
            return "BOOM"
        if "CRASH" in u:
            return "CRASH"
        return "VOLATILITY"

    for label in ("BOOM", "CRASH", "VOLATILITY"):
        cat_syms = {k: v for k, v in by_sym.items() if cat(k) == label}
        if not cat_syms:
            continue
        tot_tr = sum(d.get("trades", 0) for d in cat_syms.values())
        tot_net = sum(d.get("net_usd", 0) for d in cat_syms.values())
        avg_r = (
            sum(d.get("avg_r", 0) * d.get("trades", 0) for d in cat_syms.values()) / tot_tr
            if tot_tr else 0
        )
        wins = sum(1 for d in cat_syms.values() if d.get("net_usd", 0) > 0)
        status = "PROFITABLE" if tot_net > 0 else "LOSING"
        print(f"{label:<12} {tot_tr:>4} tr  {wins}/{len(cat_syms)} symbols green  ${tot_net:>+8.2f}  {avg_r:+.3f} R  {status}")

    print()

    profitable = [s for s, d in by_sym.items() if d.get("net_usd", 0) > 0]
    losing = [s for s, d in by_sym.items() if d.get("net_usd", 0) <= 0]
    if profitable:
        print(f"TRADE ({len(profitable)}):  " + ", ".join(profitable))
    if losing:
        print(f"AVOID ({len(losing)}):  " + ", ".join(losing))

    if result.get("errors"):
        print(f"\nWarnings ({len(result['errors'])}):")
        for e in result["errors"][:8]:
            print("  ", e)

    # Save JSON to backtest/ for the dashboard
    import datetime, json
    out = ROOT / "backtest" / f"all_synthetics_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\nSaved full result to {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

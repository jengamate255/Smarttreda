"""Promotion gate — scans all daily validation reports and shows which
strategy combos have passed on multiple days.

A combo is READY TO PROMOTE when it has passed validation on >= 3 distinct
days (need not be consecutive — markets differ day to day).

Usage: python promotion_check.py [min_days]
"""
from __future__ import annotations
import json, sys
from pathlib import Path

VALIDATED_DIR = Path(__file__).resolve().parent / "memory" / "validated"


def combo_key(r: dict) -> str:
    return f"{r['entry_tf']}|{r['gates']}|{r['sl_tp']}|{r['filters']}"


def main() -> int:
    min_days = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    reports = sorted(VALIDATED_DIR.glob("validated_*.json"))
    if not reports:
        print("no validation reports yet — run the research agent first")
        return 1

    # aggregate: combo -> {"days": {date}, "test_stats": [...]}
    agg: dict[str, dict] = {}
    for rp in reports:
        with open(rp) as f:
            data = json.load(f)
        date = data.get("date", rp.stem.replace("validated_", ""))
        for r in data.get("results", []):
            key = combo_key(r)
            entry = agg.setdefault(key, {"days": set(), "tests": [], "combo": r})
            if r.get("passes"):
                entry["days"].add(date)
                entry["tests"].append(r.get("test", {}))

    if not agg:
        print("no combos validated yet")
        return 0

    ranked = sorted(agg.values(), key=lambda e: len(e["days"]), reverse=True)
    print(f"validation history: {len(reports)} report(s), {len(agg)} unique combos")
    print(f"promotion threshold: {min_days} passing day(s)\n")
    print(f"{'combo':38} {'passes':>6}  latest test stats")
    print("-" * 80)
    for e in ranked:
        days = len(e["days"])
        c = e["combo"]
        label = f"{c['entry_tf']} {c['gates']} {c['sl_tp']:g}R {c['filters']}"
        mark = "  <-- READY" if days >= min_days else ""
        if e["tests"]:
            t = e["tests"][-1]
            latest = f"{t['trades']}t {t['win%']}% exp {t['expect']} PF {t['PF']}"
        else:
            latest = "never passed"
        print(f"{label:38} {days:>6d}  {latest}{mark}")

    ready = [e for e in ranked if len(e["days"]) >= min_days]
    print()
    if ready:
        best = ready[0]
        c = best["combo"]
        print(f"READY TO PROMOTE: {c['entry_tf']} | gates={c['gates']} | "
              f"SL:TP={c['sl_tp']} | filters={c['filters']} "
              f"({len(best['days'])} passing days)")
        print("run: python backtest_agent.py promote")
    else:
        n = min_days - len(ranked[0]["days"]) if ranked else min_days
        print(f"nothing ready yet — best combo needs {max(n,1)} more passing day(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

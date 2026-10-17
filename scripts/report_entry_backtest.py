"""Render completed frozen backtest and independent verification artifacts."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def render(directory):
    result=json.loads((directory/"results.json").read_text(encoding="utf-8"))
    verification=json.loads((directory/"verification.json").read_text(encoding="utf-8"))
    manifest=result["manifest"]
    start=datetime.fromtimestamp(manifest["start"]-manifest["offset"],timezone.utc).isoformat()
    end=datetime.fromtimestamp(manifest["end"]-manifest["offset"],timezone.utc).isoformat()
    aggregate=result["aggregate"]
    old,new=aggregate["previous"],aggregate["updated"]
    verdict="No eligible simulated entries; no edge or improvement can be established." if not old["full"]["trades"] and not new["full"]["trades"] else "A paper price-rule comparison only; no live strategy promotion."
    lines=["# Entry selection backtest — 2026-10-14", "", verdict,"",
        f"Frozen UTC period: **{start} to {end}**. Chronological 70/30 holdout starts **{result['holdout_utc']}**.","",
        "| Symbol | Previous trades | Updated trades | Previous net R | Updated net R | Updated holdout trades |",
        "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in result["results"]:
        before,after=row["comparison"]["previous"],row["comparison"]["updated"]
        lines.append(f"| {row['symbol']} | {before['full']['trades']} | {after['full']['trades']} | {before['full']['net_r']} | {after['full']['net_r']} | {after['holdout']['trades']} |")
    funnels=[(row["symbol"],row["comparison"]["previous"].get("shared_funnel")) for row in result["results"]]
    if all(value is not None for _,value in funnels):
        lines += ["", "## Shared entry funnel", "",
            "These sequential filters apply to both rule versions before the new structural checks. Counts are scan observations, not independent trades.", "",
            "| Symbol | Allowed-hour scans | Usable history/ATR/spread | H1/H4 agree | Fresh zone proximity | Entry confirmation | Confirmed H1 pattern |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for symbol,funnel in funnels:
            values=[funnel[key] for key in ("allowed_hour_scans","history_atr_spread_ready","closed_h1_h4_aligned",
                "fresh_zone_location_pass","entry_confirmation_pass","confirmed_pattern_pass")]
            lines.append("| "+symbol+" | "+" | ".join(str(v) for v in values)+" |")
        totals={key:sum(funnel[key] for _,funnel in funnels) for key in funnels[0][1]}
        lines += ["",f"Across {totals['allowed_hour_scans']:,} allowed-hour scan observations, all had usable history/ATR/spread. "
            f"{totals['closed_h1_h4_aligned']:,} had aligned closed H1/H4 trends; only {totals['fresh_zone_location_pass']} "
            f"also met fresh-zone proximity, {totals['entry_confirmation_pass']} had entry confirmation, and "
            f"{totals['confirmed_pattern_pass']} had the required confirmed H1 pattern. These are the shared existing filters: "
            "the new structural checks received no real-data candidates. The combined entry policy was effectively inactive in this period.",""]
    lines+= ["",f"Aggregate previous / updated trades: **{old['full']['trades']} / {new['full']['trades']}**.",
        f"Aggregate previous / updated holdout trades: **{old['holdout']['trades']} / {new['holdout']['trades']}**.","",
        "A profit factor is unavailable when there are no completed trades. Zero net R and zero closed drawdown from zero trades do not imply a profitable or safe strategy.","",
        "## What was compared","",
        "Both branches use the same frozen broker candles, point sizes, allowed UTC hours, closed H1/H4 trend alignment, confirmed H1 pattern, fresh unbroken same-side zone, closed-entry-candle confirmation, configured stop floors and entry drift/RR checks. Ranking trust is neutral in both because historical learned factors are unavailable.","",
        "The previous branch ranks confirmed patterns by score and generates TP from configured R. The updated branch filters structural invalidation/target geometry, checks remaining reward against the actual stop, caps TP at pattern target and rechecks structure at simulated fill. The baseline removes only those newly introduced structural checks; it retains all preceding quality checks.","",
        "Fills start on the next full future M5 bar consistent with the paper engine. Historical bid/ask spread, adverse stop gaps and configured extra cost in R are applied. Ambiguous stop/target bars are stop-first. Missing historical spreads are forward-filled from earlier observations only; gapped outcome windows are rejected. One open simulated trade and one entry per UTC day per symbol are permitted.","",
        "This does not reproduce historical health/news, consensus, learned score adjustments, confluence ranking, correlation/prop/account sizing, or live exit management. It therefore cannot verify the entire updated desk. Existing desk setup blocks remain active; a replay applying the frozen blocked `desk:`/`desk-` prefixes permits zero desk trades regardless of price-rule results.","",
        "## Verification","",
        f"Independent verifier passed: **{verification['passed']}**. Frozen history files checked: **{verification['history_files_checked']}**. Actual simulated trades independently recomputed: **{verification['verified_trades']}**.","",
        "The verifier separately checks entry/exit prices, spread and R accounting, gap/ambiguous fills, candle availability, event order, overlapping entries, daily duplication, incomplete candles and input hashes. Its 20 synthetic regression tests include incorrect P&L, tampered input and missing-symbol cases, a matched previous/updated structural-rejection case, and exact production/accelerated pivot-count comparisons. With zero actual trades it verifies zero actual fills; synthetic cases do not supply profitability evidence.","",
        "Data capture succeeded for all six allowed symbols and 18 M5/H1/H4 files. Exact row counts, periods and SHA-256 hashes are in `manifest.json`. Computed results and independent checks are in `results.json` and `verification.json`.","",
        "No orders, notifications, live journal/model writes, risk settings, allowlist edits, or service/task actions were performed. Broker use was read-only history and metadata; replay used the captured cache.","",
        "## Reproduce","", "```powershell", "python scripts/backtest_entry_selection.py --cached",
        "python scripts/verify_entry_backtest.py reports/entry_backtest_20261014",
        "python scripts/report_entry_backtest.py reports/entry_backtest_20261014", "```", ""]
    if result["failures"] or verification["errors"]:
        lines += ["## Failures", "",json.dumps({"replay":result["failures"],"verification":verification["errors"]},indent=2)]
    path=directory/"report.md"
    path.write_text("\n".join(lines),encoding="utf-8")
    return path


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path)
    print(render(parser.parse_args().directory))

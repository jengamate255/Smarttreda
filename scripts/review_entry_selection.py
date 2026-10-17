"""Read-only audit of recorded entry selection; no strategy/runtime imports.

This inspects evidence coverage and direction contradictions. It does not infer
profits, replay missing analyst snapshots, use later outcomes to select entries,
or write to runtime state. SQLite connections explicitly use mode=ro.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
# These names have fixed directions in chart_patterns.py. Triangle, range and
# other breakout names are deliberately omitted: direction can depend on price.
FIXED_PATTERN_DIRECTIONS = {
    "double_top": "SELL", "double_bottom": "BUY",
    "head_and_shoulders": "SELL", "inverse_head_and_shoulders": "BUY",
    "rising_wedge": "SELL", "falling_wedge": "BUY",
    "rising_channel": "BUY", "falling_channel": "SELL",
}


def pattern_direction(name: str) -> str | None:
    name = str(name or "").lower()
    if name.startswith(("desk:", "desk-")):
        name = name[5:]
    # Desk broker comments truncate the pattern name at 18 characters.
    matches = {side for pattern, side in FIXED_PATTERN_DIRECTIONS.items()
               if name in (pattern, pattern[:18])}
    return next(iter(matches)) if len(matches) == 1 else None


def mismatch(row: dict) -> bool:
    expected = pattern_direction(row.get("pattern", row.get("setup", "")))
    side = row.get("direction", row.get("dir", row.get("side")))
    return bool(expected and side in ("BUY", "SELL") and side != expected)


def audit_events(path: Path) -> dict:
    counts = Counter()
    mismatched_patterns = Counter()
    examples = []
    timestamps = []
    with path.open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            counts["lines"] += 1
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("event is not an object")
            except (ValueError, TypeError):
                counts["unreadable_lines"] += 1
                continue
            if event.get("kind") != "scan":
                continue
            counts["scans"] += 1
            if event.get("ts"):
                timestamps.append(event["ts"])
            top = event.get("top") or []
            picks = event.get("picks") or []
            counts["recorded_picks"] += len(picks)
            counts["executions_recorded"] += len(event.get("executions") or [])
            for row in top:
                counts["top_observations"] += 1
                if isinstance(row.get("analysts"), dict):
                    counts["with_analyst_snapshot"] += 1
                if pattern_direction(row.get("pattern", "")):
                    counts["fixed_direction_observations"] += 1
                if not mismatch(row):
                    continue
                counts["fixed_direction_mismatches"] += 1
                mismatched_patterns[row["pattern"]] += 1
                chosen = any(pick.get("symbol") == row.get("symbol") and
                             pick.get("dir") == row.get("dir") for pick in picks)
                if chosen:
                    counts["mismatches_also_recorded_as_picks"] += 1
                if len(examples) < 10:
                    examples.append({"line": line_number, "ts": event.get("ts"),
                                     "symbol": row.get("symbol"), "side": row.get("dir"),
                                     "pattern": row.get("pattern"),
                                     "confluence": row.get("confluence"),
                                     "also_recorded_as_pick": chosen})
    for key in ("scans", "top_observations", "with_analyst_snapshot",
                "fixed_direction_observations", "fixed_direction_mismatches",
                "mismatches_also_recorded_as_picks", "unreadable_lines"):
        counts.setdefault(key, 0)
    return {"counts": dict(counts), "first_scan": min(timestamps) if timestamps else None,
            "last_scan": max(timestamps) if timestamps else None,
            "mismatched_patterns": dict(mismatched_patterns), "examples": examples}


def read_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    return connection


def audit_paper(path: Path) -> dict:
    connection = read_connection(path)
    try:
        connection.execute("BEGIN")
        rows = [dict(row) for row in connection.execute(
            "SELECT symbol,side,setup,created_at,qualified,status,reasons FROM candidates")]
        reasons = Counter()
        for row in rows:
            reasons.update(json.loads(row["reasons"]))
        contradictions = [row for row in rows if mismatch(row)]
        return {"candidates": len(rows), "qualified": sum(bool(row["qualified"]) for row in rows),
                "status_counts": dict(Counter(row["status"] for row in rows)),
                "fixed_direction_mismatches": len(contradictions),
                "mismatches_by_setup": dict(Counter(row["setup"] for row in contradictions)),
                "reason_counts": dict(reasons.most_common()),
                "first_created": min((row["created_at"] for row in rows), default=None),
                "last_created": max((row["created_at"] for row in rows), default=None)}
    finally:
        connection.close()


def audit_journal(path: Path) -> dict:
    connection = read_connection(path)
    try:
        connection.execute("BEGIN")
        trades = dict(connection.execute(
            "SELECT COUNT(*) AS rows, SUM(setup IN ('manual','reconcile')) AS unattributed, "
            "COUNT(confidence) AS with_confidence, COUNT(trade_score) AS with_score FROM trades").fetchone())
        signals = dict(connection.execute(
            "SELECT COUNT(*) AS rows, SUM(filled) AS marked_filled, COUNT(close_time) AS with_close_time, "
            "COUNT(r_multiple) AS with_r_multiple FROM signals").fetchone())
        return {"trades": trades, "signals": signals}
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=ROOT / "mt5_mcp/memory/desk_events.jsonl")
    parser.add_argument("--paper-db", type=Path, default=ROOT / "mt5_mcp/memory/entry_shadow.db")
    parser.add_argument("--journal", type=Path, default=ROOT / "mt5_mcp/memory/journal.db")
    parser.add_argument("--output", type=Path, help="Optional report destination; stdout otherwise.")
    args = parser.parse_args()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "Offline coverage audit; no runtime imports, account calls, outcome-based tuning or P&L claims.",
        "desk": audit_events(args.events),
        "paper": audit_paper(args.paper_db),
        "journal": audit_journal(args.journal),
        "before_after_replay": {
            "available": False,
            "reason": "Historical top/pick rows lack full analyst scores/directions, pattern geometry, zone state, executable quotes and historical policy/state.",
            "measurable_effect": "Recorded fixed-pattern direction mismatches fail the new same-direction requirement. Counts describe repeated observations, not independent trades or prevented losses.",
        },
        "limits": [
            "Top-five scan observations omit lower-ranked alternatives and may repeatedly describe the same setup.",
            "Fixed-pattern direction is inferred from explicit detector semantics; dynamic triangle/range directions are not inferred from names.",
            "Historical policy changed. Recorded picks are not fills, and these counts do not measure the marginal effect under today's strict gates.",
            "No historical analyst scores, geometry, spread/quote, learning factors or state are fabricated to replay changes.",
            "Each SQLite read uses a consistent read-only transaction; event/DB snapshots are separate and runtime writers may continue.",
        ],
    }
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        output = args.output.resolve()
        if output in {args.events.resolve(), args.paper_db.resolve(), args.journal.resolve()}:
            parser.error("report destination must differ from every input")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print(str(output))
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

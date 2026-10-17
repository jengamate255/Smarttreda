"""Offline forward-test measurement and rollback preparation; no MT5 imports."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def summarize(path: Path, since: float, until: float, test_id: str = "") -> dict:
    if not 0 <= since < until:
        raise ValueError("Expected UTC bounds since < until")
    counts = Counter()
    cohorts = Counter()
    violations = Counter()
    seen = set()
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
                stamp = float(row["epoch"])
                if not since <= stamp < until:
                    continue
                if test_id and row.get("test_id") != test_id:
                    continue
                if row.get("schema_version") != 2:
                    counts["legacy_rows_not_attempt_denominator"] += 1
                    continue
                attempt = row["attempt_id"]
                if attempt in seen:
                    counts["duplicate_rows"] += 1
                    continue
                seen.add(attempt)
                counts["attempts"] += 1
                counts["rules_passed" if row["rules_allowed"] else "rules_failed"] += 1
                counts["permitted" if row["send_permitted"] else "blocked"] += 1
                hour = datetime.fromtimestamp(stamp, timezone.utc).hour
                key = (row["symbol"], row.get("setup", ""), row["source"],
                       hour, row["mode"], row["event"])
                cohorts[key] += 1
                violations.update(set(row["violations"]))
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError):
                counts["malformed_rows"] += 1
    return {
        "since_utc_epoch": since, "until_utc_epoch": until, "test_id": test_id,
        "counts": dict(counts), "violations": dict(violations),
        "cohorts": [dict(symbol=k[0], setup=k[1], source=k[2], hour_utc=k[3],
                         mode=k[4], event=k[5], attempts=n)
                    for k, n in sorted(cohorts.items())],
        "limitations": [
            "Gate attempts are not fills. Broker acceptance, tickets and P&L are unknown.",
            "Legacy sent=true meant permission only; clean attempts were not logged.",
            "Upstream rejects never reaching the final gate are outside this denominator.",
            "Reconcile account-scoped broker positions/costs before drawing outcome conclusions.",
        ],
    }


def write_rollback(settings: Path, output: Path) -> None:
    # Deliberately never writes the source, .env or guard baseline/good copy.
    from mt5_mcp.settings_guard import rollback_candidate
    if output.resolve() == settings.resolve() or not output.name.endswith(".candidate.json"):
        raise ValueError("Output must be a separate new *.candidate.json file")
    data = rollback_candidate(json.loads(settings.read_text(encoding="utf-8")))
    with output.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report")
    report.add_argument("--log", type=Path, required=True)
    report.add_argument("--since", type=float, required=True, help="UTC epoch inclusive")
    report.add_argument("--until", type=float, required=True, help="UTC epoch exclusive")
    report.add_argument("--test-id", default="")
    rollback = commands.add_parser("rollback-candidate")
    rollback.add_argument("--settings", type=Path, required=True)
    rollback.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "report":
        print(json.dumps(summarize(args.log, args.since, args.until, args.test_id), indent=2))
    else:
        write_rollback(args.settings, args.output)


if __name__ == "__main__":
    main()

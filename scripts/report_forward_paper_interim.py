"""Offline interim coverage report; never imports the runner or trading modules.

Usage: python scripts/report_forward_paper_interim.py --directory PATH
       [--output-directory NEW_PATH]
Without an output directory, Markdown is printed only. Outputs are create-only
and must be outside the input tree. No terminal connection or live state writes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def span(values):
    values = sorted(set(values))
    return {"unique_timestamps": len(values), "first_utc": iso(values[0]) if values else None,
            "last_utc": iso(values[-1]) if values else None,
            "max_timestamp_gap_seconds": max((b-a for a, b in zip(values, values[1:])), default=None)}


def intervals(values):
    """Union request ranges; overlapping recovery requests must not inflate time."""
    merged = []
    for start, end in sorted(values):
        if end < start:
            raise ValueError("Reversed tick request range")
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return {"union_seconds": sum(b-a for a, b in merged),
            "first_utc": iso(merged[0][0]) if merged else None,
            "last_utc": iso(merged[-1][1]) if merged else None,
            "gaps": [{"start_utc": iso(a[1]), "end_utc": iso(b[0]), "seconds": b[0]-a[1]}
                     for a, b in zip(merged, merged[1:])]}


def summarize(directory, now=None):
    # Only pandas/pyarrow and standard library are used; no project imports.
    import pandas as pd

    directory = Path(directory).resolve()
    now = time.time() if now is None else now
    warnings, evidence = [], []
    stamps = {}

    def read(path):
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        stamps[path] = (after.st_size, after.st_mtime_ns)
        if (before.st_size, before.st_mtime_ns) != stamps[path]:
            warnings.append(f"Changed while reading: {path.name}")
        evidence.append({"path": str(path.relative_to(directory)), "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest()})
        return data

    manifest = json.loads(read(directory / "manifest.json"))
    state = json.loads(read(directory / "state.json"))
    if state.get("status") == "running" and now-state["last_run"] > 900:
        warnings.append("Saved run start is older than 15 minutes; current collector liveness is unverified (offline heuristic, not a task-status check)")
    start, end, offset = manifest["start"], min(now, manifest["end"]), manifest["offset"]
    symbols, families = manifest["symbols"], list(state["books"])
    rows = []
    for filename in ("observations.jsonl", "trades.jsonl"):
        parsed = []
        path = directory / filename
        if path.exists():
            for line_number, line in enumerate(read(path).splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    parsed.append(json.loads(line))
                except (ValueError, UnicodeError) as exc:
                    warnings.append(f"{filename}:{line_number}: unreadable record ({exc})")
        else:
            warnings.append(f"Missing {filename}; counts may be incomplete")
        rows.append(parsed)
    observations, ledger = rows
    observations = [r for r in observations if start <= datetime.fromisoformat(r["utc"]).timestamp() <= end]
    errors = [r for r in state.get("errors", []) if start <= datetime.fromisoformat(r["utc"]).timestamp() <= end]

    # Freeze the file inventory, not the running collector. Concurrent updates are disclosed.
    inventory = sorted(directory.glob("bars/*.parquet")) + sorted(directory.glob("ticks/*.parquet"))
    bars = defaultdict(lambda: {"files": 0, "rows": 0, "times": set()})
    ticks = defaultdict(lambda: {"files": 0, "rows": 0, "invalid_rows": 0, "quotes": set(), "ranges": []})
    for path in inventory:
        try:
            data = read(path)
            import io
            if path.parent.name == "bars":
                match = re.fullmatch(r"(.+)_(M\d+|H\d+|D\d+|W\d+)_(\d+)\.parquet", path.name)
                if not match:
                    raise ValueError("Unrecognized bar filename")
                symbol, tf, _ = match.groups()
                frame = pd.read_parquet(io.BytesIO(data), columns=["time"])
                times = pd.to_numeric(frame.time, errors="raise").tolist()
                if not all(math.isfinite(t) for t in times):
                    raise ValueError("Nonfinite bar timestamp")
                item = bars[symbol, tf]
                item["files"] += 1
                item["rows"] += len(frame)
                item["times"].update(float(t)-offset for t in times)
            else:
                match = re.fullmatch(r"(.+)_(\d+)_(\d+)\.parquet", path.name)
                if not match:
                    raise ValueError("Unrecognized tick filename")
                symbol, left, right = match.groups()
                frame = pd.read_parquet(io.BytesIO(data), columns=["time_msc", "bid", "ask"])
                item = ticks[symbol]
                item["ranges"].append((int(left)/1000-offset, int(right)/1000-offset))
                item["files"] += 1
                item["rows"] += len(frame)
                for stamp, bid, ask in frame.itertuples(index=False, name=None):
                    if not all(math.isfinite(v) for v in (stamp, bid, ask)) or not ask >= bid > 0:
                        item["invalid_rows"] += 1
                    else:
                        item["quotes"].add((float(stamp)/1000-offset, float(bid), float(ask)))
        except Exception as exc:
            warnings.append(f"Unreadable archive {path.relative_to(directory)}: {exc}")

    bar_report = {}
    for (symbol, tf), item in sorted(bars.items()):
        seconds = int(tf[1:]) * {"M": 60, "H": 3600, "D": 86400, "W": 604800}[tf[0]]
        window = sorted(t for t in item["times"] if start <= t+seconds <= end)
        bar_report[f"{symbol}/{tf}"] = {
            "files": item["files"], "archived_rows_with_overlap": item["rows"],
            "all_history": span(item["times"]), "closed_in_measurement_window": span(window),
            "calendar_gaps_over_one_bar": sum(b-a > seconds for a, b in zip(window, window[1:]))}
    for symbol in symbols:
        for family in families:
            tf = "H4" if family == "btc_trend_following" else manifest["settings"]["strategy"]["confirmation_timeframe"] if family == "trend_zone" else "H1"
            key = f"{symbol}/{tf}"
            if key not in bar_report:
                bar_report[key] = {"files": 0, "archived_rows_with_overlap": 0,
                    "all_history": span([]), "closed_in_measurement_window": span([]),
                    "calendar_gaps_over_one_bar": 0}
                warnings.append(f"No archived initial signal-timeframe bars: {key}")
    tick_report = {}
    for symbol in symbols:
        item = ticks[symbol]
        quotes = [q for q in item["quotes"] if start <= q[0] <= end]
        tick_report[symbol] = {"files": item["files"], "archived_rows_with_overlap": item["rows"],
            "invalid_archived_rows": item["invalid_rows"], "unique_valid_quote_triples_in_window": len(quotes),
            "quote_timestamps_in_window": span(q[0] for q in quotes),
            "archived_request_ranges": intervals(item["ranges"])}

    # Reconcile append-only evidence with state; use identity to avoid counting both copies.
    entries, exits = {}, {}
    def identity(family, p):
        return (family, p["symbol"], p["entry_time"])
    for row in observations:
        if row.get("paper_entry"):
            entries[identity(row["family"], row["paper_entry"])] = row["paper_entry"]
    for family, book in state["books"].items():
        for p in book["closed"] + book["positions"]:
            if start <= p["entry_time"] <= end:
                entries[identity(family, p)] = p
        for p in book["closed"]:
            if start <= p["exit_time"] <= end:
                exits[identity(family, p)] = p
    for p in ledger:
        if start <= p["exit_time"] <= end:
            key = identity(p["family"], p)
            if key in exits and exits[key] != {k: v for k, v in p.items() if k != "family"}:
                warnings.append(f"State/ledger exit disagreement: {key}")
            exits[key] = {k: v for k, v in p.items() if k != "family"}
            if start <= p["entry_time"] <= end:
                entries.setdefault(key, exits[key])

    cells = []
    for family in families:
        tf = "H4" if family == "btc_trend_following" else manifest["settings"]["strategy"]["confirmation_timeframe"] if family == "trend_zone" else "H1"
        for symbol in symbols:
            obs = [o for o in observations if (o["family"], o["symbol"]) == (family, symbol)]
            err = [e for e in errors if (e.get("family"), e.get("symbol")) == (family, symbol)]
            keys = [k for k in entries if k[:2] == (family, symbol)]
            closed_keys = [k for k in exits if k[:2] == (family, symbol)]
            cells.append({"family": family, "symbol": symbol, "signal_timeframe": tf,
                "shared_bar_coverage_key": f"{symbol}/{tf}", "shared_tick_coverage_key": symbol,
                "observations": len(obs), "unique_signal_bars": len({o["signal_bar_broker"] for o in obs}),
                "observation_times": span(datetime.fromisoformat(o["utc"]).timestamp() for o in obs),
                "valid_directional_plans": sum(o["plan"].get("valid", False) and o["plan"].get("action") in ("BUY", "SELL") for o in obs),
                "paper_rejections": dict(Counter(o["paper_rejection"] for o in obs if o.get("paper_rejection"))),
                "plan_errors": dict(Counter(str(e) for o in obs for e in o["plan"].get("errors", []))),
                "retained_feed_errors": dict(Counter(f"{e.get('phase')}: {e.get('error')}" for e in err)),
                "entries": len(keys), "exits": len(closed_keys),
                "open_in_evidence": sum(k not in exits for k in keys),
                "estimated_closed_net": sum(exits[k]["net_pnl"] for k in closed_keys)})

    changed = []
    for path, stamp in stamps.items():
        try:
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != stamp:
                changed.append(str(path.relative_to(directory)))
        except OSError:
            changed.append(str(path.relative_to(directory)))
    current_inventory = set(directory.glob("bars/*.parquet")) | set(directory.glob("ticks/*.parquet"))
    if changed or current_inventory != set(inventory):
        warnings.append("Collector changed artifacts during read; this is a non-atomic snapshot. Changed files: " + ", ".join(changed))
    details = [{"family": k[0], **p, "entry_utc": iso(p["entry_time"]),
                "exit_utc": iso(exits[k]["exit_time"]) if k in exits else None,
                "closed_record": exits.get(k)} for k, p in sorted(entries.items())]
    return {"schema": 1, "generated_utc": iso(now), "input_directory": str(directory),
        "window_start_utc": iso(start), "window_end_utc": iso(manifest["end"]), "as_of_utc": iso(end),
        "saved_status": state["status"], "saved_last_run_utc": iso(state["last_run"]),
        "saved_state_age_seconds": now-state["last_run"], "broker_timestamp_offset_seconds": offset,
        "symbols": symbols, "families": families, "bar_coverage": bar_report, "tick_coverage": tick_report,
        "cells": cells, "retained_errors": errors, "trades": details,
        "currency": manifest.get("currency", "unspecified"), "cost_reserve_r": manifest.get("cost_r"),
        "caveats": CAVEATS, "warnings": warnings, "input_evidence": evidence}


CAVEATS = [
    "Interim PAPER research only. Independent strategy books must not be summed as portfolio returns; no edge or earnings forecast is established by this small sample.",
    "Only measurement observations are counted; initialization, prevalidation and warmup artifacts are excluded. Counts show logged evaluation rows and distinct signal bars, not five-minute scan attempts; the runner normally skips previously seen signal bars. Open-position, daily-cap and equity-stop skips are not logged observations; absent observations do not establish absence of signals.",
    "Bar snapshots overlap and include lookback history. Unique in-window bars use close time for window membership; reported timestamps are bar OPEN times. Calendar gaps include market closures and are not automatically feed failures. No exchange calendar or expected-session denominator is assumed.",
    "Tick files exist only for paper-position settlement requests. Zero tick files for a symbol does not imply a failed feed. Tick request unions and quote gaps cannot prove broker completeness or continuous prospective signal collection. Recovery history can settle an open trade but cannot recover missed entries during scanner interruptions.",
    "Per-strategy bar references identify the runner's initial signal timeframe; archives are shared across families and cannot prove each planner consumed every archived timeframe. Tick coverage is also shared by symbol, not attributable as independent coverage to every strategy.",
    "The saved error list is truncated to the last 100 on completed runs (partial runs may retain more). Error counts are retained evidence, not lifetime totals. The scan/settlement exception list includes feed failures and may include planner, sizing or conversion failures; exact messages are preserved. Plan errors are reported separately. Saved last_run marks run start, not proof a full scan completed; floating estimates may be stale and are omitted.",
    "Reads do not lock the active collector. Evidence hashes identify bytes read; files can represent different moments. Concurrent changes and unreadable files are disclosed; this is not a final integrity or settlement audit.",
    "Broker bar/tick timestamps are normalized by the frozen manifest offset; observation, entry and exit times are already UTC. Times in tables are UTC.",
    "Research may fail live admission; full desk, news, consensus, prop and portfolio gates are not replayed. Exits use stops/targets only; no early cuts, trailing or partials. Executable bid/ask includes spread; the frozen cost reserve is assumed, not measured future commission/swap.",
]


def markdown(report):
    def safe(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    def table(headers, rows):
        return ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"] + ["| " + " | ".join(safe(v) for v in row) + " |" for row in rows]
    lines = ["# Seven-day paper comparison — interim coverage", "",
        f"Generated: {report['generated_utc']}. Measurement: {report['window_start_utc']} through {report['as_of_utc']}; scheduled end {report['window_end_utc']}.",
        f"Saved status: {report['saved_status']}; saved run start: {report['saved_last_run_utc']} ({report['saved_state_age_seconds']/60:.1f} minutes old).", "",
        f"Estimated monetary results are in {report['currency']}; frozen additional cost reserve: {report['cost_reserve_r']}R per round trip.", "",
        "## Caveats", ""] + [f"- {c}" for c in CAVEATS]
    lines += ["", "## Read warnings", ""] + ([f"- {w}" for w in report["warnings"]] or ["No read warnings detected; this does not certify continuous collection."])
    lines += ["", "## By symbol", ""]
    rows = []
    for symbol in report["symbols"]:
        cells = [c for c in report["cells"] if c["symbol"] == symbol]
        tick = report["tick_coverage"][symbol]
        rows.append([symbol, sum(c["observations"] for c in cells), sum(sum(c["retained_feed_errors"].values()) for c in cells),
                     sum(c["entries"] for c in cells), sum(c["exits"] for c in cells), sum(c["open_in_evidence"] for c in cells),
                     tick["files"], tick["unique_valid_quote_triples_in_window"]])
    lines += table(["Symbol", "Observations", "Retained errors", "Entries", "Exits", "Open", "Tick files", "Unique quote triples"], rows)
    lines += ["", "## By strategy", ""]
    strategy_rows = []
    for family in report["families"]:
        cells = [c for c in report["cells"] if c["family"] == family]
        strategy_rows.append([family, sum(c["observations"] for c in cells),
            sum(sum(c["retained_feed_errors"].values()) for c in cells),
            sum(c["entries"] for c in cells), sum(c["exits"] for c in cells),
            sum(c["open_in_evidence"] for c in cells),
            f"{sum(c['estimated_closed_net'] for c in cells):+.2f}"])
    lines += table(["Strategy", "Observations", "Retained errors", "Entries", "Exits", "Open", "Est. closed net"], strategy_rows)
    lines += ["", "## By strategy and symbol", "", "Bar reference = shared archive symbol/timeframe; tick reference = shared symbol coverage below.", ""]
    lines += table(["Strategy", "Symbol", "Bar reference", "Obs / unique bars", "Retained errors", "Entries / exits / open", "Est. closed net"],
        [[c["family"], c["symbol"], c["shared_bar_coverage_key"], f"{c['observations']} / {c['unique_signal_bars']}",
          sum(c["retained_feed_errors"].values()), f"{c['entries']} / {c['exits']} / {c['open_in_evidence']}", f"{c['estimated_closed_net']:+.2f}"] for c in report["cells"]])
    lines += ["", "## Bar archive coverage", ""]
    lines += table(["Symbol / TF", "Files", "Rows with overlap", "All-history unique", "Window unique", "First / last open UTC in window", "Max open-time gap (s)", "Calendar gaps > one bar"],
        [[key, b["files"], b["archived_rows_with_overlap"], b["all_history"]["unique_timestamps"], b["closed_in_measurement_window"]["unique_timestamps"],
          f"{b['closed_in_measurement_window']['first_utc']} / {b['closed_in_measurement_window']['last_utc']}", b["closed_in_measurement_window"]["max_timestamp_gap_seconds"], b["calendar_gaps_over_one_bar"]] for key, b in report["bar_coverage"].items()])
    lines += ["", "## Tick archive coverage", ""]
    lines += table(["Symbol", "Files / rows with overlap", "Unique valid quote triples", "First / last quote UTC", "Max quote gap (s)", "Request union (s)", "Request gaps", "Invalid rows"],
        [[s, f"{t['files']} / {t['archived_rows_with_overlap']}", t["unique_valid_quote_triples_in_window"],
          f"{t['quote_timestamps_in_window']['first_utc']} / {t['quote_timestamps_in_window']['last_utc']}",
          t["quote_timestamps_in_window"]["max_timestamp_gap_seconds"], round(t["archived_request_ranges"]["union_seconds"], 3), len(t["archived_request_ranges"]["gaps"]), t["invalid_archived_rows"]] for s, t in report["tick_coverage"].items()])
    lines += ["", "## Retained feed errors and observation gaps", ""]
    for c in report["cells"]:
        lines.append(f"- {c['family']} / {c['symbol']}: observations {c['observation_times']['first_utc']} to {c['observation_times']['last_utc']}; max successful-observation gap {c['observation_times']['max_timestamp_gap_seconds']} seconds; retained feed errors {json.dumps(c['retained_feed_errors'])}; plan errors {json.dumps(c['plan_errors'])}; rejections {json.dumps(c['paper_rejections'])}.")
    lines += ["", "## Paper entries and exits", ""]
    lines += table(["Strategy", "Symbol / side", "Entry UTC", "Exit UTC / reason", "Entry / SL / TP", "Lots", "Est. net", "Static screen"],
        [[p["family"], f"{p['symbol']} {p['side']}", p["entry_utc"], f"{p['exit_utc']} / {(p['closed_record'] or {}).get('exit_reason', 'open')}",
          f"{p['entry']} / {p['sl']} / {p['tp']}", p["lots"], (p["closed_record"] or {}).get("net_pnl", "unrealized omitted"),
          "; ".join(p.get("static_screen_reasons", [])) or str(p.get("static_screen_pass"))] for p in report["trades"]])
    lines += ["", "Full request-gap intervals, evidence hashes and machine-readable counts are in summary.json when saved.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path)
    args = parser.parse_args()
    source = args.directory.resolve()
    output = args.output_directory.resolve() if args.output_directory else None
    if output and (output == source or source in output.parents or output in source.parents):
        parser.error("Output must be outside the input tree and cannot be its ancestor")
    if output and output.exists():
        parser.error("Output directory must be new (create-only)")
    report = summarize(source)
    if output:
        output.mkdir(parents=True, exist_ok=False)
        (output / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        (output / "report.md").write_text(markdown(report), encoding="utf-8")
        print(output / "report.md")
    else:
        print(markdown(report))
    return 1 if report["warnings"] else 0


if __name__ == "__main__":
    sys.exit(main())

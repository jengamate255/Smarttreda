"""Floor validation report: backtest A/B comparison + live gate-log evidence.

Two independent views for tuning the entry floors (conf/score/health/RR/zone):

  1. Backtest A/B: compare two ``mt5_mcp.backtest --out`` directories
     (relaxed vs strict floors) side by side, symbol by symbol.
  2. Gate log: every live pipeline evaluation is appended to
     mt5_mcp/memory/confirm_gate_log.jsonl by confirm_execute_daemon; this
     joins those rows with H1 forward prices to show what the floors
     admitted vs rejected actually did afterwards (6/24 bars ahead).

Usage:
    python floor_report.py                 # both views with default paths
    python floor_report.py --backtest tmp/bt_relaxed.json tmp/bt_strict.json
    python floor_report.py --gates only    # live evidence only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
GATE_LOG = PROJECT_DIR / "mt5_mcp" / "memory" / "confirm_gate_log.jsonl"
DEFAULT_BT = [PROJECT_DIR / "tmp" / "bt_relaxed.json", PROJECT_DIR / "tmp" / "bt_strict.json"]


def _summary_rows(out_dir: Path) -> list[dict]:
    import csv

    csv_path = out_dir / "summary.csv"
    if not csv_path.exists():
        return []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def backtest_report(paths: list[Path], labels: list[str]) -> None:
    tables = []
    for p in paths:
        rows = _summary_rows(p)
        if not rows:
            print(f"[backtest] no summary.csv under {p} (run the backtest first)")
            return
        tables.append(rows)
    header = (
        f"{'symbol':8} | {'trades':>14} | {'win%':>11} | {'PF':>10} | "
        f"{'net pnl':>13} | {'maxDD%':>11} | {'expectancy':>11}"
    )
    print("=" * len(header))
    print(f"BACKTEST FLOOR A/B  [{labels[0]}] vs [{labels[1]}]  (identical non-floor params)")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    by_sym = {}
    for table, label in zip(tables, labels):
        for row in table:
            by_sym.setdefault(row["symbol"], {})[label] = row
    totals = {label: {"trades": 0, "pnl": 0.0, "ret": 0.0} for label in labels}
    for sym, entry in by_sym.items():
        cells = []
        for label in labels:
            r = entry.get(label)
            if not r:
                cells.append("n/a")
                continue
            cells.append(f"{r['trades']} {r['win_rate']}%")
        cells2, cells3, cells4, cells5, cells6 = [], [], [], [], []
        for label in labels:
            r = entry.get(label)
            if not r:
                for lst in (cells2, cells3, cells4, cells5, cells6):
                    lst.append("--")
                continue
            cells2.append(f"{r['profit_factor']}")
            cells3.append(f"{r['net_pnl']}")
            cells4.append(f"{r['max_drawdown_pct']}")
            cells5.append(f"{r['expectancy']}")
            cells6.append(f"{r['win_rate']}")
            totals[label]["trades"] += int(r["trades"])
            totals[label]["pnl"] += float(r["net_pnl"])
            totals[label]["ret"] += float(r["total_return_pct"])
        print(
            f"{sym:8} | {' vs '.join(cells):>14} | {' vs '.join(cells6):>11} | "
            f"{' vs '.join(cells2):>10} | {' vs '.join(cells3):>13} | "
            f"{' vs '.join(cells4):>11} | {' vs '.join(cells5):>11}"
        )
    print("-" * len(header))
    print(
        "TOTAL    | "
        + " | ".join(f"{totals[l]['trades']} trades" for l in labels)
        + f" | {'':>11} | {'':>10} | "
        + " vs ".join(f"{totals[l]['pnl']:.2f}" for l in labels)
        + f" | {'':>11} | {'':>11}"
    )
    a, b = labels[0], labels[1]
    print(
        f"\nverdict: {a} total {totals[a]['pnl']:.2f} vs {b} total {totals[b]['pnl']:.2f} "
        f"-> {'keep ' + b if totals[b]['pnl'] > totals[a]['pnl'] else 'consider ' + a} "
        "(same-param A/B; check trade counts for sample size)"
    )
    print("NOTE: single variant family (trend H4, zones H1+H4, tp 80-100pt, risk 0.5%).")


def _load_gates() -> list[dict]:
    if not GATE_LOG.exists():
        return []
    rows = []
    for line in GATE_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def gates_report() -> None:
    rows = _load_gates()
    if not rows:
        print(f"[gates] {GATE_LOG} is empty - no live evaluations yet")
        return
    from collections import defaultdict

    by_symbol: dict = defaultdict(list)
    for row in rows:
        if row.get("action") in ("BUY", "SELL"):
            by_symbol[row["symbol"]].append(row)

    print("=" * 78)
    print(f"LIVE GATE-LOG EVIDENCE  ({len(rows)} evaluations, {len(by_symbol)} symbols with signals)")
    print("=" * 78)
    if not by_symbol:
        print("no BUY/SELL evaluations yet (all rows were NONE) - nothing to join")
        return
    try:
        from mt5_mcp.client import get_client

        client = get_client()
        client.ensure_connected()
    except Exception as exc:  # noqa: BLE001
        print(f"[gates] MT5 unavailable: {exc}")
        return

    for sym, sym_rows in sorted(by_symbol.items()):
        oldest = min(r["ts"] for r in sym_rows)
        start = datetime.fromisoformat(oldest).astimezone(timezone.utc).replace(tzinfo=None)
        hours = max(48, int((datetime.now(timezone.utc).replace(tzinfo=None) - start).total_seconds() // 3600) + 30)
        try:
            df = client.rates(sym, "H1", min(hours, 20000))
        except Exception as exc:  # noqa: BLE001
            print(f"{sym}: rates fetch failed ({exc})")
            continue
        stats = {}  # (avail, action) -> list[(fwd6, fwd24)]
        for row in sym_rows:
            ts = datetime.fromisoformat(row["ts"]).astimezone(timezone.utc).replace(tzinfo=None)
            hits = df.index[df["time"] >= ts]
            if len(hits) == 0:
                continue
            i0 = int(hits[0])
            if i0 + 24 >= len(df):
                continue
            ref = float(df["close"].iloc[i0])
            fwd6 = float(df["close"].iloc[i0 + 6]) - ref
            fwd24 = float(df["close"].iloc[i0 + 24]) - ref
            sign = 1.0 if row["action"] == "BUY" else -1.0
            bucket = (bool(row.get("avail")), row["action"])
            stats.setdefault(bucket, []).append((sign * fwd6, sign * fwd24, abs(fwd6), abs(fwd24)))
        print(f"\n{sym}  (direction-signed forward returns, price units)")
        print(f"  {'bucket':22} {'n':>4} {'fwd+6':>10} {'fwd+24':>10} {'hit24%':>7}")
        for (avail, action), vals in sorted(stats.items()):
            n = len(vals)
            m6 = sum(v[0] for v in vals) / n
            m24 = sum(v[1] for v in vals) / n
            hit = 100.0 * sum(1 for v in vals if v[1] > 0) / n
            tag = "AVAIL (taken)" if avail else "REJECTED (gated)"
            print(f"  {tag + ' ' + action:22} {n:>4} {m6:>10.5f} {m24:>10.5f} {hit:>6.0f}%")
    print(
        "\nREAD: if AVAIL rows show positive signed expectancy vs REJECTED near zero/negative,\n"
        "      the floors are earning their keep; if REJECTED beats AVAIL, floors are too strict.\n"
        "      Windows overlap (one row/30s) - treat as directional evidence, not independent samples."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="floor validation: backtest A/B + live gate log")
    parser.add_argument("--backtest", nargs=2, metavar=("A_DIR", "B_DIR"),
                        default=[str(p) for p in DEFAULT_BT],
                        help="two backtest --out directories to compare")
    parser.add_argument("--labels", nargs=2, default=["relaxed", "strict"])
    parser.add_argument("--gates", action="store_true", help="only the live gate-log view")
    parser.add_argument("--backtest-only", action="store_true", help="only the A/B view")
    args = parser.parse_args(argv)
    if not args.gates:
        backtest_report([Path(p) for p in args.backtest], args.labels)
    if not args.backtest_only:
        if not args.gates:
            print()
        gates_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())


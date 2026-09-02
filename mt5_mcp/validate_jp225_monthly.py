"""Monthly re-validation battery for the deployed JP225 config.

The JP225 edge showed PF decay at the 6000-bar window (1.26 vs 1.64
anchor). Per the 2026-09-02 research report, this battery MUST be re-run
monthly. Halt trigger: if the anchor fails any hard criterion, notify
and recommend halting the JP225_Z40 auto-trader config.

Hard criteria (all must hold on the anchor):
  - trades >= 20
  - PF >= 1.3
  - net >= $0

Window stability (soft): >= 2 of 3 windows PF >= 1.3

Run from mt5_mcp directory:
    python -X utf8 validate_jp225_monthly.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "memory" / "research_lab"

BASE = [
    sys.executable, "-X", "utf8", "backtest.py",
    "--symbols", "JP225",
    "--trend-tf", "H4",
    "--zone-tfs", "H1,H4",
    "--min-zone-score", "40",
    "--min-rr", "3.0",
    "--tp-min", "80",
    "--tp-max", "100",
]

RUNS = {
    "anchor_5000": BASE + ["--bars", "5000"],
    "window_3000": BASE + ["--bars", "3000"],
    "window_6000": BASE + ["--bars", "6000"],
    "perturb_z45": [
        sys.executable, "-X", "utf8", "backtest.py",
        "--symbols", "JP225", "--trend-tf", "H4", "--zone-tfs", "H1,H4",
        "--min-zone-score", "45", "--min-rr", "3.0",
        "--tp-min", "80", "--tp-max", "100", "--bars", "5000",
    ],
    "perturb_rr25": [
        sys.executable, "-X", "utf8", "backtest.py",
        "--symbols", "JP225", "--trend-tf", "H4", "--zone-tfs", "H1,H4",
        "--min-zone-score", "40", "--min-rr", "2.5",
        "--tp-min", "80", "--tp-max", "100", "--bars", "5000",
    ],
}


def run_one(tag: str, cmd: list[str]) -> dict:
    print(f"=== {tag} ===", flush=True)
    proc = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True, timeout=3600)
    out = proc.stdout + proc.stderr

    # The backtester prints per-variant lines like:
    #   "  JP225_...:  29 trades | win  41.4% | PF  1.64 | ret +5.35% | ..."
    import re
    trades = pf = win = net = None
    for line in out.splitlines():
        m = re.search(r":\s*(\d+)\s+trades\s+\|\s+win\s+([\d.]+)%\s+\|\s+PF\s+([\d.]+)", line)
        if m:
            trades = int(m.group(1))
            win = float(m.group(2))
            pf = float(m.group(3))
            try:
                exp = float(re.search(r"exp\s+([+-]?[\d.]+)", line).group(1))
                net = exp * trades
            except (AttributeError, ValueError):
                pass
            break
    return {"tag": tag, "trades": trades, "pf": pf, "win_rate": win, "net": net, "raw_tail": out[-400:]}


def main() -> None:
    results = {}
    for tag, cmd in RUNS.items():
        results[tag] = run_one(tag, cmd)

    a = results.get("anchor_5000") or {}
    hard_pass = bool(
        (a.get("trades") or 0) >= 20
        and (a.get("pf") or 0) >= 1.3
        and (a.get("net") or 0) >= 0
    )
    win_pf = [results[k].get("pf") or 0 for k in ("window_3000", "anchor_5000", "window_6000")]
    windows_pass = sum(1 for p in win_pf if p >= 1.3) >= 2

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "results": {k: {kk: vv for kk, vv in v.items() if kk != "raw_tail"} for k, v in results.items()},
        "hard_criteria_pass": hard_pass,
        "window_stability_pass": windows_pass,
        "action": (
            "CONTINUE - JP225_Z40 edge intact"
            if hard_pass and windows_pass
            else "HALT - JP225_Z40 failed re-validation; disable the config and re-run the full research battery"
        ),
    }
    out_path = RESULTS_DIR / f"jp225_monthly_{datetime.now():%Y%m%d}.json"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()

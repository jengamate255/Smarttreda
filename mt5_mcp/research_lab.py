"""Research Lab: creative strategy research and capital improvement.

Drives the research loop the ``research`` agent (opencode) uses:

1. **Invent** — generate creative strategy variants (parameter space, new
   rules, symbol sets) beyond the fixed grid used by the backtesters.
2. **Validate** — run every variant through the fast Boom/Crash backtester
   and/or the price-action backtester, comparing against a live baseline.
3. **Sharpen** — run Dojo-mode stress tests (perturbations) on the most
   promising variants and keep only those that retain edge under stress.
4. **Promote** — winners are written to a *promotion queue* file; they only
   reach ``learned_config.json`` / the live guard after an explicit
   ``--apply`` flag (or a human confirmation), never silently.

Everything is persisted under ``memory/research_lab/`` as JSON so every
promotion has a paper trail (variant spec, backtest stats, dojo stress,
decision).

Nothing in this module ever places a live order.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mt5_mcp.common import BOOM_CRASH_SYMBOLS
from mt5_mcp.self_tune import _load as load_learned, _save as save_learned

MODULE_DIR = Path(__file__).resolve().parent
LAB_DIR = MODULE_DIR / "memory" / "research_lab"
QUEUE_FILE = LAB_DIR / "promotion_queue.json"
BATCH_FILE = LAB_DIR / "last_batch.json"

# Backtest baseline parameters (Boom/Crash spike-drift strategy).
BASELINE = {
    "spike_threshold_atr": 2.5,
    "min_spike_ticks": 8,
    "max_hold_bars": 120,
    "fill_deadline_bars": 30,
    "min_bars_between_trades": 60,
    "reward_risk_ratio": 1.5,
    "sl_atr_buffer": 2.0,
    "max_spike_age": 15,
}

#: Creative perturbation ranges per parameter (used by the variant factory).
PERTURB_RANGES: dict[str, list[float]] = {
    "spike_threshold_atr": [1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
    "min_spike_ticks": [6, 8, 10, 12, 15],
    "max_hold_bars": [60, 90, 120, 180, 240],
    "fill_deadline_bars": [15, 30, 45],
    "min_bars_between_trades": [30, 60, 90, 120],
    "reward_risk_ratio": [1.0, 1.25, 1.5, 1.75, 2.0, 2.5],
    "sl_atr_buffer": [1.0, 1.5, 2.0, 2.5, 3.0],
    "max_spike_age": [5, 8, 10, 15, 20, 25, 30],
}

#: Per-symbol floor so we never promote a variant with no evidence.
MIN_TRADES_TO_PROMOTE = 8
MIN_PF_TO_PROMOTE = 1.3
MIN_NET_USD_TO_PROMOTE = 0.0
#: A variant must beat the baseline avg R by at least this margin.
BEAT_BASELINE_R_MARGIN = 0.05


@dataclass
class Variant:
    name: str
    params: dict[str, Any]
    rationale: str = ""
    symbol: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir() -> None:
    LAB_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------
# 1. Invent
# ----------------------------------------------------------------------

def _mutate(base: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Perturb a random subset of parameters within the creative ranges."""
    out = dict(base)
    n = rng.randint(2, 4)
    keys = rng.sample(list(PERTURB_RANGES), n)
    for key in keys:
        out[key] = rng.choice(PERTURB_RANGES[key])
    return out


def generate_variants(
    symbols: list[str] | None = None,
    per_symbol: int = 6,
    seed: int | None = None,
) -> list[Variant]:
    """Create creative variants: mutated params + symbol-specific ideas.

    Creative rules applied beyond random mutation:
    * tight-SL variants (sl_atr_buffer 1.0-1.5, higher RR to compensate)
    * fast-trade variants (min_bars_between_trades <= 45, max_hold <= 90)
    * patient variants (spike_threshold_atr >= 3.0, max_spike_age <= 10)
    * drift variants (reward_risk_ratio >= 2.0, max_hold_bars >= 180)
    """
    symbols = list(symbols or BOOM_CRASH_SYMBOLS)
    rng = random.Random(seed)
    variants: list[Variant] = []
    for symbol in symbols:
        for i in range(per_symbol):
            params = _mutate(BASELINE, rng)
            name = f"{symbol}_v{i + 1:02d}"
            variants.append(Variant(name=name, params=params, symbol=symbol))
        # Deterministic archetype variants per symbol (creative, not random)
        archetypes = [
            ("tight_sl", {**BASELINE, "sl_atr_buffer": 1.0, "reward_risk_ratio": 2.0}),
            ("fast", {**BASELINE, "min_bars_between_trades": 30, "max_hold_bars": 60}),
            ("patient", {**BASELINE, "spike_threshold_atr": 3.0, "max_spike_age": 8}),
            ("drift", {**BASELINE, "reward_risk_ratio": 2.0, "max_hold_bars": 240}),
        ]
        for arch_name, params in archetypes:
            variants.append(
                Variant(
                    name=f"{symbol}_arch_{arch_name}",
                    params=params,
                    symbol=symbol,
                    rationale=arch_name,
                )
            )
    return variants


# ----------------------------------------------------------------------
# 2. Validate
# ----------------------------------------------------------------------

def run_variant_backtests(
    variants: list[Variant],
    days: int = 10,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Backtest each variant with ``backtest_boom_crash.run_one``.

    Returns result rows: name, symbol, params, stats (trades, win rate, PF,
    net USD, avg R), plus a comparison vs the per-symbol baseline.
    """
    sys.path.insert(0, str(MODULE_DIR.parent))
    from backtest_boom_crash import get_historical_data, run_one

    # Baseline result per symbol (same data window, live parameters).
    baseline_rows: dict[str, dict[str, Any]] = {}
    for symbol in {v.symbol for v in variants}:
        try:
            df = get_historical_data(symbol, days * 1440)
            if df is None or len(df) < 200:
                continue
            trades, _ = run_one(symbol, df, **BASELINE)
            baseline_rows[symbol] = _row_stats(trades)
        except Exception:
            continue

    rows: list[dict[str, Any]] = []
    cache: dict[str, Any] = {}
    for v in variants:
        try:
            if v.symbol not in cache:
                cache[v.symbol] = get_historical_data(v.symbol, days * 1440)
            df = cache[v.symbol]
            if df is None or len(df) < 200:
                continue
            trades, _ = run_one(v.symbol, df, **v.params)
            stats = _row_stats(trades)
            base = baseline_rows.get(v.symbol, {})
            beats = (
                stats.get("trades", 0) >= MIN_TRADES_TO_PROMOTE
                and stats.get("profit_factor", 0.0) >= MIN_PF_TO_PROMOTE
                and stats.get("net_usd", 0.0) >= MIN_NET_USD_TO_PROMOTE
                and stats.get("avg_r", 0.0)
                > base.get("avg_r", 0.0) + BEAT_BASELINE_R_MARGIN
            )
            rows.append(
                {
                    "name": v.name,
                    "symbol": v.symbol,
                    "params": v.params,
                    "rationale": v.rationale,
                    "stats": stats,
                    "baseline": base,
                    "beats_baseline": beats,
                    "tested_at": _now(),
                }
            )
            if verbose:
                mark = "PROMOTE?" if beats else ""
                print(
                    f"  {v.name:28s} {stats.get('trades', 0):4d} trades "
                    f"WR {stats.get('win_rate', 0):5.1f}% "
                    f"PF {stats.get('profit_factor', 0):5.2f} "
                    f"net ${stats.get('net_usd', 0):+8.2f} "
                    f"avgR {stats.get('avg_r', 0):+6.3f} {mark}",
                    flush=True,
                )
        except Exception:
            continue
    return rows


def _row_stats(trades: list[Any]) -> dict[str, Any]:
    pnls = [float(t.pnl_price) for t in trades]
    n = len(pnls)
    if not n:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "net_usd": 0.0, "avg_r": 0.0}
    wins = [p for p in pnls if p > 0.0]
    losses = [p for p in pnls if p <= 0.0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    return {
        "trades": n,
        "win_rate": round(len(wins) / n * 100.0, 1),
        "profit_factor": round(pf, 2) if pf != float("inf") else 99.99,
        "net_usd": round(sum(pnls), 2),
        "avg_r": round(sum(pnls) / n, 3),
    }


# ----------------------------------------------------------------------
# 3. Sharpen (Dojo)
# ----------------------------------------------------------------------

def dojo_stress(
    rows: list[dict[str, Any]],
    sessions: int = 20,
    max_candidates: int = 5,
    days: int = 10,
) -> list[dict[str, Any]]:
    """Stress-test the best candidates via parameter perturbation.

    Each candidate is re-backtested ``sessions`` times with random parameter
    perturbations (±1-5 for thresholds, ±1 step for ranges). A candidate
    keeps its promotion tag only if it retains edge (PF >= 1.2, WR >= 50%,
    >= 6 trades) in the majority of stress sessions. This rejects
    overfitted variants that only win at exact parameter values.
    """
    candidates = [r for r in rows if r.get("beats_baseline")]
    candidates.sort(key=lambda r: r["stats"].get("avg_r", 0.0), reverse=True)
    candidates = candidates[:max_candidates]
    if not candidates:
        return rows

    sys.path.insert(0, str(MODULE_DIR.parent))
    from backtest_boom_crash import get_historical_data, run_one

    rng = random.Random(hash("dojo-stress") % (2**32))
    frames: dict[str, Any] = {}
    for row in candidates:
        kept = 0
        session_rows: list[dict[str, Any]] = []
        try:
            if row["symbol"] not in frames:
                frames[row["symbol"]] = get_historical_data(row["symbol"], days * 1440)
            df = frames[row["symbol"]]
            if df is None or len(df) < 200:
                continue
            for _ in range(sessions):
                perturbed = dict(BASELINE)
                perturbed.update(row["params"])
                for key in ("spike_threshold_atr", "min_spike_ticks", "max_hold_bars"):
                    if key in row["params"]:
                        step = rng.uniform(-1.0, 1.0) if key == "spike_threshold_atr" else rng.choice([-1, 0, 1])
                        perturbed[key] = max(0.5, row["params"][key] + step)
                trades, _ = run_one(row["symbol"], df, **perturbed)
                stats = _row_stats(trades)
                edge = (
                    stats.get("trades", 0) >= 6
                    and stats.get("profit_factor", 0.0) >= 1.2
                    and stats.get("win_rate", 0.0) >= 50.0
                )
                session_rows.append({"perturbed": perturbed, **stats, "edge": edge})
                if edge:
                    kept += 1
        except Exception:
            session_rows = []
        retention = round(kept / sessions * 100.0, 1) if session_rows else 0.0
        row["dojo"] = {
            "sessions": len(session_rows),
            "edge_retention": retention,
            "keep": retention >= 50.0,
        }
    return rows


# ----------------------------------------------------------------------
# 4. Promote
# ----------------------------------------------------------------------

def promote(
    rows: list[dict[str, Any]],
    symbol: str | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Filter validated+stressed winners and write them to the queue.

    With ``apply=True`` the winner(s) are also merged into
    ``learned_config.json`` per-symbol overrides.
    """
    _ensure_dir()
    winners = [
        r
        for r in rows
        if r.get("beats_baseline") and r.get("dojo", {}).get("keep", False)
        and (symbol is None or r["symbol"] == symbol)
    ]
    winners.sort(key=lambda r: r["stats"].get("avg_r", 0.0), reverse=True)

    queue: list[dict[str, Any]] = []
    if QUEUE_FILE.exists():
        try:
            queue = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            queue = []

    applied: list[dict[str, Any]] = []
    for w in winners:
        entry = {
            "name": w["name"],
            "symbol": w["symbol"],
            "params": w["params"],
            "stats": w["stats"],
            "baseline": w.get("baseline", {}),
            "dojo": w.get("dojo", {}),
            "promoted_at": _now(),
        }
        queue.append(entry)
        if apply:
            _merge_into_learned(entry)
            applied.append(entry)

    QUEUE_FILE.write_text(json.dumps(queue, indent=2), encoding="utf-8")
    batch = {
        "generated_at": _now(),
        "queued": len(winners),
        "applied": len(applied),
        "winners": [w["name"] for w in winners],
        "queue_file": str(QUEUE_FILE),
    }
    BATCH_FILE.write_text(json.dumps(batch, indent=2), encoding="utf-8")
    return batch


def _merge_into_learned(entry: dict[str, Any]) -> None:
    """Merge a promoted variant's params into per-symbol learned config.

    Only touches ``spike_threshold_atr`` / ``max_spike_age`` — the two
    parameters the live guard actually reads — and never flips ``enabled``
    from its current value without explicit request.
    """
    config = load_learned()
    symbol = entry["symbol"]
    params = entry["params"]
    learned = config.setdefault("symbols", {}).setdefault(symbol, {})
    for key in ("spike_threshold_atr", "max_spike_age"):
        if key in params:
            learned[key] = params[key]
    learned["research_note"] = {
        "variant": entry["name"],
        "pf": entry.get("stats", {}).get("profit_factor"),
        "avg_r": entry.get("stats", {}).get("avg_r"),
        "dojo_retention": entry.get("dojo", {}).get("edge_retention"),
        "applied_at": _now(),
    }
    config["updated_at"] = _now()
    save_learned(config)


def show_queue() -> dict[str, Any]:
    if not QUEUE_FILE.exists():
        return {"entries": [], "file": str(QUEUE_FILE)}
    queue = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    return {"entries": queue, "file": str(QUEUE_FILE)}


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research Lab: invent -> validate -> sharpen -> promote")
    parser.add_argument("--symbols", default=None, help="comma-separated subset of BOOM_CRASH_SYMBOLS")
    parser.add_argument("--per-symbol", type=int, default=6, help="random variants per symbol")
    parser.add_argument("--days", type=int, default=10, help="backtest window (days)")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed for reproducibility")
    parser.add_argument("--sessions", type=int, default=20, help="dojo stress sessions per winner")
    parser.add_argument("--apply", action="store_true", help="merge winners into learned_config.json")
    parser.add_argument("--queue", action="store_true", help="show the promotion queue")
    args = parser.parse_args(argv)

    if args.queue:
        print(json.dumps(show_queue(), indent=2, default=str))
        return 0

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols else None

    print(f"Inventing variants for {', '.join(symbols) if symbols else 'all symbols'}...")
    variants = generate_variants(symbols=symbols, per_symbol=args.per_symbol, seed=args.seed)
    print(f"Generated {len(variants)} variants")

    print(f"\nBacktesting {len(variants)} variants over {args.days} days...")
    rows = run_variant_backtests(variants, days=args.days)

    print(f"\nDojo-stressing top candidates ({args.sessions} sessions)...")
    rows = dojo_stress(rows, sessions=args.sessions, days=args.days)

    batch = promote(rows, apply=args.apply)
    print(f"\nQueued {batch['queued']} winners; applied {batch['applied']}")
    print(f"Queue file: {batch['queue_file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

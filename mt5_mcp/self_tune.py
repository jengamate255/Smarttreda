"""Self-tuning layer for the boom/crash spike-drift strategy.

Combines two evidence sources into per-symbol parameter updates that the
trade guard and the watcher consume:

* Backtest priors (``python -m mt5_mcp.self_tune --seed-backtest``): fast
  per-symbol stats at the live parameter set, giving cold-start guidance.
* Live journal outcomes: closed watcher trades retrain the per-symbol stats
  on every learning sync.

Tuning is persisted atomically as JSON in ``memory/learned_config.json``.
The rules are intentionally deterministic and conservative so a single bad
outcome never rewrites strategy parameters.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mt5_mcp.common import BOOM_CRASH_SYMBOLS
from mt5_mcp.memory.trade_guard import _is_watcher_trade
from mt5_mcp.memory.trade_journal import get_journal

MODULE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = MODULE_DIR / "memory" / "learned_config.json"

MIN_LIVE_TRADES = 5
MIN_PRIOR_TRADES = 50
DEFAULT_PARAMS = {
    "enabled": True,
    "confidence_floor": 90.0,
    "max_spike_age": 15,
    "spike_threshold_atr": 2.5,
    "size_multiplier": 1.0,
}

logger = logging.getLogger("mt5_mcp.self_tune")


def _stats(rows: list[dict[str, Any]], pnl_key: str = "pnl") -> dict[str, Any]:
    pnls = [float(row.get(pnl_key) or 0.0) for row in rows]
    n = len(pnls)
    if n == 0:
        return {"trades": 0, "wins": 0, "losses": 0, "pf": 0.0, "avg_r": 0.0, "wr": 0.0}
    wins = [p for p in pnls if p > 0.0]
    losses = [p for p in pnls if p <= 0.0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "pf": pf,
        "avg_r": sum(pnls) / n,
        "wr": len(wins) / n * 100.0,
    }


def _tune_symbol(stats: dict[str, Any]) -> dict[str, Any]:
    """Derive per-symbol parameters from its stats (conservative rules)."""
    params = dict(DEFAULT_PARAMS)
    n = int(stats.get("trades") or 0)
    if n < MIN_LIVE_TRADES:
        return params
    pf = float(stats.get("pf") or 0.0)
    avg_r = float(stats.get("avg_r") or 0.0)
    if pf < 1.0:
        params["enabled"] = False
    elif pf < 1.15:
        params["confidence_floor"] = min(97.0, params["confidence_floor"] + 4.0)
        params["max_spike_age"] = max(5, params["max_spike_age"] - 3)
        params["spike_threshold_atr"] = min(4.0, params["spike_threshold_atr"] + 0.6)
    elif avg_r < 0.05:
        params["confidence_floor"] = min(97.0, params["confidence_floor"] + 2.0)
        params["spike_threshold_atr"] = min(4.0, params["spike_threshold_atr"] + 0.3)
    elif pf >= 1.3 and float(stats.get("wr") or 0.0) >= 50.0:
        params["max_spike_age"] = min(15, params["max_spike_age"] + 2)
    if avg_r < 0.0:
        params["size_multiplier"] = 0.75
    if avg_r < -0.2 and n >= 15:
        params["size_multiplier"] = 0.5
    return params


def _load() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return {"global": dict(DEFAULT_PARAMS), "symbols": {}, "priors": {}, "updated_at": None}


def _save(config: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    payload = json.dumps(config, indent=2)
    # Transient PermissionErrors happen when another process holds the file
    # during the atomic replace; retry, then fall back to a direct write.
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(CONFIG_PATH)
            return
        except PermissionError as exc:  # noqa: PERF203
            last_err = exc
            time.sleep(0.3 * (attempt + 1))
    logger.warning("atomic save failed (%s); falling back to direct write", last_err)
    try:
        CONFIG_PATH.write_text(payload, encoding="utf-8")
    except OSError as exc:
        logger.error("learned_config.json save FAILED: %s - tuning not persisted", exc)


def _live_rows(symbol: str, lookback_days: int = 30) -> list[dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = []
    for trade in get_journal().list_trades(limit=5000):
        if not _is_watcher_trade(trade):
            continue
        if str(trade.get("symbol") or "").upper() != symbol.upper():
            continue
        raw = trade.get("close_time") or trade.get("created_at")
        try:
            value = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if value < since or trade.get("pnl") is None:
            continue
        rows.append(trade)
    return rows


def retrain_from_live(lookback_days: int = 30) -> dict[str, Any] | None:
    """Rebuild the config from backtest priors blended with live outcomes."""
    config = _load()
    priors = config.get("priors") or {}
    changed = 0
    for symbol in BOOM_CRASH_SYMBOLS:
        live = _live_rows(symbol, lookback_days)
        live_stats = _stats(live)
        prior_stats = _stats(priors.get(symbol, {}).get("trades") or [], pnl_key="pnl")
        if live_stats["trades"] >= MIN_LIVE_TRADES:
            stats = live_stats
            source = "live"
        elif prior_stats["trades"] >= MIN_PRIOR_TRADES:
            stats = prior_stats
            source = "prior"
        else:
            config["symbols"].pop(symbol, None)
            continue
        old = config["symbols"].get(symbol)
        params = _tune_symbol(stats)
        params["evidence"] = {"source": source, **stats}
        config["symbols"][symbol] = params
        if old != params:
            changed += 1
    config["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save(config)
    return config if changed else None


def seed_backtest(days: int = 10, spike_threshold_atr: float = 2.5) -> dict[str, Any]:
    """Run the fast backtest per symbol at live parameters and store priors."""
    sys.path.insert(0, str(MODULE_DIR.parent))
    from backtest_boom_crash import get_historical_data, run_one

    config = _load()
    priors: dict[str, Any] = {}
    for symbol in BOOM_CRASH_SYMBOLS:
        try:
            df = get_historical_data(symbol, days * 1440)
            trades, _ = run_one(
                symbol,
                df,
                spike_threshold_atr=spike_threshold_atr,
                min_spike_ticks=8,
                max_hold_bars=120,
                fill_deadline_bars=30,
                min_bars_between_trades=60,
                reward_risk_ratio=1.5,
                sl_atr_buffer=2.0,
                max_spike_age=15,
            )
            pnls = [float(t.pnl_price) for t in trades]
            stats = _stats([{"pnl": p} for p in pnls])
            priors[symbol] = {
                "trades": [{"pnl": p} for p in pnls],
                "stats": stats,
                "seeded_at": datetime.now(timezone.utc).isoformat(),
            }
            logger.info(
                "%s prior: %d trades pf=%.2f avg_r=%.3f",
                symbol,
                stats["trades"],
                stats["pf"],
                stats["avg_r"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s backtest prior failed: %s", symbol, exc)
    config["priors"] = priors
    _save(config)
    retrain_from_live()
    return config


def current_symbol(symbol: str) -> dict[str, Any]:
    """Effective parameters for one symbol, or defaults when unlearned."""
    params = dict(DEFAULT_PARAMS)
    params.update(_load().get("symbols", {}).get(symbol.upper()) or {})
    return params


def current() -> dict[str, Any]:
    return _load()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-backtest", action="store_true")
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--spike-threshold-atr", type=float, default=2.5)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.seed_backtest:
        config = seed_backtest(args.days, args.spike_threshold_atr)
    else:
        config = retrain_from_live()
        if config is None:
            config = current()
    for symbol, params in sorted(config.get("symbols", {}).items()):
        evidence = params.get("evidence", {})
        print(
            f"{symbol:13s} enabled={str(params.get('enabled')):5s} "
            f"floor={params.get('confidence_floor'):.0f} "
            f"age={params.get('max_spike_age')} "
            f"thr={params.get('spike_threshold_atr'):.2f} "
            f"size={params.get('size_multiplier'):.2f} "
            f"[{evidence.get('source', '?')} n={evidence.get('trades', 0)} "
            f"pf={evidence.get('pf', 0):.2f} avgR={evidence.get('avg_r', 0):.3f}]"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
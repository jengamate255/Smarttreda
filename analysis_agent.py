"""30-minute market analysis agent: scans for available setups, no orders.

Runs a read-only cycle over a symbol universe:
  1. compliance pre-check (fail closed, analysis-only so halt just skips)
  2. per-symbol market health via mt5_mcp.market_intel.market_health
  3. per-symbol strategy plan via mt5_mcp.strategy.evaluate_strategy
  4. availability verdict (confidence / trade score / RR / health floors)
  5. persist state + per-run JSON, notify only on new/changed AVAILABLE

Usage:
  python analysis_agent.py --once                # single scan (scheduler mode)
  python analysis_agent.py --once --dry-run      # same, explicit (no orders ever sent)
  python analysis_agent.py --loop                # continuous every --interval-minutes
  python analysis_agent.py --once --symbols EURUSD XAUUSD

Scheduling (Windows Task Scheduler, every 30 min):
  python E:\\Project\\Mt5 Ea\\analysis_agent.py --once

This module never places orders. It only reads market data and writes
analysis state under mt5_mcp/memory/analysis/.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    from dotenv import load_dotenv
except ImportError:  # some venvs lack python-dotenv; env may come from the shell
    def load_dotenv(*args, **kwargs):
        return False

PROJECT_DIR = Path(__file__).resolve().parents[0]
MODULE_DIR = PROJECT_DIR / "mt5_mcp"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

load_dotenv(PROJECT_DIR / ".env")

LOG_PATH = PROJECT_DIR / "logs" / "analysis_agent.log"
STATE_PATH = MODULE_DIR / "memory" / "analysis_state.json"
LOCK_PATH = MODULE_DIR / "memory" / "analysis_agent.lock"
KILL_PATH = MODULE_DIR / "memory" / "ANALYSIS_KILL"
EVENTS_PATH = MODULE_DIR / "memory" / "analysis_events.jsonl"
RUNS_DIR = MODULE_DIR / "memory" / "analysis"
MAX_EVENTS_KEPT = 2000

# Today's research universe (memory/research/research_2026-09-14.json)
# plus validated/metals/oil/index symbols.
DEFAULT_SYMBOLS: list[str] = [
    "EURUSD", "GBPUSD", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "CHFJPY", "EURJPY", "GBPJPY", "NZDJPY", "GBPCHF", "NZDCAD",
    "AUDNZD", "EURAUD", "EURCHF",
    "XAUUSD", "XAGUSD", "USOIL", "JP225",
]

INTERVAL_MINUTES = int(os.getenv("ANALYSIS_INTERVAL_MINUTES", "30"))
MIN_CONFIDENCE = float(os.getenv("ANALYSIS_MIN_CONFIDENCE", "75"))
MIN_TRADE_SCORE = float(os.getenv("ANALYSIS_MIN_SCORE", "70"))
MIN_HEALTH = float(os.getenv("ANALYSIS_MIN_HEALTH", "55"))
MIN_RR = float(os.getenv("ANALYSIS_MIN_RR", "3.0"))
SNOOZE_MINUTES = int(os.getenv("ANALYSIS_SNOOZE_MINUTES", "90"))

logger = logging.getLogger("analysis_agent")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(handler)
    try:
        if sys.stdout is not None:
            logger.addHandler(logging.StreamHandler())
    except Exception:
        pass


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("last_bar", {})
            data.setdefault("last_plan", {})
            data.setdefault("last_notify", {})
            data.setdefault("runs", 0)
            return data
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return {"last_bar": {}, "last_plan": {}, "last_notify": {}, "runs": 0}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
            if not handle:
                return False
            status = ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
            ctypes.windll.kernel32.CloseHandle(handle)
            return status == 0x00000102
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError, SystemError):
        return False
    return True


@contextmanager
def _single_instance() -> Iterator[None]:
    """Prevent overlapping scheduler invocations."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            break
        except FileExistsError:
            try:
                pid = int(LOCK_PATH.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                pid = 0
            if pid and _pid_alive(pid):
                raise RuntimeError(f"another analysis cycle is running (pid {pid})")
            try:
                LOCK_PATH.unlink()
            except FileNotFoundError:
                pass
    else:
        raise RuntimeError("could not acquire analysis lock")
    try:
        yield
    finally:
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass


def _short_plan(plan: Any) -> dict[str, Any]:
    get = lambda k, d=None: plan.get(k, d) if isinstance(plan, dict) else getattr(plan, k, d)
    return {
        "action": get("action"),
        "valid": bool(get("valid", False)),
        "confidence": float(get("confidence", 0.0) or 0.0),
        "trade_score": float(get("trade_score", 0.0) or 0.0),
        "entry": get("entry"),
        "sl": get("sl"),
        "tp": get("tp"),
        "sl_pips": get("sl_pips"),
        "tp_pips": get("tp_pips"),
        "risk_reward": get("risk_reward"),
        "zone_strength": float(get("zone_strength", 0.0) or 0.0),
        "confirmation": get("confirmation", ""),
        "reason": get("reason", ""),
    }


def _bar_key(client: Any, symbol: str) -> str:
    frame = client.rates(symbol, "H1", 3)
    if len(frame) < 2:
        raise RuntimeError(f"not enough H1 bars for {symbol}")
    return str(frame.iloc[-2]["time"])


def is_available(
    plan: dict[str, Any],
    health_score: float,
    min_confidence: float = MIN_CONFIDENCE,
    min_score: float = MIN_TRADE_SCORE,
    min_health: float = MIN_HEALTH,
    min_rr: float = MIN_RR,
) -> tuple[bool, list[str]]:
    """Pure verdict helper (no MT5): returns (available, reasons)."""
    reasons: list[str] = []
    if not plan.get("valid"):
        reasons.append(plan.get("reason") or "plan not valid")
    if plan.get("action") not in {"BUY", "SELL"}:
        reasons.append("no executable direction")
    if float(plan.get("confidence", 0.0) or 0.0) < min_confidence:
        reasons.append(f"confidence {plan.get('confidence', 0):.1f} < {min_confidence:.0f}")
    if float(plan.get("trade_score", 0.0) or 0.0) < min_score:
        reasons.append(f"score {plan.get('trade_score', 0):.1f} < {min_score:.0f}")
    rr = plan.get("risk_reward")
    if rr is None or float(rr) < min_rr:
        reasons.append(f"RR {rr} < {min_rr}")
    if health_score < min_health:
        reasons.append(f"health {health_score:.0f} < {min_health:.0f}")
    return (not reasons, reasons)


def should_notify(
    symbol: str,
    available: bool,
    action: str | None,
    state: dict[str, Any],
    now_ts: float,
    snooze_minutes: int = SNOOZE_MINUTES,
) -> bool:
    """Only alert on new/changed AVAILABLE setups, throttled by snooze."""
    if not available:
        return False
    prev = (state.get("last_plan", {}).get(symbol) or {}).get("plan", {})
    prev_available = bool((state.get("last_plan", {}).get(symbol) or {}).get("available"))
    prev_action = prev.get("action")
    last_ts = float((state.get("last_notify", {}).get(symbol) or {}).get("ts", 0.0) or 0.0)
    if not prev_available or prev_action != action:
        return True
    return (now_ts - last_ts) >= snooze_minutes * 60


def _append_event(record: dict[str, Any]) -> None:
    try:
        EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass


def _analyze_symbol(client: Any, symbol: str) -> dict[str, Any]:
    from mt5_mcp.market_intel import current_session, market_health
    from mt5_mcp.strategy import evaluate_strategy

    result: dict[str, Any] = {"symbol": symbol}
    try:
        bar = _bar_key(client, symbol)
    except Exception as exc:
        result["error"] = f"bar key failed: {exc}"
        return result
    result["bar"] = bar

    try:
        health = market_health(symbol, timeframe="H1", lookback=400)
    except Exception as exc:
        result["error"] = f"health failed: {exc}"
        return result
    result["health_score"] = float(health.get("health_score", 0.0) or 0.0)
    result["verdict"] = health.get("verdict")
    result["regime"] = health.get("regime")

    try:
        plan = None
        try:
            from mt5_mcp.multi_strategy import STRATEGY_ROUTING, bb_mr_fade_strategy
            routed = STRATEGY_ROUTING.get(symbol, "")
            planners = {"bb_mr_fade": bb_mr_fade_strategy}
            if routed in planners:
                plan = planners[routed](symbol)
                result["strategy_source"] = routed
        except Exception as exc:
            logger.warning("%s routed strategy failed, falling back to trend_zone: %s", symbol, exc)
        if plan is None:
            plan = evaluate_strategy(symbol=symbol, closed_confirmation=True)
    except Exception as exc:
        result["error"] = f"strategy failed: {exc}"
        return result
    short = _short_plan(plan)
    result["plan"] = short

    available, reasons = is_available(short, result["health_score"])
    result["available"] = available
    result["reasons"] = reasons
    try:
        result["session"] = current_session()
    except Exception:
        result["session"] = {}
    return result


def run_once(dry_run: bool = False, symbols: list[str] | None = None) -> int:
    """Run one read-only scan cycle. Never places orders. Returns 0 on success."""
    _setup_logging()
    if KILL_PATH.exists():
        logger.warning("disabled by kill switch: %s", KILL_PATH)
        return 0

    universe = symbols or DEFAULT_SYMBOLS
    state = _load_state()
    state["runs"] = int(state.get("runs", 0)) + 1
    _save_state(state)

    with _single_instance():
        from mt5_mcp.client import get_client
        from mt5_mcp.compliance import evaluate as evaluate_compliance
        from mt5_mcp.notify import notify

        client = get_client()
        client.ensure_connected()

        try:
            status = evaluate_compliance()
        except Exception as exc:
            logger.error("compliance check failed closed: %s", exc)
            return 1
        mode = status.get("mode", "unknown")
        logger.info(
            "analysis cycle=%d symbols=%d compliance=%s dry_run=%s",
            state["runs"], len(universe), mode, dry_run,
        )

        results: list[dict[str, Any]] = []
        for symbol in universe:
            try:
                if client.positions(symbol):
                    logger.info("%s note: existing position open (analysis continues)", symbol)
                res = _analyze_symbol(client, symbol)
            except Exception as exc:
                res = {"symbol": symbol, "error": str(exc)}
            results.append(res)
            short = res.get("plan", {})
            if short:
                state["last_bar"][symbol] = res.get("bar", "")
                state["last_plan"][symbol] = {
                    "bar": res.get("bar", ""),
                    "plan": short,
                    "available": bool(res.get("available", False)),
                    "health_score": res.get("health_score", 0.0),
                }
            logger.info(
                "%s health=%s plan=%s/%s avail=%s %s",
                symbol, res.get("health_score"), short.get("action"),
                short.get("confidence"), res.get("available"),
                "| ".join(res.get("reasons", []) or [short.get("reason", "") or "ok"]),
            )
            # consensus board: read-only stance on every evaluation
            try:
                if short:
                    from mt5_mcp.consensus import publish as consensus_publish
                    consensus_publish("analysis", symbol,
                                      short.get("action", "NONE"),
                                      short.get("confidence", 0))
            except Exception:
                pass
        _save_state(state)

        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        run_record = {
            "at": now.isoformat(),
            "cycle": state["runs"],
            "compliance_mode": mode,
            "results": results,
        }
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        (RUNS_DIR / f"analysis_{now.strftime('%Y%m%d_%H%M')}.json").write_text(
            json.dumps(run_record, indent=2, default=str), encoding="utf-8"
        )
        _append_event({"at": now.isoformat(), "cycle": state["runs"], "mode": mode,
                       "available": [r["symbol"] for r in results if r.get("available")]})

        for res in results:
            symbol = res["symbol"]
            if res.get("error") or not res.get("plan"):
                continue
            if should_notify(symbol, bool(res.get("available")),
                             res["plan"].get("action"), state, now_ts):
                msg = (f"{symbol} {res['plan'].get('action')} @ {res['plan'].get('entry')} "
                       f"SL={res['plan'].get('sl')} TP={res['plan'].get('tp')} "
                       f"conf={res['plan'].get('confidence', 0):.0f}% "
                       f"score={res['plan'].get('trade_score', 0):.0f} "
                       f"health={res.get('health_score', 0):.0f} regime={res.get('regime')}")
                try:
                    notify(f"analysis_{symbol}", msg)
                except Exception as exc:
                    logger.warning("%s notify failed: %s", symbol, exc)
                state.setdefault("last_notify", {})[symbol] = {"ts": now_ts, "msg": msg}
        _save_state(state)

        n_avail = sum(1 for r in results if r.get("available"))
        logger.info("cycle done: %d/%d available", n_avail, len(results))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="run one scan (scheduler mode)")
    parser.add_argument("--loop", action="store_true", help="run continuously every --interval-minutes")
    parser.add_argument("--dry-run", action="store_true", help="explicit read-only (default behavior)")
    parser.add_argument("--symbols", nargs="*", default=None, help="override symbol universe")
    parser.add_argument("--interval-minutes", type=int, default=INTERVAL_MINUTES)
    args = parser.parse_args()
    try:
        if args.loop and not args.once:
            _setup_logging()
            logger.info("loop mode every %d minutes", args.interval_minutes)
            while True:
                run_once(dry_run=True, symbols=args.symbols)
                time.sleep(max(60, args.interval_minutes * 60))
            return 0
        return run_once(dry_run=True, symbols=args.symbols)
    except Exception as exc:
        _setup_logging()
        logger.exception("analysis cycle failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

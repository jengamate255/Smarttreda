"""Guarded, one-shot autonomous trader for the MT5 SIM account.

The Windows task invokes this module every few minutes. It evaluates only
completed H1 confirmation bars and can trade only the validated EURUSD Med
and JP225 configurations. BTCUSD and SPX500 are deliberately excluded.

This runner fails closed unless all of the following are true:

* ``MT5_AUTOTRADER_ENABLED=1`` and ``MT5_AUTOTRADER_MODE=SIM``
* the connected account server contains ``SIM``
* no kill-switch file exists
* compliance is normal, including news/liquidity/spread/risk gates
* the strategy plan is valid, confidence is at least 90%, and the SL/TP
  remain inside the validated bounds

Use ``--dry-run`` to inspect a cycle without sending an order.
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

from dotenv import load_dotenv

from mt5_mcp.common import NewsEvent


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODULE_DIR = PROJECT_DIR / "mt5_mcp"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

# The SIM runner uses the dedicated prop-firm profile. Keep credentials in
# .env and map them to the package client without duplicating the secret.
load_dotenv(PROJECT_DIR / ".env")
for source, target in (
    ("MT5_LOGIN", "MT5_MCP_MT5_LOGIN"),
    ("MT5_PASSWORD", "MT5_MCP_MT5_PASSWORD"),
    ("MT5_SERVER", "MT5_MCP_MT5_SERVER"),
    ("MT5_PATH", "MT5_MCP_MT5_TERMINAL_PATH"),
):
    if os.getenv(source):
        os.environ[target] = os.environ[source]
os.environ.setdefault(
    "MT5_MCP_MT5_TERMINAL_PATH",
    r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
)

from mt5_mcp.client import get_client  # noqa: E402
from mt5_mcp.common import pip_size  # noqa: E402
from mt5_mcp.compliance import evaluate as evaluate_compliance  # noqa: E402
from mt5_mcp.compliance import gate  # noqa: E402
from mt5_mcp.notify import notify  # noqa: E402
from mt5_mcp.strategy import evaluate_strategy  # noqa: E402
from tools.news import get_provider  # noqa: E402
from tools.trades import MAGIC, close_position, modify_sl_tp, open_market  # noqa: E402


LOG_PATH = PROJECT_DIR / "logs" / "auto_trader.log"
STATE_PATH = MODULE_DIR / "memory" / "auto_trader_state.json"
LOCK_PATH = MODULE_DIR / "memory" / "auto_trader.lock"
KILL_PATH = MODULE_DIR / "memory" / "AUTO_TRADER_KILL"
EVENTS_PATH = MODULE_DIR / "memory" / "auto_trader_events.jsonl"
FORWARD_PATH = MODULE_DIR / "memory" / "forward_validation.jsonl"
NEWS_CACHE_PATH = MODULE_DIR / "memory" / "news_cache.json"

POSITION_TIMEOUT_SECONDS = 200 * 3600  # 200 H1 bars
NEWS_CACHE_TTL = 12 * 3600
MAX_EVENTS_KEPT = 2000

SYMBOL_CONFIG: dict[str, dict[str, Any]] = {
    "EURUSD": {
        "zone_timeframes": ["H1"],
        "min_zone_score": 70.0,
        "sl_min": 15.0,
        "sl_max": 30.0,
        "tp_min": 80.0,
        "tp_max": 100.0,
        "magic_label": "EURUSD_MED",
    },
    "JP225": {
        "zone_timeframes": ["H1", "H4"],
        "min_zone_score": 40.0,
        "sl_min": 20.0,
        "sl_max": 30.0,
        "tp_min": 80.0,
        "tp_max": 100.0,
        "magic_label": "JP225_Z40",
    },
}

RISK_PERCENT = float(os.getenv("MT5_AUTOTRADER_RISK_PERCENT", "0.25"))
MIN_RR = 3.0
MIN_TRADE_SCORE = 70.0
MIN_CONFIDENCE = 90.0
MAX_SPREAD_POINTS = 40.0

logger = logging.getLogger("mt5_autotrader")


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("last_bar", {})
            data.setdefault("last_plan", {})
            data.setdefault("last_order", {})
            data.setdefault("positions", {})
            data.setdefault("fv", {})
            data.setdefault("managed", {})
            data.setdefault("runs", 0)
            return data
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return {
        "last_bar": {},
        "last_plan": {},
        "last_order": {},
        "positions": {},
        "fv": {},
        "managed": {},
        "runs": 0,
    }


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
            return status == 0x00000102  # WAIT_TIMEOUT: process is still running
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except (OSError, ValueError, SystemError):
        return False
    return True


@contextmanager
def _single_instance() -> Iterator[None]:
    """Prevent overlapping five-minute task invocations."""
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
                raise RuntimeError(f"another auto-trader cycle is running (pid {pid})")
            try:
                LOCK_PATH.unlink()
            except FileNotFoundError:
                pass
    else:
        raise RuntimeError("could not acquire auto-trader lock")

    try:
        yield
    finally:
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass


def _account_guard(client) -> dict[str, Any]:
    mode = os.getenv("MT5_AUTOTRADER_MODE", "").strip().upper()
    if mode != "SIM":
        raise RuntimeError("MT5_AUTOTRADER_MODE must be SIM")

    account = client.account_info()
    server = str(account.get("server", ""))
    if "SIM" not in server.upper():
        raise RuntimeError(f"refusing non-SIM account server: {server or '<unknown>'}")

    expected_login = os.getenv("MT5_AUTOTRADER_EXPECTED_LOGIN", "11911448").strip()
    actual_login = str(account.get("login", ""))
    if expected_login and actual_login != expected_login:
        raise RuntimeError(
            f"unexpected account login {actual_login}; expected {expected_login}"
        )
    return account


def _news_guard() -> None:
    """Fail closed when the live calendar provider is unavailable."""
    provider = get_provider()
    try:
        provider.upcoming(hours=24)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"news provider failed: {exc}") from exc
    failure_until = float(getattr(provider, "_failure_until", 0.0) or 0.0)
    cache_usable = bool(getattr(provider, "_cache_usable", False))
    if failure_until > time.time() and not cache_usable:
        raise RuntimeError("news provider is unavailable; automatic trading paused")


def _bar_key(client, symbol: str) -> str:
    frame = client.rates(symbol, "H1", 3)
    if len(frame) < 2:
        raise RuntimeError(f"not enough H1 bars for {symbol}")
    return str(frame.iloc[-2]["time"])


def _spread_points(client, symbol: str) -> float:
    info = client.symbol_info(symbol)
    tick = client.tick(symbol)
    point = float(info.get("point", 0.0) or 0.0)
    if point <= 0:
        raise RuntimeError(f"invalid point size for {symbol}")
    return (float(tick.get("ask", 0.0)) - float(tick.get("bid", 0.0))) / point


def _status_guard() -> dict[str, Any]:
    status = evaluate_compliance()
    if status.get("mode") != "normal":
        raise RuntimeError(f"compliance mode is {status.get('mode')}")
    if not status.get("trading_allowed", False):
        raise RuntimeError("compliance does not allow trading")
    if status.get("news_blocked"):
        raise RuntimeError("high-impact news blackout is active")
    if status.get("liquidity_blocked"):
        raise RuntimeError("liquidity blackout is active")
    return status


def _plan_for(symbol: str):
    cfg = SYMBOL_CONFIG[symbol]
    return evaluate_strategy(
        symbol=symbol,
        risk_percent=RISK_PERCENT,
        tp_min=cfg["tp_min"],
        tp_max=cfg["tp_max"],
        sl_min=cfg["sl_min"],
        sl_max=cfg["sl_max"],
        min_rr=MIN_RR,
        min_zone_score=cfg["min_zone_score"],
        min_trade_score=MIN_TRADE_SCORE,
        min_confidence_percent=MIN_CONFIDENCE,
        trend_timeframe="H4",
        zone_timeframes=cfg["zone_timeframes"],
        confirmation_timeframe="H1",
        lookback=400,
        closed_confirmation=True,
    )


def _history_error(client, symbol: str) -> str | None:
    """Fail closed until the terminal has enough H1/H4 bars for a prop plan."""
    timeframes = set(["H1", "H4", *SYMBOL_CONFIG[symbol]["zone_timeframes"]])
    for timeframe in timeframes:
        try:
            frame = client.rates(symbol, timeframe, 300)
        except Exception as exc:
            return f"{timeframe} history unavailable: {exc}"
        if len(frame) < 220:
            return f"{timeframe} history warming up ({len(frame)}/220 bars)"
    return None


def _plan_rejections(client, symbol: str, plan) -> list[str]:
    cfg = SYMBOL_CONFIG[symbol]
    reasons: list[str] = []
    if not plan.valid:
        reasons.append(plan.reason or "strategy plan is not valid")
    if plan.action not in {"BUY", "SELL"}:
        reasons.append("no executable direction")
    if plan.confidence < MIN_CONFIDENCE:
        reasons.append(f"confidence {plan.confidence:.1f}% below {MIN_CONFIDENCE:.1f}%")
    if plan.trade_score < MIN_TRADE_SCORE:
        reasons.append(f"trade score {plan.trade_score:.1f} below {MIN_TRADE_SCORE:.1f}")
    if plan.zone_strength < cfg["min_zone_score"]:
        reasons.append("zone strength below configured threshold")
    if plan.sl_pips is None or not cfg["sl_min"] <= plan.sl_pips <= cfg["sl_max"]:
        reasons.append(
            f"SL {plan.sl_pips} pips outside {cfg['sl_min']}-{cfg['sl_max']}"
        )
    if plan.tp_pips is None or not cfg["tp_min"] <= plan.tp_pips <= cfg["tp_max"]:
        reasons.append(
            f"TP {plan.tp_pips} pips outside {cfg['tp_min']}-{cfg['tp_max']}"
        )
    if plan.risk_reward is None or plan.risk_reward < MIN_RR:
        reasons.append(f"RR {plan.risk_reward} below {MIN_RR}")
    if plan.entry is None or plan.sl is None or plan.tp is None or not plan.lots:
        reasons.append("entry, protective prices, or lot size missing")
    try:
        spread = _spread_points(client, symbol)
        if spread > MAX_SPREAD_POINTS:
            reasons.append(f"spread {spread:.0f} exceeds {MAX_SPREAD_POINTS:.0f} points")
    except Exception as exc:  # noqa: BLE001
        reasons.append(str(exc))
    return reasons


def _short_plan(plan) -> dict[str, Any]:
    return {
        "action": plan.action,
        "valid": plan.valid,
        "confidence": plan.confidence,
        "trade_score": plan.trade_score,
        "entry": plan.entry,
        "sl": plan.sl,
        "tp": plan.tp,
        "sl_pips": plan.sl_pips,
        "tp_pips": plan.tp_pips,
        "risk_reward": plan.risk_reward,
        "lots": plan.lots,
        "zone_strength": plan.zone_strength,
        "confirmation": plan.confirmation,
        "reason": plan.reason,
    }


def run_once(dry_run: bool = False) -> int:
    _setup_logging()
    if not _env_bool("MT5_AUTOTRADER_ENABLED"):
        logger.info("disabled: MT5_AUTOTRADER_ENABLED is not 1")
        return 0
    if KILL_PATH.exists():
        logger.warning("disabled by kill switch: %s", KILL_PATH)
        return 0

    execute = _env_bool("MT5_AUTOTRADER_EXECUTE") and not dry_run
    state = _load_state()
    state["runs"] = int(state.get("runs", 0)) + 1
    _save_state(state)

    with _single_instance():
        client = get_client()
        client.ensure_connected()
        account = _account_guard(client)
        _news_guard()
        status = _status_guard()
        logger.info(
            "cycle=%d account=%s server=%s equity=%.2f execute=%s",
            state["runs"],
            account.get("login"),
            account.get("server"),
            float(account.get("equity", 0.0) or 0.0),
            execute,
        )

        for symbol in SYMBOL_CONFIG:
            bar = _bar_key(client, symbol)
            if state["last_bar"].get(symbol) == bar:
                logger.info("%s skipped: H1 bar already evaluated (%s)", symbol, bar)
                continue

            if client.positions(symbol) or client.pending_orders(symbol):
                logger.info("%s skipped: existing position or pending order", symbol)
                state["last_bar"][symbol] = bar
                _save_state(state)
                continue

            history_error = _history_error(client, symbol)
            if history_error:
                logger.info("%s skipped: %s", symbol, history_error)
                state["last_bar"][symbol] = bar
                _save_state(state)
                continue

            plan = _plan_for(symbol)
            state["last_plan"][symbol] = {
                "bar": bar,
                "plan": _short_plan(plan),
            }
            state["last_bar"][symbol] = bar
            _save_state(state)

            rejections = _plan_rejections(client, symbol, plan)
            if rejections:
                logger.info("%s rejected: %s", symbol, " | ".join(rejections))
                continue

            verdict = gate(
                symbol,
                plan.action,
                float(plan.lots),
                sl_price=float(plan.sl),
                sl_pips=float(plan.sl_pips),
                confidence=float(plan.confidence),
            )
            if not verdict.get("allowed"):
                logger.info(
                    "%s compliance veto: %s",
                    symbol,
                    " | ".join(verdict.get("violations", [])),
                )
                continue

            if not execute:
                logger.warning(
                    "%s DRY RUN candidate: %s %.2f lots entry=%s SL=%s TP=%s",
                    symbol,
                    plan.action,
                    plan.lots,
                    plan.entry,
                    plan.sl,
                    plan.tp,
                )
                continue

            result = open_market(
                symbol,
                plan.action,
                float(plan.lots),
                sl=float(plan.sl),
                tp=float(plan.tp),
                comment=f"auto-{SYMBOL_CONFIG[symbol]['magic_label']}",
                confidence=float(plan.confidence),
            )
            state["last_order"][symbol] = {
                "bar": bar,
                "accepted": result.accepted,
                "ticket": result.ticket,
                "retcode": result.retcode,
                "error": result.error,
            }
            _save_state(state)
            if result.accepted:
                logger.warning(
                    "%s ORDER ACCEPTED ticket=%s action=%s lots=%s entry=%s SL=%s TP=%s",
                    symbol,
                    result.ticket,
                    plan.action,
                    plan.lots,
                    plan.entry,
                    plan.sl,
                    plan.tp,
                )
                try:
                    from mt5_mcp.visual_notify import popup

                    popup(
                        f"[MT5-PROP] {symbol} {plan.action} filled",
                        f"ticket #{result.ticket}\n"
                        f"lots={plan.lots} entry={plan.entry}\n"
                        f"SL={plan.sl} TP={plan.tp}\n"
                        f"conf={plan.confidence:.0f}%",
                    )
                except Exception:  # noqa: BLE001 - notifications must never crash the runner
                    pass
            else:
                logger.error("%s order rejected after preflight: %s", symbol, result.error)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one scan (the scheduled-task mode)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="evaluate and log candidates without sending orders",
    )
    args = parser.parse_args()
    try:
        return run_once(dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - scheduled task must fail closed
        _setup_logging()
        logger.exception("cycle failed closed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

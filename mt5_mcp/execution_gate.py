"""Single choke point for every order that OPENS a position.

Post-mortem 2026-10-02 (live FundingPips SIM, -556 USD on 213 closed
positions): 129 positions were opened by paths that never saw compliance,
88% carried magic 0, so journal/trade records could not attribute them, and
the consensus allowlist was consulted by one pipeline only.  Anything that
reaches ``MetaTrader5.order_send`` / ``MT5Client.order_send`` now passes
through ``require()``.

Design rules:

* Only OPENING requests are gated.  Closes (``position`` set, CLOSE_BY),
  SL/TP modifications and pending-order cancels are never blocked - a
  losing trade must always be closable.
* Fail-closed: a missing magic id, a missing comment, an empty allowlist,
  an exception inside any sub-check, or a veto from consensus/compliance
  all reject the order.
* No bypass env var.  To permit a symbol again, fix the allowlist in
  settings.json deliberately (and re-baseline settings_guard).
* Forward-test log mode requires a named UTC window (maximum seven days)
  and no strict quality/performance/prop policy. The hot forward_test.stop
  marker restores enforcement. Every opening attempt is audited; permission
  is not broker acceptance. Audit failure blocks log-mode sends.
  ``check()`` itself is mode-agnostic.

Raw senders (``run_telegram_bot.py`` and friends) must call ``require()``
themselves before their own ``mt5.order_send``; ``MT5Client.order_send``
calls it automatically for every process.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mt5_mcp.common import (
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_ACTION_SLTP,
)
from mt5_mcp.compliance import is_synthetic_symbol

logger = logging.getLogger("mt5_mcp.execution_gate")

MODULE_DIR = Path(__file__).resolve().parent
GATE_LOG = MODULE_DIR / "memory" / "execution_gate.jsonl"
LIVE_STATS_PATH = MODULE_DIR / "memory" / "live_stats.json"

TRADE_ACTION_MODIFY = 7
TRADE_ACTION_CLOSE_BY = 10

# A symbol is suspended only once live stats show a real sample of losses.
SUSPEND_MIN_TRADES = int(os.getenv("EXECUTION_GATE_SUSPEND_TRADES", "20"))

_BUY_TYPES = {
    ORDER_TYPE_BUY,
    ORDER_TYPE_BUY_LIMIT,
    ORDER_TYPE_BUY_STOP,
}


class OrderBlocked(Exception):
    """Raised when the execution gate rejects an opening order."""


class RawMt5Adapter:
    """Client-shaped facade over a raw ``MetaTrader5`` module handle.

    Scripts that call ``mt5.order_send`` themselves (possibly on a second
    terminal, e.g. the Deriv account) must not construct an ``MT5Client``:
    that would try to attach to a different terminal.  This adapter gives the
    gate read-only access to the account the raw sender is actually on.
    """

    def __init__(self, mt5module: Any) -> None:
        self._mt5 = mt5module

    @staticmethod
    def _dict(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if hasattr(value, "_asdict"):
            return dict(value._asdict())
        if hasattr(value, "__dict__"):
            return dict(vars(value))
        return {}

    def account_info(self) -> dict[str, Any]:
        return self._dict(self._mt5.account_info())

    def terminal_info(self) -> dict[str, Any]:
        return self._dict(self._mt5.terminal_info())

    def symbol_info(self, symbol: str) -> dict[str, Any]:
        return self._dict(self._mt5.symbol_info(symbol))

    def tick(self, symbol: str) -> dict[str, Any]:
        return self._dict(self._mt5.symbol_info_tick(symbol))

    def rates(self, symbol: str, timeframe: str, count: int):
        import pandas as pd
        from mt5_mcp.common import TIMEFRAME_MT5

        tf = getattr(self._mt5, "TIMEFRAME_" + timeframe, TIMEFRAME_MT5[timeframe])
        tick = self.tick(symbol)
        stamp = int(tick.get("time") or time.time())
        start = stamp - count * TIMEFRAME_MT5[timeframe] * 60
        data = self._mt5.copy_rates_range(symbol, tf, datetime.fromtimestamp(start,timezone.utc),
                                        datetime.fromtimestamp(stamp,timezone.utc))
        if data is None or len(data) == 0:
            data = self._mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if data is None or len(data) == 0:
            raise ValueError("rates unavailable")
        frame = pd.DataFrame(data)
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        return frame

    def positions(self, symbol: str = "") -> list[dict[str, Any]]:
        found = (
            self._mt5.positions_get(symbol=symbol) if symbol else self._mt5.positions_get()
        )
        if found is None:
            raise ValueError("Open-position evidence unavailable")
        return [self._dict(p) for p in found]

    def pending_orders(self, symbol: str = "") -> list[dict[str, Any]]:
        found = self._mt5.orders_get(symbol=symbol) if symbol else self._mt5.orders_get()
        if found is None:
            raise ValueError("Pending-order evidence unavailable")
        return [self._dict(o) for o in found]

    def account_deals_since(self, epoch_seconds: int) -> list[dict[str, Any]]:
        found = self._mt5.history_deals_get(int(epoch_seconds),int(time.time())+86400)
        if found is None:
            raise ValueError("Account history unavailable")
        return [self._dict(d) for d in found]

    def deals_since(self, epoch_seconds: int, symbol: str = "") -> list[dict[str, Any]]:
        found = self._mt5.history_deals_get(int(epoch_seconds), int(time.time()) + 86400)
        out: list[dict[str, Any]] = []
        for deal in found or ():
            deal = self._dict(deal)
            if int(deal.get("type") or 0) in (0, 1, 2):
                out.append(deal)
        return out

    def market_open(self, symbol: str) -> tuple[bool, str]:
        info = self.symbol_info(symbol)
        if not info:
            return False, f"symbol '{symbol}' not found"
        if int(info.get("trade_mode", 0) or 0) == 1:
            return False, "trading disabled for this symbol"
        if datetime.now(timezone.utc).weekday() >= 5 and not is_synthetic_symbol(symbol):
            return False, "market closed (weekend)"
        tick = self.tick(symbol)
        tick_time = int(tick.get("time", 0) or 0)
        if tick_time and time.time() - tick_time > 900:
            return False, f"market closed (no fresh quotes for {int(time.time() - tick_time)}s)"
        return True, "market open"

    def server_time(self) -> datetime:
        value = int(self.terminal_info().get("time", 0) or 0)
        return datetime.fromtimestamp(value, tz=timezone.utc)


def send_raw(mt5_module, request: dict[str, Any], source: str = ""):
    """Apply the same durable admission latch to the raw watcher fallback."""
    from mt5_mcp.prop_risk import serialized_admission, mark_sending, record_result

    @serialized_admission
    def dispatch(client, order):
        require(order,client=client,source=source)
        mark_sending()
        result = mt5_module.order_send(order)
        record_result(client._dict(result) if result is not None else {})
        return result

    return dispatch(RawMt5Adapter(mt5_module),request)


def classify(request: dict[str, Any]) -> str:
    """Classify an MT5 request as open|close|modify|cancel."""
    try:
        action = int(request.get("action") or 0)
    except (TypeError, ValueError):
        return "open"  # malformed -> gate it
    if action in (TRADE_ACTION_SLTP, TRADE_ACTION_MODIFY):
        return "modify"
    if action == TRADE_ACTION_REMOVE:
        return "cancel"
    if action == TRADE_ACTION_CLOSE_BY:
        return "close"
    if action in (TRADE_ACTION_DEAL, TRADE_ACTION_PENDING):
        position = request.get("position")
        try:
            return "close" if int(position or 0) else "open"
        except (TypeError, ValueError):
            return "open"
    return "open"  # unknown action -> gate it


def side_of(request: dict[str, Any]) -> str:
    try:
        order_type = int(request.get("type") or 0)
    except (TypeError, ValueError):
        return "BUY"
    return "BUY" if order_type in _BUY_TYPES else "SELL"


def _sl_price(request: dict[str, Any]) -> float | None:
    try:
        sl = float(request.get("sl") or 0.0)
    except (TypeError, ValueError):
        return None
    return sl or None


def _lots(request: dict[str, Any]) -> float:
    try:
        return float(request.get("volume") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def live_stats() -> dict[str, dict[str, Any]]:
    """Per-symbol live performance written by mt5_mcp.journal_reconcile."""
    try:
        data = json.loads(LIVE_STATS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    symbols = data.get("symbols") if isinstance(data, dict) else None
    if isinstance(symbols, dict):
        return symbols
    if isinstance(data, dict):
        return data
    return {}


def check(
    request: dict[str, Any],
    client: Any = None,
    source: str = "",
) -> list[str]:
    """Return gate violations for *request* ([] means allowed).

    Non-opening requests always return [] so exits stay executable.
    Any internal error is reported as a violation (fail-closed).
    """
    if classify(request) != "open":
        if classify(request) == "modify":
            from mt5_mcp.prop_risk import modification_violations
            return modification_violations(request,client)
        return []

    violations: list[str] = []
    try:
        _check_open(request, client, source, violations)
    except Exception as exc:  # noqa: BLE001 - fail-closed by design
        logger.exception("execution gate crashed (blocking order)")
        violations.append(f"execution gate error (fail-closed): {exc}")
    return violations


def _check_open(
    request: dict[str, Any],
    client: Any,
    source: str,
    violations: list[str],
) -> None:
    from mt5_mcp.config import get_settings

    symbol = str(request.get("symbol") or "").strip()
    side = side_of(request)
    lots = _lots(request)
    sl = _sl_price(request)
    upper = symbol.upper()

    from mt5_mcp import entry_quality
    quality_result = entry_quality.check(request, client)
    violations.extend(quality_result.get("violations", []))

    try:
        from mt5_mcp.learning_loop import check_entry
        learned = check_entry(upper, side, setup=str(request.get("comment") or source))
        violations.extend(learned.get("violations", []))
    except Exception as exc:
        logger.warning("learning gate unavailable: %s", exc)
        violations.append("learning: completed-trade evidence unavailable")

    if not symbol:
        violations.append("opening order has no symbol")
        return

    magic = request.get("magic", 0)
    try:
        magic_ok = int(magic or 0) != 0
    except (TypeError, ValueError):
        magic_ok = False
    if not magic_ok:
        violations.append(
            "opening order has no magic id (ungated sender) - tag every order "
            "with MAGIC so journal, compliance and reporting can attribute it"
        )

    if not str(request.get("comment") or "").strip():
        violations.append("opening order has no comment (ungated sender)")

    risk = get_settings().risk
    blocked = {str(s).upper() for s in (risk.blocked_symbols or [])}
    if upper in blocked:
        violations.append(f"{upper} is on the blocked list")

    # Trading-hours filter (UTC hour).  Recorded always; enforced only
    # while risk.reject_orders is true.
    hours = [int(h) for h in (getattr(risk, "allowed_hours_utc", None) or [])]
    if hours:
        hour = datetime.now(timezone.utc).hour
        if hour not in hours:
            violations.append(
                f"hour {hour:02d} UTC outside allowed trading hours "
                f"{sorted(hours)} (risk.allowed_hours_utc)"
            )

    # Setup-family filter: desk scan orders carry comments like
    # "desk-rising_channel" / "desk:double_top".  Recorded always;
    # enforced only while risk.reject_orders is true.
    prefixes = tuple(
        str(p).lower()
        for p in (getattr(risk, "blocked_setup_prefixes", None) or [])
    )
    comment = str(request.get("comment") or "").strip()
    if prefixes and comment and comment.lower().startswith(prefixes):
        violations.append(
            f"setup comment '{comment}' matches blocked setup prefix(es) "
            f"{list(prefixes)} (risk.blocked_setup_prefixes)"
        )

    # Allowlist: fail-closed.  An empty list permits nothing.
    from mt5_mcp import consensus

    cfg = consensus._cfg()
    allow = {str(s).upper() for s in (cfg.get("allowed_symbols") or [])}
    if not allow:
        violations.append(
            "symbol allowlist is empty (fail-closed): no symbol has proven "
            "edge, so no new position may be opened"
        )
    elif upper not in allow:
        violations.append(
            f"{upper} not in allowlist (no proven live/backtested edge)"
        )

    # Evidence-based suspension from reconciled broker history.
    stat = live_stats().get(upper)
    if isinstance(stat, dict):
        trades = int(stat.get("trades") or 0)
        net = float(stat.get("net") or 0.0)
        if trades >= SUSPEND_MIN_TRADES and net < 0:
            violations.append(
                f"{upper} suspended by live stats: {trades} trades, "
                f"net {net:+.2f} (needs >=0 over {SUSPEND_MIN_TRADES}+ trades)"
            )

    verdict = consensus.check(upper, side, source or "execution_gate")
    violations.extend(str(v) for v in verdict.get("violations", []))

    from mt5_mcp.compliance import gate as compliance_gate

    cf = compliance_gate(
        upper,
        side,
        lots,
        sl_price=sl,
        confidence=None,
        entry_price=float(request.get("price") or 0) or None,
        client=client,
    )
    violations.extend(f"compliance: {v}" for v in cf.get("violations", []))
    if int(request.get("action",1)) == 5 and get_settings().prop_risk.enabled:
        try:
            from mt5_mcp.prop_risk import stop_risk
            pending_risk = stop_risk(client.symbol_info(upper),request["price"],sl,lots,0 if side=="BUY" else 1)
            pct = get_settings().performance_risk.losing_percent
            if pending_risk > float(client.account_info()["equity"])*pct/100:
                violations.append("Pending risk must fit the losing-mode budget even before a regime switch")
        except Exception:
            violations.append("Pending risk cannot be verified")
    try:
        entry_quality.capture(request, quality_result, violations)
    except Exception as exc:
        logger.warning("paper candidate capture failed: %s", exc)
        violations.append("quality: paper audit unavailable")


def require(
    request: dict[str, Any],
    client: Any = None,
    source: str = "",
) -> None:
    """Gate *request* and raise :class:`OrderBlocked` when it may not run.

    In bounded forward-test log mode violations
    are audited with ``event="violation"`` and the order is allowed
    through, except learned pauses and unavailable learning evidence, which
    always reject new entries. Existing prop modification rules stay enforced;
    closes and cancels remain available.
    """
    violations = check(request, client=client, source=source)
    if classify(request) != "open" and not violations:
        return

    from mt5_mcp.config import rejections_enabled

    # Learned abstention is an entry rule, including during forward-test log mode.
    enforce = rejections_enabled() or any(v.startswith(("learning:", "quality:")) for v in violations)
    blocked = bool(violations) and enforce
    symbol = str(request.get("symbol") or "?")
    side = side_of(request)
    recorded = _audit(
        symbol,
        side,
        _lots(request),
        source,
        violations,
        event="blocked" if blocked else "violation" if violations else "allowed",
        mode="enforce" if enforce else "log",
        sent=not blocked,
        request=request,
    )
    if not recorded and not enforce:
        raise OrderBlocked("forward-test audit unavailable; opening order blocked")
    if blocked:
        logger.warning(
            "execution gate BLOCKED %s %s: %s",
            symbol,
            side,
            "; ".join(violations),
        )
        raise OrderBlocked("; ".join(violations))
    if not violations:
        return
    logger.warning(
        "execution gate LOG-MODE sending %s %s despite: %s",
        symbol,
        side,
        "; ".join(violations),
    )


def _audit(
    symbol: str,
    side: str,
    lots: float,
    source: str,
    violations: list[str],
    *,
    event: str = "blocked",
    mode: str = "enforce",
    sent: bool = False,
    request: dict[str, Any] | None = None,
) -> bool:
    try:
        from mt5_mcp.config import get_settings
        test = get_settings().forward_test
        GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with GATE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "schema_version": 2,
                        "attempt_id": uuid.uuid4().hex,
                        "test_id": test.test_id,
                        "test_starts_at": test.starts_at,
                        "test_expires_at": test.expires_at,
                        "setup": str((request or {}).get("comment") or ""),
                        "magic": (request or {}).get("magic"),
                        "action": (request or {}).get("action"),
                        "ts": datetime.now(timezone.utc).isoformat(
                            timespec="seconds"
                        ),
                        "epoch": int(time.time()),
                        "event": event,
                        "mode": mode,
                        # require runs BEFORE order_send; acceptance/fill is unknown.
                        "send_permitted": sent,
                        "sent": None if sent else False,
                        "rules_allowed": not violations,
                        "symbol": symbol,
                        "side": side,
                        "lots": lots,
                        "source": source or "unknown",
                        "violations": violations,
                    }
                )
                + "\n"
            )
        return True
    except Exception as exc:  # strict policy remains usable if measurement fails
        logger.warning("gate audit write failed: %s", exc)
        return False

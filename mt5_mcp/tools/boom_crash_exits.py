"""Chandelier trailing exit manager for the Boom/Crash spike-drift watcher.

Replaces the fixed-TP exit on live watcher positions (research 2026-08-13,
``memory/research_lab/report_2026-08-13.md``, Structural Idea B):

* the trail arms once unrealized profit reaches +1R (entry +/- risk)
* while armed, the stop is set to ``highest-high-since-entry - N x ATR`` for
  longs (``lowest-low + N x ATR`` for shorts), N = 1.0-1.5 ATR (default 1.0)
* the stop is only ever moved toward profit (never away); if between cycles
  price retraces past the chandelier line the position is closed at market
* no fixed TP is placed on new watcher orders (``tp_price`` /
  ``tp_price_drift`` stay informational)
* the 1R partial exit from the strategy spec is kept: close
  ``partial_exit_fraction`` of the position once +1R is reached

Per-position state (original SL captured before the first trail, partial /
arm flags) is persisted to ``logs/boom_crash_exit_state.json`` so a watcher
restart cannot double-fire the partial exit or lose the original risk.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from mt5_mcp.common import ORDER_TYPE_BUY, BOOM_CRASH_SYMBOLS
from mt5_mcp.config import get_settings
from mt5_mcp.indicators import atr
from mt5_mcp.tools.boom_crash_strategy import (
    PARTIAL_EXIT_FRACTION,
    TRAIL_ARM_R,
    TRAIL_ATR_DISTANCE,
    TRAIL_ATR_DISTANCE_MAX,
    TRAIL_ATR_DISTANCE_MIN,
    mt5_symbol,
)
from mt5_mcp.tools.trades import MAGIC, close_position, modify_sl_tp, partial_close
from mt5_mcp.telegram_alerts import send_telegram
from mt5_mcp.trade_events import emit as emit_event

logger = logging.getLogger("boom_crash_exits")

# Persisted exit state next to the watcher logs.
_STATE_PATH = (
    Path(__file__).resolve().parents[2] / "logs" / "boom_crash_exit_state.json"
)

# M1 lookback for ATR and since-entry extremes.
_CANDLE_LOOKBACK = 240
# Clock-skew tolerance when matching candles to the position open time.
_OPEN_TIME_TOLERANCE_SECONDS = 60.0


@dataclass
class ExitConfig:
    """Chandelier exit parameters (researched defaults)."""

    trail_arm_r: float = TRAIL_ARM_R
    trail_atr_distance: float = TRAIL_ATR_DISTANCE
    trail_atr_min: float = TRAIL_ATR_DISTANCE_MIN
    trail_atr_max: float = TRAIL_ATR_DISTANCE_MAX
    partial_exit_fraction: float = PARTIAL_EXIT_FRACTION
    partial_exit_enabled: bool = True

    def trail_distance(self) -> float:
        """Trail distance in ATR units, clamped to the researched 1.0-1.5 range."""
        return min(max(float(self.trail_atr_distance), self.trail_atr_min), self.trail_atr_max)

    def arm_level(self, entry: float, risk: float, is_buy: bool) -> float:
        """Price level that arms the trail (+1R unrealized profit)."""
        return entry + risk if is_buy else entry - risk


def get_exit_config() -> ExitConfig:
    """Build the exit config from settings.json -> "boom_crash" (fallback to constants)."""
    try:
        bc = get_settings().boom_crash
        return ExitConfig(
            trail_arm_r=float(bc.trail_arm_r),
            trail_atr_distance=float(bc.trail_atr_distance),
            trail_atr_min=float(bc.trail_atr_min),
            trail_atr_max=float(bc.trail_atr_max),
            partial_exit_fraction=float(bc.partial_exit_fraction),
            partial_exit_enabled=bool(bc.partial_exit_enabled),
        )
    except Exception:
        return ExitConfig()


# ---- Per-ticket state ---------------------------------------------------

class ExitState:
    """Small persisted store of per-position exit context."""

    def __init__(self, path: Path | str = _STATE_PATH) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("exit state load failed: %s", exc)
        return {}

    def get(self, ticket: int) -> dict[str, Any] | None:
        with self._lock:
            return self._data.get(str(ticket))

    def set(self, ticket: int, **fields: Any) -> None:
        with self._lock:
            self._data.setdefault(str(ticket), {}).update(fields)

    def remove(self, ticket: int) -> None:
        with self._lock:
            self._data.pop(str(ticket), None)

    def prune(self, live_tickets: set[int]) -> None:
        """Drop state for positions that no longer exist."""
        with self._lock:
            stale = [k for k in self._data if int(k) not in live_tickets]
            for key in stale:
                self._data.pop(key, None)

    def save(self) -> None:
        with self._lock:
            payload = json.dumps(self._data, indent=2, default=str)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            logger.warning("exit state save failed: %s", exc)


_state: ExitState | None = None
_state_lock = threading.Lock()


def _get_state() -> ExitState:
    global _state
    with _state_lock:
        if _state is None:
            _state = ExitState()
        return _state


# ---- Market data --------------------------------------------------------

def _m1_candles(client: Any, symbol: str) -> list[dict[str, Any]]:
    """Newest M1 candles for an MT5 symbol name as records with epoch time."""
    try:
        frame = client.rates(symbol, "M1", _CANDLE_LOOKBACK)
    except Exception:
        return []
    if frame is None or len(frame) == 0:
        return []
    if isinstance(frame, pd.DataFrame):
        rows = frame.to_dict("records")
    else:
        rows = list(frame)
    out: list[dict[str, Any]] = []
    for row in rows:
        ts = row.get("time")
        if isinstance(ts, pd.Timestamp):
            ts = ts.to_pydatetime()
        if hasattr(ts, "timestamp"):
            ts = ts.timestamp()
        try:
            out.append(
                {
                    "time": float(ts),
                    "high": float(row.get("high")),
                    "low": float(row.get("low")),
                    "close": float(row.get("close")),
                }
            )
        except (TypeError, ValueError):
            continue
    return out


def _atr_now(candles: list[dict[str, Any]]) -> float:
    df = pd.DataFrame(candles)
    if len(df) < 2:
        return 0.0
    series = atr(df, period=14)
    return float(series.iloc[-1]) if len(series) else 0.0


def _digits(client: Any, symbol: str) -> int:
    try:
        return int(client.symbol_info(symbol).get("digits", 5))
    except Exception:
        return 5


def _is_watcher_position(position: dict[str, Any]) -> bool:
    """Only positions opened by the boom/crash watcher are exit-managed."""
    name = str(position.get("symbol", "") or "").upper().replace(" ", "")
    if not (name.startswith("BOOM") or name.startswith("CRASH")):
        return False
    magic = int(position.get("magic", 0) or 0)
    comment = str(position.get("comment", "") or "")
    return magic == MAGIC or "spike-drift" in comment


def _journal_context(
    symbol: str, entry: float, open_time: float
) -> dict[str, Any] | None:
    """Original (lots, sl) from the watcher's journal placement record.

    The watcher journals every order (``_record_order``) with the entry
    level, SL and lot size. Pending-order tickets differ from position
    tickets, so the record is matched by symbol + setup + entry + time.
    Falls back to None when unavailable (e.g. positions opened before the
    journaling change) - the persisted exit state is the primary source.
    """
    try:
        from datetime import datetime, timezone as _tz

        from mt5_mcp.memory.trade_guard import SETUP
        from mt5_mcp.memory.trade_journal import get_journal

        for trade in get_journal().list_trades(symbol=symbol, setup=SETUP, limit=100):
            raw = trade.get("open_time")
            if not raw:
                continue
            try:
                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            # Order was placed before/around the fill; allow a generous window.
            if abs(ts - open_time) > 600:
                continue
            trade_entry = trade.get("entry")
            if trade_entry and abs(float(trade_entry) - entry) > 0.01:
                continue
            sl = float(trade.get("sl") or 0.0)
            lots = float(trade.get("lots") or 0.0)
            if sl > 0:
                return {"sl_original": sl, "lots": lots}
    except Exception:
        pass
    return None


# ---- Exit management ----------------------------------------------------

def manage_position(
    client: Any,
    position: dict[str, Any],
    config: ExitConfig | None = None,
    state: ExitState | None = None,
    log: Optional[logging.Logger] = None,
) -> dict[str, Any]:
    """One management pass over a single open watcher position.

    Returns a summary dict with the position status and any actions taken
    (``partial_close`` / ``trail_sl``). Never moves the SL backward; never
    places a fixed TP.
    """
    log = log or logger
    config = config or get_exit_config()
    state = state or _get_state()

    ticket = int(position.get("ticket", 0) or 0)
    symbol = str(position.get("symbol", "") or "")
    is_buy = int(position.get("type", 0) or 0) == ORDER_TYPE_BUY
    entry = float(position.get("price_open", 0.0) or 0.0)
    current_sl = float(position.get("sl", 0.0) or 0.0)
    volume = float(position.get("volume", 0.0) or 0.0)
    open_time = float(position.get("time", 0.0) or 0.0)

    summary: dict[str, Any] = {"ticket": ticket, "symbol": symbol, "actions": [], "status": "watching"}

    # Capture the original risk before any trail moves the SL. The persisted
    # state is the primary source; the journal placement record is the
    # fallback when state was lost (e.g. after a watcher restart).
    st = state.get(ticket)
    if st is None:
        st = {
            "symbol": symbol,
            "entry": entry,
            "sl_original": current_sl,
            "lots": volume,
            "partial_done": False,
            "armed": False,
        }
        ctx = _journal_context(symbol, entry, open_time)
        if ctx:
            st["sl_original"] = ctx["sl_original"]
            st["lots"] = ctx["lots"] or st["lots"]
        state.set(ticket, **st)
    else:
        st = dict(st)

    risk = abs(entry - float(st.get("sl_original") or 0.0))
    if risk <= 0:
        summary["status"] = "no-risk"
        return summary
    arm_level = config.arm_level(entry, risk, is_buy)

    candles = _m1_candles(client, symbol)
    if not candles:
        summary["status"] = "no-data"
        return summary
    atr_now = _atr_now(candles)
    if atr_now <= 0:
        summary["status"] = "no-atr"
        return summary

    since_entry = [c for c in candles if c["time"] >= open_time - _OPEN_TIME_TOLERANCE_SECONDS]
    if not since_entry:
        since_entry = candles
    highest_high = max(c["high"] for c in since_entry)
    lowest_low = min(c["low"] for c in since_entry)

    tick = client.tick(symbol)
    bid = float(tick.get("bid", 0.0) or 0.0)
    ask = float(tick.get("ask", bid) or bid)
    price = bid if is_buy else ask
    crossed_arm = (price >= arm_level) if is_buy else (price <= arm_level)

    # 1R partial exit (strategy spec) - fires once, only while the position
    # still carries (approximately) its full original size, so a restart can
    # never double-fire it on the remainder.
    if config.partial_exit_enabled and not st.get("partial_done"):
        full_size = float(st.get("lots") or volume or 0.0)
        still_full = full_size <= 0 or volume >= full_size - 0.005
        if crossed_arm and still_full:
            close_vol = round(volume * float(config.partial_exit_fraction), 2)
            if 0 < close_vol < volume:
                result = partial_close(ticket, close_vol)
                if result.accepted:
                    st["partial_done"] = True
                    state.set(ticket, **st)
                    summary["actions"].append(f"partial +1R close {close_vol:.2f}")
                    log.info("%s ticket=%s partial +1R close %.2f lots", symbol, ticket, close_vol)
                    emit_event(
                        "deriv", "partial", symbol,
                        f"PARTIAL +1R: #{ticket} closed {close_vol:.2f} lots @ ~+1R",
                    )
                    send_telegram(
                        f"\U0001F4B0 <b>PARTIAL +1R {symbol}</b>\n"
                        f"#{ticket} closed {close_vol:.2f} lots at ~+1R\n"
                        f"<i>runner now trailing (chandelier)</i>"
                    )
                else:
                    summary["actions"].append(f"partial rejected: {result.error}")
                    log.warning("%s ticket=%s partial close rejected: %s", symbol, ticket, result.error)

    # Arm the chandelier at +1R, then trail the stop toward profit only.
    if not st.get("armed"):
        if crossed_arm:
            st["armed"] = True
            state.set(ticket, **st)
            summary["actions"].append("armed at +1R")
            log.info("%s ticket=%s chandelier armed at +1R (risk %.5f)", symbol, ticket, risk)
            summary["status"] = "armed"
            send_telegram(
                f"\u2705 <b>TRAIL ARMED {symbol}</b>\n"
                f"#{ticket} reached +1R - stop now trails 1.0 ATR"
            )
        else:
            summary["status"] = "watching"
    else:
        summary["status"] = "armed"

    if st.get("armed"):
        distance = config.trail_distance()
        if is_buy:
            candidate = highest_high - distance * atr_now
            improved = candidate > current_sl + 1e-9
            stop_line = max(candidate, current_sl)
            breached = bid <= stop_line + 1e-9
        else:
            candidate = lowest_low + distance * atr_now
            improved = candidate < current_sl - 1e-9
            stop_line = min(candidate, current_sl)
            breached = ask >= stop_line - 1e-9

        if breached:
            # The chandelier line (or the protective SL) is already through
            # the market: the exit has been triggered, so close at market.
            # This mirrors the backtest rule "bar low <= chandelier -> exit"
            # for the case where price retraced past the line between cycles.
            result = close_position(ticket)
            if result.accepted:
                summary["actions"].append(
                    f"chandelier exit at market (stop_line {stop_line:.5f})"
                )
                summary["status"] = "exited"
                log.info(
                    "%s ticket=%s chandelier exit at market (line %.5f)",
                    symbol, ticket, stop_line,
                )
                emit_event(
                    "deriv", "closed", symbol,
                    f"CHANDELIER EXIT: #{ticket} closed at market (line {stop_line:.5f})",
                )
            else:
                summary["actions"].append(f"exit rejected: {result.error}")
                log.warning("%s ticket=%s chandelier exit rejected: %s", symbol, ticket, result.error)
        elif improved and candidate > 0:
            result = modify_sl_tp(ticket, sl=round(candidate, _digits(client, symbol)))
            if result.accepted:
                summary["actions"].append(f"trail SL {current_sl:.5f} -> {candidate:.5f}")
                summary["status"] = "trailing"
                log.info(
                    "%s ticket=%s trail SL %.5f -> %.5f (N=%.2f ATR=%.5f extreme=%.5f)",
                    symbol, ticket, current_sl, candidate, distance, atr_now,
                    highest_high if is_buy else lowest_low,
                )
                emit_event(
                    "deriv", "trail", symbol,
                    f"TRAIL #{ticket} SL {current_sl:.5f} -> {candidate:.5f}",
                )
            else:
                summary["actions"].append(f"trail rejected: {result.error}")
                log.warning("%s ticket=%s trail modify rejected: %s", symbol, ticket, result.error)

    return summary


def run_exit_management(
    client: Any,
    log: Optional[logging.Logger] = None,
    config: ExitConfig | None = None,
    state: ExitState | None = None,
) -> dict[str, Any]:
    """Manage exits for every open boom/crash watcher position.

    Call this on every watcher scan cycle. Returns a summary of checked
    positions and actions taken.
    """
    log = log or logger
    config = config or get_exit_config()
    state = state or _get_state()

    try:
        positions = client.positions() or []
    except Exception as exc:
        log.warning("exit management: positions fetch failed: %s", exc)
        return {"checked": 0, "error": str(exc)}

    live = [p for p in positions if _is_watcher_position(p)]
    summary: dict[str, Any] = {
        "checked": len(live),
        "modified": 0,
        "partials": 0,
        "exits": 0,
        "statuses": {},
    }
    for position in live:
        try:
            result = manage_position(client, position, config, state, log)
            ticket = int(position.get("ticket", 0) or 0)
            summary["statuses"][str(ticket)] = result.get("status", "watching")
            for action in result.get("actions", []):
                if action.startswith("trail SL"):
                    summary["modified"] += 1
                elif action.startswith("partial"):
                    summary["partials"] += 1
                elif action.startswith("chandelier exit"):
                    summary["exits"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad position must not kill the cycle
            log.warning("exit management failed for %s: %s", position.get("symbol"), exc)

    state.prune({int(p.get("ticket", 0) or 0) for p in positions})
    state.save()
    return summary


def _mt5_names() -> set[str]:
    """MT5 symbol names of the boom/crash universe (for tests/filtering)."""
    return {mt5_symbol(s) for s in BOOM_CRASH_SYMBOLS}
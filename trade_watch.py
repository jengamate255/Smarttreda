"""Continuous Boom/Crash signal watcher.

The default mode is dry-run. Pass ``--live`` only when the watcher should
submit pending orders through the normal compliance and trade preflight gates.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mt5_mcp.common import BOOM_CRASH_SYMBOLS
from mt5_mcp.config import get_settings
from mt5_mcp.memory.trade_guard import SETUP, annotate_closed_trades, evaluate as learning_gate
from mt5_mcp.memory.trade_journal import get_journal
from mt5_mcp.self_tune import current_symbol, retrain_from_live
from mt5_mcp.tools.boom_crash_strategy import detect_spike, mt5_symbol
from mt5_mcp.tools.boom_crash_exits import run_exit_management
from mt5_mcp.tools.trades import open_market, pending_order
from mt5_mcp.common import TRADE_ACTION_DEAL, ORDER_TYPE_BUY, ORDER_TYPE_SELL, ORDER_TIME_DAY, ORDER_FILLING_FOK
from mt5_mcp.watcher_base import BaseWatcher


def _parse_args() -> argparse.Namespace:
    parser = BaseWatcher.base_arg_parser("Boom/Crash spike-drift watcher")
    parser.add_argument("--confidence-floor", type=float, default=60.0,
                        help="Minimum confidence to enter trade (default: 75, lowered from 90 for synthetics)")
    parser.add_argument("--max-spike-age", type=int, default=60)
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--spike-threshold-atr", type=float, default=2.5)
    parser.add_argument("--min-spike-ticks", type=int, default=8)
    parser.add_argument("--signal-expiry-seconds", type=float, default=20.0)
    parser.add_argument(
        "--symbols", type=str, default=None,
        help="comma-separated subset of BOOM/CRASH symbols (default: all or settings watchlist)",
    )
    parser.add_argument(
        "--micro", action="store_true",
        help="micro account mode: fixed 1%% risk, no equity curve/drawdown recovery, 2 pos/symbol, 3%% daily halt",
    )
    parser.add_argument(
        "--fixed-equity", type=float, default=None,
        help="override equity for lot sizing (e.g. 500 to simulate a $500 account)",
    )
    parser.set_defaults(log_file=str(ROOT / "logs" / "trade_watch.log"))
    parser.set_defaults(interval=15.0)
    return parser.parse_args()


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return [s.strip() for s in args.symbols.split(",") if s.strip()]
    settings = get_settings()
    watchlist = getattr(settings, "boom_crash", None)
    if watchlist and hasattr(watchlist, "watchlist") and watchlist.watchlist:
        return list(watchlist.watchlist)
    return list(BOOM_CRASH_SYMBOLS)


def _get_exposure_by_type(client) -> dict[str, list[str]]:
    """Get open positions grouped by Boom/Crash type.

    Returns dict: {"boom": [symbols], "crash": [symbols]}
    """
    exposure = {"boom": [], "crash": []}

    for pos in client.positions() or []:
        pos_symbol = str(pos.get("symbol", "")).lower()
        if "boom" in pos_symbol:
            exposure["boom"].append(pos.get("symbol", ""))
        elif "crash" in pos_symbol:
            exposure["crash"].append(pos.get("symbol", ""))

    return exposure


def _has_opposite_exposure(client, symbol: str, max_positions_per_type: int = 2) -> tuple[bool, str]:
    """Check if adding this symbol would exceed exposure limits.

    Improved: Allows up to max_positions_per_type of each kind (boom/crash).
    Only blocks if we'd exceed the limit.

    Returns (should_block, reason).
    """
    kind = "boom" if "boom" in symbol.lower() else (
        "crash" if "crash" in symbol.lower() else ""
    )
    if not kind:
        return False, ""

    exposure = _get_exposure_by_type(client)
    current_count = len(exposure[kind])

    # Check if we already have this exact symbol
    if symbol in exposure[kind]:
        return True, f"already have {symbol} position open"

    # Check if adding this would exceed the limit
    if current_count >= max_positions_per_type:
        return True, f"max {max_positions_per_type} {kind.upper()} positions already open: {', '.join(exposure[kind])}"

    return False, ""


def _record_order(*, ticket, symbol, side, entry, sl, tp, lots, signal) -> None:
    risk = abs(entry - sl)
    rr = abs(tp - entry) / risk if risk and tp is not None else None
    get_journal().record_trade(
        ticket=ticket,
        symbol=symbol,
        side=side,
        setup=SETUP,
        entry=entry,
        sl=sl,
        tp=tp,
        lots=lots,
        open_time=datetime.now(timezone.utc).isoformat(),
        reason=(
            f"{symbol} spike-drift {signal.retracement * 100:.1f}% stop order; "
            "directional drift with pullback entry"
        ),
        market_conditions=(
            f"spike_age_candles={int(signal.spike_age_candles)};"
            f"spike_size={signal.spike_size:.5f};atr={signal.atr:.5f};"
            f"confidence={signal.confidence:.1f}"
        ),
        confidence=signal.confidence,
        rr=rr,
    )


def _notify_fill(logger, api_symbol, side, result, signal, lots, entry, sl) -> None:
    from mt5_mcp.notifier import trade_filled
    try:
        trade_filled(
            symbol=api_symbol, side=side,
            entry=entry, lots=lots, sl=sl, tp=0.0,
            ticket=int(result.ticket) if result.ticket else None,
            confidence=signal.confidence, setup=SETUP, account="deriv",
        )
    except Exception as exc:
        logger.warning("%s notification failed: %s", api_symbol, exc)


class BoomCrashWatcher(BaseWatcher):
    SETUP = SETUP
    LOGGER_NAME = "boom_crash_watcher"
    DEFAULT_INTERVAL = 15.0

    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)
        micro = getattr(args, "micro", False)
        self.MAX_DAILY_LOSS_PERCENT = 3.0 if micro else 99.0
        self._micro = micro
        self._micro_max_per_symbol = 2 if micro else 1
        if micro:
            self.args.risk_percent = 1.0
        self._last_learning_sync = 0.0
        # Spike frequency tracking: {symbol: [spike_timestamps]}
        self._spike_history: dict[str, list[float]] = {}
        self._spike_window_minutes = 60  # Track spikes in last 60 minutes
        self._min_spike_frequency = 2    # Minimum spikes per window to consider "hot"

    def symbols(self) -> list[str]:
        return _resolve_symbols(self.args)

    def client_key(self) -> str | None:
        return "CRASH1000"

    def account_label(self) -> str:
        return "deriv"

    def volume_defaults(self) -> tuple[float, float]:
        return (0.1, 100.0)

    def detect_signal(self, symbol: str):
        return None

    def compliance_gate(self, symbol, signal):
        return True, []

    def build_order(self, signal):
        return "buy_stop", 0, 0, None, 0.0

    def pre_scan_cycle(self, client) -> None:
        if time.monotonic() - self._last_learning_sync < 60.0:
            return
        try:
            result = get_journal().sync_from_mt5(client, days=30)
            updated = annotate_closed_trades(self.args.max_spike_age)
            if result.get("added") or result.get("updated") or updated:
                self.logger.info(
                    "learning sync added=%s updated=%s annotated=%s",
                    result.get("added", 0), result.get("updated", 0), updated,
                )
        except Exception as exc:
            self.logger.warning("learning sync failed: %s", exc)
        try:
            tune = retrain_from_live()
            if tune:
                self.logger.info("self-tune: parameters updated for %d symbols", len(tune.get("symbols", {})))
        except Exception as exc:
            self.logger.warning("self-tune retrain failed: %s", exc)
        self._last_learning_sync = time.monotonic()

    def exit_management(self, client) -> None:
        try:
            summary = run_exit_management(client, self.logger)
            if summary.get("modified") or summary.get("partials"):
                self.logger.info(
                    "exit management: checked=%s modified=%s partials=%s",
                    summary.get("checked", 0), summary.get("modified", 0), summary.get("partials", 0),
                )
        except Exception as exc:
            self.logger.warning("exit management cycle failed: %s", exc)

    def _record_spike(self, symbol: str) -> None:
        """Record a spike event for frequency tracking."""
        now = time.time()
        if symbol not in self._spike_history:
            self._spike_history[symbol] = []

        self._spike_history[symbol].append(now)

        # Prune old spikes outside the window
        cutoff = now - (self._spike_window_minutes * 60)
        self._spike_history[symbol] = [
            ts for ts in self._spike_history[symbol] if ts > cutoff
        ]

    def _get_spike_frequency(self, symbol: str) -> int:
        """Get number of spikes in the tracking window."""
        if symbol not in self._spike_history:
            return 0

        now = time.time()
        cutoff = now - (self._spike_window_minutes * 60)
        return sum(1 for ts in self._spike_history[symbol] if ts > cutoff)

    def _is_spike_hot(self, symbol: str) -> bool:
        """Check if symbol has recent spike momentum."""
        freq = self._get_spike_frequency(symbol)
        return freq >= self._min_spike_frequency

    def scan_once(self, client, dry_run: bool = False) -> int:
        account = client.account_info()
        equity = float(account.get("equity", account.get("balance", 0.0)) or 0.0)
        risk = get_settings().risk
        if self._micro:
            budget = equity * 0.01
        else:
            budget = equity * float(risk.max_risk_percent) / 100.0 * 0.8
        placed = False

        for api_symbol in self.symbols():
            symbol = mt5_symbol(api_symbol)
            try:
                sym_tune = current_symbol(api_symbol)
                if sym_tune.get("enabled") is False:
                    self.logger.info("%s skipped: self-tuning disabled (negative edge)", api_symbol)
                    continue

                signal = detect_spike(
                    api_symbol,
                    lookback_minutes=self.args.lookback,
                    spike_threshold_atr=float(sym_tune.get("spike_threshold_atr", self.args.spike_threshold_atr)),
                    min_spike_ticks=self.args.min_spike_ticks,
                )
                if signal is None:
                    continue

                # Record this spike for frequency tracking
                self._record_spike(api_symbol)
                spike_freq = self._get_spike_frequency(api_symbol)

                # Log spike frequency for monitoring
                if spike_freq >= self._min_spike_frequency:
                    self.logger.info(
                        "%s spike detected: conf=%.1f freq=%d/%dmin (hot) reason=%s",
                        api_symbol, signal.confidence, spike_freq, self._spike_window_minutes,
                        signal.reason,
                    )
                else:
                    lvl = logging.INFO if signal.confidence <= 0 else logging.DEBUG
                    self.logger.log(
                        lvl,
                        "%s spike detected: conf=%.1f freq=%d/%dmin (cold, need %d) reason=%s",
                        api_symbol, signal.confidence, spike_freq, self._spike_window_minutes,
                        self._min_spike_frequency, signal.reason,
                    )

                signal_seen = time.monotonic()
                learned = learning_gate(
                    api_symbol,
                    base_max_spike_age=self.args.max_spike_age,
                    base_confidence_floor=self.args.confidence_floor,
                )
                if not learned["allowed"]:
                    self.logger.warning("%s skipped: %s", api_symbol, "; ".join(learned["violations"]))
                    continue

                # Apply spike frequency boost: hot symbols get +5 confidence
                confidence_floor = learned["confidence_floor"]
                self.logger.info("%s learning_gate: floor=%.1f max_age=%s allowed=%s",
                                 api_symbol, confidence_floor, learned["max_spike_age"], learned["allowed"])
                effective_confidence = signal.confidence
                if spike_freq >= self._min_spike_frequency:
                    effective_confidence += 5.0  # Boost for hot symbols
                    self.logger.info("%s spike frequency boost: %.1f -> %.1f",
                                     api_symbol, signal.confidence, effective_confidence)

                if effective_confidence < confidence_floor:
                    self.logger.info(
                        "%s skipped: confidence %.1f (effective %.1f) < floor %.1f",
                        api_symbol, signal.confidence, effective_confidence, confidence_floor,
                    )
                    continue
                if signal.spike_age_candles > learned["max_spike_age"]:
                    self.logger.info("%s skipped: spike age %s bars exceeds %s",
                                     api_symbol, signal.spike_age_candles, learned["max_spike_age"])
                    continue

                if time.monotonic() - signal_seen > self.args.signal_expiry_seconds:
                    self.logger.info("%s skipped: signal expired before revalidation", api_symbol)
                    continue
                fresh = detect_spike(
                    api_symbol,
                    lookback_minutes=self.args.lookback,
                    spike_threshold_atr=self.args.spike_threshold_atr,
                    min_spike_ticks=self.args.min_spike_ticks,
                )
                if fresh is None:
                    self.logger.info("%s skipped: signal expired before revalidation", api_symbol)
                    continue

                # Apply same frequency boost to revalidated signal
                fresh_effective = fresh.confidence
                if spike_freq >= self._min_spike_frequency:
                    fresh_effective += 5.0

                if fresh_effective < confidence_floor:
                    self.logger.info("%s skipped: revalidated confidence %.1f (effective %.1f) < floor %.1f",
                                    api_symbol, fresh.confidence, fresh_effective, confidence_floor)
                    continue
                if fresh.spike_age_candles > learned["max_spike_age"]:
                    self.logger.info("%s skipped: revalidated signal is stale", api_symbol)
                    continue
                entry_shift = abs(float(fresh.entry_price) - float(signal.entry_price))
                max_shift = max(float(fresh.atr), float(signal.atr), 1e-9) * 0.5
                if entry_shift > max_shift:
                    self.logger.info("%s skipped: entry moved %.5f during revalidation", api_symbol, entry_shift)
                    continue
                signal = fresh

                tick = client.tick(symbol)
                bid = float(tick.get("bid", 0.0))
                ask = float(tick.get("ask", bid))
                side = "BUY" if signal.drift_direction == "up" else "SELL"
                entry = float(signal.entry_price)
                sl = float(signal.sl_price)
                tp = None
                if self._micro:
                    micro_risk = 1.0
                else:
                    micro_risk = float(risk.max_risk_percent) * 0.8 * float(sym_tune.get("size_multiplier", 1.0))
                lots = self._lot_size(
                    client, symbol, entry, sl,
                    risk_percent=micro_risk,
                )
                if lots is None:
                    continue

                journal = get_journal()
                signal_id = str(uuid.uuid4())
                journal.log_signal(
                    signal_id=signal_id, symbol=api_symbol, side=side, setup=SETUP,
                    timeframe="M1", entry=entry, sl=sl, tp=tp, lots=lots,
                    confidence=signal.confidence,
                    setup_confidence=signal.confidence,
                    trend_confidence=signal.confidence,
                    zone_strength=signal.confidence,
                    confirmation_confidence=signal.confidence,
                    rr=signal.rr_used if hasattr(signal, "rr_used") else 1.5,
                    sl_pips=None, tp_pips=None,
                    atr=signal.atr, range_high=signal.spike_high, range_low=signal.spike_low,
                    atr_value=signal.atr, compactness=None,
                    trend_dir=signal.drift_direction,
                    market_conditions=f"spike_age_candles={signal.spike_age_candles};spike_size={signal.spike_size:.5f};atr={signal.atr:.5f}",
                    signal_time=datetime.now(timezone.utc).isoformat(),
                    filled=False,
                )

                self.logger.info(
                    "%s qualifying: conf=%.1f age=%s entry=%.5f sl=%.5f lots=%.2f",
                    api_symbol, signal.confidence, signal.spike_age_candles, entry, sl, lots,
                )

                if dry_run:
                    self.logger.info("%s dry-run: no order submitted", api_symbol)
                    return False

                if self._market_fallback(client, api_symbol, symbol, side, entry, sl, tp, lots, signal, signal_id):
                    placed = True
            except Exception as exc:
                self.logger.warning("%s scan error: %s", api_symbol, exc)
        return placed

    def _on_order_placed(self, result, api_symbol, symbol, side, entry, sl, tp, lots, signal, signal_id, journal, order_type) -> None:
        if result.ticket:
            _record_order(
                ticket=int(result.ticket), symbol=symbol, side=side,
                entry=entry, sl=sl, tp=tp, lots=lots, signal=signal,
            )
            journal.update_signal_outcome(
                signal_id, ticket=int(result.ticket),
                filled=True, trade_time=datetime.now(timezone.utc).isoformat(),
            )
        self.logger.info("%s order placed: ticket=%s retcode=%s", api_symbol, result.ticket, result.retcode)
        _notify_fill(self.logger, api_symbol, side, result, signal, lots, entry, sl)

    def _market_fallback(self, client, api_symbol, symbol, side, entry_level, sl, tp, lots, signal, signal_id=None) -> bool:
        max_pos = getattr(self, "_micro_max_per_symbol", 1)
        existing = [p for p in client.positions() if p.get("symbol") == symbol]
        if len(existing) >= max_pos:
            self.logger.info("%s skipped: already %d open position(s) on %s (limit %d)", api_symbol, len(existing), api_symbol, max_pos)
            return False

        tick = client.tick(symbol)
        bid = float(tick.get("bid", 0.0))
        ask = float(tick.get("ask", bid))
        atr = float(signal.atr) or 1e-9
        if side == "BUY":
            overshoot = ask - entry_level
            price = ask
        else:
            overshoot = entry_level - bid
            price = bid
        from mt5_mcp.config import get_settings
        max_overshoot = get_settings().quality.max_entry_drift_atr if get_settings().quality.enabled else 20.0
        if overshoot > atr * max_overshoot:
            self.logger.info(
                "%s skipped: market %.5f too far from entry %.5f (overshoot %.5f > %.5f)",
                api_symbol, price, entry_level, overshoot, atr * max_overshoot,
            )
            return False

        order_type = ORDER_TYPE_BUY if side == "BUY" else ORDER_TYPE_SELL
        request = {
            "action": TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lots,
            "type": order_type,
            "price": price,
            "sl": sl if sl is not None else 0.0,
            "tp": tp if tp is not None else 0.0,
            "deviation": 1000,
            "magic": 20260601,
            "comment": self.SETUP,
            "type_time": ORDER_TIME_DAY,
            "type_filling": ORDER_FILLING_FOK,
        }

        from mt5_mcp.execution_gate import OrderBlocked, send_raw
        result_dict = None
        for attempt in range(2):
            self.logger.info("%s order_send #%d: symbol=%s side=%s lots=%.2f price=%.5f sl=%.5f tp=%.5f",
                api_symbol, attempt + 1, symbol, side, lots, price, sl, tp)
            try:
                import MetaTrader5 as _mt5
                raw = send_raw(_mt5,request,source=self.SETUP)
                if raw is None:
                    self.logger.warning("%s mt5.order_send returned None: %s", api_symbol, _mt5.last_error())
                    result_dict = None
                else:
                    result_dict = {
                        "retcode": raw.retcode,
                        "deal": raw.deal,
                        "order": raw.order,
                        "volume": raw.volume,
                        "price": raw.price,
                        "comment": raw.comment,
                    }
                    self.logger.info("%s order_send result: retcode=%s ticket=%s", api_symbol, raw.retcode, raw.order)
            except OrderBlocked as e:
                self.logger.info("%s learned/execution gate blocked fallback: %s", api_symbol, e)
                return False
            except Exception as e:
                self.logger.warning("%s order_send exception: %s: %s", api_symbol, type(e).__name__, e)
                result_dict = None
            retcode = result_dict.get("retcode") if result_dict else None
            if retcode in (10009, 10008):
                break
            time.sleep(0.3)
            tick = client.tick(symbol)
            price = float(tick.get("ask", 0.0)) if side == "BUY" else float(tick.get("bid", 0.0))
            request["price"] = price

        if result_dict and result_dict.get("retcode") in (10009, 10008):
            ticket = int(result_dict.get("order", 0) or result_dict.get("deal", 0) or 0)
            _record_order(
                ticket=ticket, symbol=symbol, side=side,
                entry=price, sl=sl, tp=tp, lots=lots, signal=signal,
            )
            if signal_id:
                get_journal().update_signal_outcome(
                    signal_id, ticket=ticket,
                    filled=True, trade_time=datetime.now(timezone.utc).isoformat(),
                )
            self.logger.info("%s filled via direct order_send: ticket=%s retcode=%s", api_symbol, ticket, result_dict.get("retcode"))
            from mt5_mcp.notifier import trade_filled
            trade_filled(
                symbol=api_symbol, side=side, lots=lots,
                entry=price, sl=sl, tp=tp,
                ticket=ticket, source="boom_crash_watcher",
            )
            return True

        err = result_dict if result_dict else {}
        self.logger.warning("%s market fallback rejected: retcode=%s comment=%s",
            api_symbol, err.get("retcode"), err.get("comment", ""))
        return False


def main() -> int:
    args = _parse_args()
    import logging
    if getattr(args, "micro", False):
        logging.info(
            "=== MICRO MODE === risk=1.0%% daily_halt=3.0%% max_pos/symbol=2 symbols=%s",
            args.symbols or "default",
        )
    logging.info("Startup args: confidence_floor=%.1f max_spike_age=%s micro=%s", args.confidence_floor, args.max_spike_age, getattr(args, "micro", False))
    watcher = BoomCrashWatcher(args)
    return watcher.run()


if __name__ == "__main__":
    raise SystemExit(main())

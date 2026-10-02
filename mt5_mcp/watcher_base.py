"""Base class for strategy watcher processes.

Eliminates the duplicated scaffolding across trade_watch (Boom/Crash),
forex_breakout_watch, and btc_trend_watch. Subclasses provide only:

* symbol list + client key
* signal detection
* compliance gating
* order construction
* exit management

Everything else (connection, sizing, exposure check, notification dispatch,
daily loss breaker, main loop) lives here.
"""

from __future__ import annotations

import argparse
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from mt5_mcp.common import pip_size, pip_value_per_lot, round_to_step


@dataclass
class Signal:
    """A detected trade signal from a watcher strategy."""
    symbol: str
    side: str
    entry: float
    sl: float
    tp: float | None = None
    lots: float | None = None
    confidence: float = 0.0
    rr: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseWatcher(ABC):
    """Shared scaffolding for all strategy watcher processes.

    Subclass and implement the abstract methods to create a new watcher.
    """

    SETUP: str = "base_watcher"
    LOGGER_NAME: str = "watcher"
    DEFAULT_INTERVAL: float = 60.0
    DEFAULT_RISK_PERCENT: float = 0.25
    MAX_DAILY_LOSS_PERCENT: float = 3.0

    def __init__(self, args: argparse.Namespace | None = None) -> None:
        self.args = args or self._default_args()
        self.logger = self._make_logger()
        self._client = None
        self._daily_start_equity: float | None = None
        self._daily_halted = False
        self._state: dict[str, Any] = {}

    @abstractmethod
    def symbols(self) -> list[str]:
        """Return the list of symbols to scan."""

    @abstractmethod
    def client_key(self) -> str | None:
        """Return the client key for get_client(), or None for default."""

    @abstractmethod
    def detect_signal(self, symbol: str) -> Signal | None:
        """Detect a trade signal for the given symbol. Return None if no setup."""

    @abstractmethod
    def compliance_gate(self, symbol: str, signal: Signal) -> tuple[bool, list[str]]:
        """Run strategy-specific compliance checks. Return (allowed, violations)."""

    @abstractmethod
    def build_order(
        self, signal: Signal
    ) -> tuple[str, float, float, float | None, float]:
        """Return (order_type, price, sl, tp, lots) for the signal.

        order_type is "market" or "pending".
        """

    def exit_management(self, client: Any) -> None:
        """Manage open positions (trailing, partial exits). Default: no-op."""

    def pre_scan_cycle(self, client: Any) -> None:
        """Hook called before each scan cycle (learning sync, etc). Default: no-op."""

    def on_order_accepted(self, result: Any, signal: Signal) -> None:
        """Hook called after an order is accepted. Default: standard notification."""
        self._notify_order(result, signal)

    def account_label(self) -> str:
        """Return account label for event logging. Override for 'deriv' etc."""
        return "prop"

    def volume_defaults(self) -> tuple[float, float]:
        """Return (volume_min, volume_max) fallback defaults."""
        return (0.01, 100.0)

    # ── Shared infrastructure ─────────────────────────────────────────────

    def _default_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            live=False,
            once=False,
            interval=self.DEFAULT_INTERVAL,
            risk_percent=self.DEFAULT_RISK_PERCENT,
            log_file=None,
        )

    def _make_logger(self) -> logging.Logger:
        log = logging.getLogger(self.LOGGER_NAME)
        log.setLevel(logging.INFO)
        log.propagate = False
        if not log.handlers:
            log.addHandler(logging.StreamHandler())
        log_path = getattr(self.args, "log_file", None)
        if log_path:
            path = Path(log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(
                path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(fh)
        return log

    def _get_client(self) -> Any:
        from mt5_mcp.client import get_client, reset_client
        reset_client()
        key = self.client_key()
        self._client = get_client(key) if key else get_client()
        return self._client

    def _lot_size(
        self,
        client: Any,
        symbol: str,
        entry: float,
        sl: float,
        risk_percent: float | None = None,
    ) -> float | None:
        """Compute lot size from risk budget using tick-value math."""
        risk_pct = risk_percent or getattr(self.args, "risk_percent", self.DEFAULT_RISK_PERCENT)
        try:
            equity = float(client.account_info().get("equity", 0))
        except Exception:
            return None
        if equity <= 0:
            return None

        budget = equity * (risk_pct / 100.0)
        info = client.symbol_info(symbol)
        if not info:
            return None

        pip = pip_size(info)
        pv = pip_value_per_lot(info)
        if pip <= 0 or pv <= 0:
            return None

        # Spread never blocks, but it is real risk: widen the SL distance by
        # the live spread so wide-spread fills get smaller lots.
        spread_points = float(info.get("spread") or 0.0)
        point = float(info.get("point") or 0.0)
        sl_distance = abs(entry - sl) + spread_points * point
        if sl_distance <= 0:
            return None

        loss_per_lot = sl_distance / pip * pv
        if loss_per_lot <= 0:
            return None

        raw = budget / loss_per_lot
        vol_min, vol_max = self.volume_defaults()
        step = float(info.get("volume_step", 0.01))
        actual_min = float(info.get("volume_min", vol_min))
        actual_max = float(info.get("volume_max", vol_max))

        return round_to_step(raw, step, floor=actual_min, ceil=actual_max)

    @staticmethod
    def _has_exposure(client: Any, symbol: str) -> bool:
        """Check if there is already an open position or pending order."""
        positions = client.positions(symbol) or []
        orders = client.pending_orders(symbol) or []
        return bool(positions or orders)

    def _notify_order(self, result: Any, signal: Signal) -> None:
        """Standard notification dispatch after order acceptance."""
        from mt5_mcp import telegram_alerts, trade_events, visual_notify

        ticket = None
        entry = signal.entry
        if hasattr(result, "ticket"):
            ticket = result.ticket
        if hasattr(result, "details") and result.details:
            entry = float(result.details.get("price", entry))

        msg = (
            f"{signal.side} {signal.symbol} {signal.lots or 0:.2f} lots "
            f"@ {entry:.5g} SL={signal.sl:.5g}"
        )
        if signal.tp:
            msg += f" TP={signal.tp:.5g}"

        try:
            visual_notify.popup(f"{self.SETUP}: {signal.side} {signal.symbol}", msg)
        except Exception:
            pass

        try:
            html = telegram_alerts.trade_entry_msg(
                symbol=signal.symbol,
                side=signal.side,
                lots=signal.lots or 0,
                entry=entry,
                sl=signal.sl,
                tp=signal.tp,
                confidence=signal.confidence,
                ticket=ticket,
                setup=self.SETUP,
                rr=f"{signal.rr:.1f}" if signal.rr else "",
            )
            telegram_alerts.send_telegram(html)
        except Exception:
            pass

        try:
            trade_events.emit(
                account=self.account_label(),
                kind="order",
                symbol=signal.symbol,
                message=msg,
            )
        except Exception:
            pass

    def _check_daily_loss(self, client: Any) -> bool:
        """Return True if daily loss limit is breached (halt trading)."""
        try:
            equity = float(client.account_info().get("equity", 0))
        except Exception:
            return False

        if self._daily_start_equity is None:
            self._daily_start_equity = equity
            return False

        if self._daily_halted:
            return True

        if equity <= 0 or self._daily_start_equity <= 0:
            return False

        loss_pct = (self._daily_start_equity - equity) / self._daily_start_equity * 100
        if loss_pct >= self.MAX_DAILY_LOSS_PERCENT:
            self._daily_halted = True
            self.logger.error(
                "daily loss limit hit: %.2f%% (limit %.2f%%)", loss_pct, self.MAX_DAILY_LOSS_PERCENT
            )
            try:
                from mt5_mcp import telegram_alerts
                telegram_alerts.send_telegram(
                    telegram_alerts.halt_msg(
                        reason="Daily loss limit",
                        detail=f"Loss: {loss_pct:.2f}% (limit {self.MAX_DAILY_LOSS_PERCENT:.1f}%)",
                    )
                )
            except Exception:
                pass
            return True
        return False

    def _reset_daily_if_new_day(self) -> None:
        """Reset the daily equity baseline at midnight UTC."""
        now = datetime.now(timezone.utc)
        today = now.date()
        last = getattr(self, "_last_day", None)
        if last != today:
            self._daily_start_equity = None
            self._daily_halted = False
            self._last_day = today

    def scan_once(self, client: Any, dry_run: bool = False) -> int:
        """Run one scan cycle across all symbols. Return number of orders placed."""
        placed = 0
        for symbol in self.symbols():
            if self._has_exposure(client, symbol):
                continue

            signal = self.detect_signal(symbol)
            if signal is None:
                continue

            allowed, violations = self.compliance_gate(symbol, signal)
            if not allowed:
                self.logger.info(
                    "compliance blocked %s %s: %s", symbol, signal.side, violations
                )
                continue

            order_type, price, sl, tp, lots = self.build_order(signal)
            if lots is None or lots <= 0:
                self.logger.info("sizing returned no lots for %s", symbol)
                continue

            signal.lots = lots

            if dry_run:
                self.logger.info(
                    "dry-run: would place %s %s %s %.2f lots SL=%.5g TP=%s",
                    order_type, symbol, signal.side, lots, sl, tp or 0,
                )
                placed += 1
                continue

            result = self._place_order(client, order_type, symbol, signal.side, lots, price, sl, tp)
            if result and getattr(result, "accepted", False):
                self.on_order_accepted(result, signal)
                placed += 1

        return placed

    def _place_order(
        self,
        client: Any,
        order_type: str,
        symbol: str,
        side: str,
        lots: float,
        price: float,
        sl: float,
        tp: float | None,
    ) -> Any:
        """Place a market or pending order."""
        from mt5_mcp.tools.trades import open_market, pending_order

        if order_type == "market":
            return open_market(
                symbol, side, lots, sl=sl, tp=tp,
                comment=self.SETUP, confidence=None,
            )
        else:
            return pending_order(
                symbol, side, order_type, lots, price, sl=sl, tp=tp,
                comment=self.SETUP,
            )

    def run(self) -> int:
        """Main loop: connect, scan, manage, sleep. Handles KeyboardInterrupt."""
        self.logger.info(
            "starting %s (interval=%.0fs, risk=%.2f%%, symbols=%s)",
            self.SETUP,
            getattr(self.args, "interval", self.DEFAULT_INTERVAL),
            getattr(self.args, "risk_percent", self.DEFAULT_RISK_PERCENT),
            self.symbols(),
        )

        client = self._get_client()
        dry_run = not getattr(self.args, "live", False)

        try:
            while True:
                self._reset_daily_if_new_day()

                try:
                    client.ensure_connected()
                except Exception as exc:
                    self.logger.warning("connection lost: %s", exc)
                    time.sleep(10)
                    continue

                if self._check_daily_loss(client):
                    time.sleep(max(1.0, getattr(self.args, "interval", self.DEFAULT_INTERVAL)))
                    continue

                self.pre_scan_cycle(client)
                self.scan_once(client, dry_run=dry_run)
                self.exit_management(client)

                if getattr(self.args, "once", False):
                    return 0

                time.sleep(max(1.0, getattr(self.args, "interval", self.DEFAULT_INTERVAL)))

        except KeyboardInterrupt:
            self.logger.info("watcher stopped")
            return 0

    @staticmethod
    def base_arg_parser(description: str = "Strategy watcher") -> argparse.ArgumentParser:
        """Return an ArgumentParser with the shared watcher args."""
        p = argparse.ArgumentParser(description=description)
        p.add_argument("--live", action="store_true", help="execute real orders")
        p.add_argument("--once", action="store_true", help="run one cycle and exit")
        p.add_argument("--interval", type=float, default=60.0, help="poll interval seconds")
        p.add_argument("--risk-percent", type=float, default=0.25, help="risk per trade %%")
        p.add_argument("--log-file", type=str, default=None, help="log file path")
        return p

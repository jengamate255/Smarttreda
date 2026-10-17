"""MT5 Adapter — stub interface for MetaTrader 5 Python API with kill switch."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


class KillSwitchReason(str, Enum):
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    MAX_DRAWDOWN = "max_drawdown"
    ABNORMAL_EXECUTION = "abnormal_execution"
    DATA_FEED_FAILURE = "data_feed_failure"
    MODEL_FAILURE = "model_failure"


class KillSwitchTriggered(Exception):
    def __init__(self, reason: KillSwitchReason, details: str = "") -> None:
        self.reason = reason
        self.details = details
        super().__init__(f"Kill switch triggered: {reason.value} — {details}")


@dataclass
class KillSwitchConfig:
    daily_loss_limit: float = 0.03
    max_drawdown: float = 0.10
    max_slippage_pips: float = 5.0
    max_order_rejections: int = 5
    data_feed_timeout_seconds: float = 30.0
    model_failure_threshold: int = 3


@dataclass
class KillSwitchState:
    triggered: bool = False
    reason: KillSwitchReason | None = None
    triggered_at: datetime | None = None
    details: str = ""
    daily_pnl: float = 0.0
    peak_equity: float = 0.0
    current_drawdown: float = 0.0
    consecutive_rejections: int = 0
    consecutive_model_failures: int = 0
    last_data_feed_time: datetime | None = None


@dataclass
class RateLimiter:
    max_requests: int = 60
    window_seconds: float = 1.0
    _timestamps: deque = field(default_factory=deque)

    def allow(self) -> bool:
        now = time.monotonic()
        while self._timestamps and self._timestamps[0] < now - self.window_seconds:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_requests:
            return False
        self._timestamps.append(now)
        return True

    def wait_time(self) -> float:
        now = time.monotonic()
        while self._timestamps and self._timestamps[0] < now - self.window_seconds:
            self._timestamps.popleft()
        if len(self._timestamps) < self.max_requests:
            return 0.0
        return self._timestamps[0] + self.window_seconds - now


class MT5Adapter:
    """Interface for MetaTrader 5 Python API.

    Stub implementation that simulates MT5 responses.
    Replace method bodies with actual mt5 calls for production.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self._connected = False
        self._account_info: dict[str, Any] = {
            "login": 0,
            "server": "",
            "balance": 10000.0,
            "equity": 10000.0,
            "margin": 0.0,
            "free_margin": 10000.0,
            "leverage": 100,
            "currency": "USD",
        }
        self._symbols: dict[str, dict[str, Any]] = {}
        self._positions: dict[str, dict[str, Any]] = {}
        self._orders: dict[str, dict[str, Any]] = {}
        self._order_history: list[dict[str, Any]] = []
        self._kill_config = KillSwitchConfig(
            daily_loss_limit=config.get("daily_loss_limit", 0.03),
            max_drawdown=config.get("max_drawdown", 0.10),
            max_slippage_pips=config.get("max_slippage_pips", 5.0),
            max_order_rejections=config.get("max_order_rejections", 5),
            data_feed_timeout_seconds=config.get("data_feed_timeout_seconds", 30.0),
            model_failure_threshold=config.get("model_failure_threshold", 3),
        )
        self._kill_state = KillSwitchState(
            peak_equity=self._account_info["balance"],
        )
        self._rate_limiter = RateLimiter(
            max_requests=config.get("rate_limit_max", 60),
            window_seconds=config.get("rate_limit_window", 1.0),
        )
        self._retry_attempts = config.get("retry_attempts", 3)
        self._retry_delay = config.get("retry_delay", 0.5)
        self._init_default_symbols()

    def _init_default_symbols(self) -> None:
        defaults = {
            "EURUSD": {"point": 0.00001, "digits": 5, "contract_size": 100000,
                       "spread": 15, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01},
            "GBPUSD": {"point": 0.00001, "digits": 5, "contract_size": 100000,
                       "spread": 20, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01},
            "USDJPY": {"point": 0.001, "digits": 3, "contract_size": 100000,
                       "spread": 15, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01},
            "GBPJPY": {"point": 0.001, "digits": 3, "contract_size": 100000,
                       "spread": 30, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01},
            "XAUUSD": {"point": 0.01, "digits": 2, "contract_size": 100,
                       "spread": 30, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01},
        }
        self._symbols = defaults

    # ------------------------------------------------------------------
    # Kill switch
    # ------------------------------------------------------------------

    def _check_kill_switch(self) -> None:
        if self._kill_state.triggered:
            return

        if self._kill_state.daily_pnl < 0:
            loss_ratio = abs(self._kill_state.daily_pnl) / self._account_info["balance"]
            if loss_ratio >= self._kill_config.daily_loss_limit:
                self._trigger_kill_switch(
                    KillSwitchReason.DAILY_LOSS_LIMIT,
                    f"Daily loss {loss_ratio:.4f} exceeds limit {self._kill_config.daily_loss_limit}",
                )
                return

        if self._kill_state.peak_equity > 0:
            drawdown = (self._kill_state.peak_equity - self._account_info["equity"]) / self._kill_state.peak_equity
            self._kill_state.current_drawdown = drawdown
            if drawdown >= self._kill_config.max_drawdown:
                self._trigger_kill_switch(
                    KillSwitchReason.MAX_DRAWDOWN,
                    f"Drawdown {drawdown:.4f} exceeds limit {self._kill_config.max_drawdown}",
                )
                return

        if self._kill_state.consecutive_rejections >= self._kill_config.max_order_rejections:
            self._trigger_kill_switch(
                KillSwitchReason.ABNORMAL_EXECUTION,
                f"Consecutive rejections {self._kill_state.consecutive_rejections} exceeds limit",
            )
            return

        if self._kill_state.last_data_feed_time is not None:
            elapsed = (datetime.now(timezone.utc) - self._kill_state.last_data_feed_time).total_seconds()
            if elapsed > self._kill_config.data_feed_timeout_seconds:
                self._trigger_kill_switch(
                    KillSwitchReason.DATA_FEED_FAILURE,
                    f"Data feed silence {elapsed:.1f}s exceeds timeout {self._kill_config.data_feed_timeout_seconds}s",
                )
                return

        if self._kill_state.consecutive_model_failures >= self._kill_config.model_failure_threshold:
            self._trigger_kill_switch(
                KillSwitchReason.MODEL_FAILURE,
                f"Consecutive model failures {self._kill_state.consecutive_model_failures} exceeds threshold",
            )
            return

    def _trigger_kill_switch(self, reason: KillSwitchReason, details: str) -> None:
        self._kill_state.triggered = True
        self._kill_state.reason = reason
        self._kill_state.triggered_at = datetime.now(timezone.utc)
        self._kill_state.details = details
        logger.critical("KILL SWITCH TRIGGERED: %s — %s", reason.value, details)

    def reset_kill_switch(self) -> None:
        self._kill_state = KillSwitchState(
            peak_equity=self._account_info["balance"],
        )
        logger.info("Kill switch reset")

    def get_kill_switch_status(self) -> dict[str, Any]:
        return {
            "triggered": self._kill_state.triggered,
            "reason": self._kill_state.reason.value if self._kill_state.reason else None,
            "triggered_at": self._kill_state.triggered_at.isoformat() if self._kill_state.triggered_at else None,
            "details": self._kill_state.details,
            "daily_pnl": round(self._kill_state.daily_pnl, 2),
            "peak_equity": round(self._kill_state.peak_equity, 2),
            "current_drawdown": round(self._kill_state.current_drawdown, 4),
            "consecutive_rejections": self._kill_state.consecutive_rejections,
            "consecutive_model_failures": self._kill_state.consecutive_model_failures,
        }

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    async def _rate_limit_check(self) -> None:
        if not self._rate_limiter.allow():
            wait = self._rate_limiter.wait_time()
            logger.warning("Rate limited, waiting %.3fs", wait)
            await asyncio.sleep(wait)

    # ------------------------------------------------------------------
    # Retry logic
    # ------------------------------------------------------------------

    async def _retry(self, coro_factory, operation: str = "operation") -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, self._retry_attempts + 1):
            try:
                return await coro_factory()
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "MT5 %s failed (attempt %d/%d): %s",
                    operation,
                    attempt,
                    self._retry_attempts,
                    exc,
                )
                if attempt < self._retry_attempts:
                    await asyncio.sleep(self._retry_delay * attempt)
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self, path: str = "", login: int = 0, password: str = "", server: str = "") -> bool:
        """Stub: simulates MT5 connection."""
        self._connected = True
        self._account_info["login"] = login
        self._account_info["server"] = server
        logger.info("MT5 connected (stub): login=%d server=%s", login, server)
        return True

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("MT5 disconnected (stub)")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def get_account_info(self) -> dict[str, Any]:
        await self._rate_limit_check()
        self._check_kill_switch()
        if self._kill_state.triggered:
            raise KillSwitchTriggered(self._kill_state.reason, self._kill_state.details)

        equity = self._account_info["balance"] + sum(
            p.get("unrealized_pnl", 0) for p in self._positions.values()
        )
        self._account_info["equity"] = equity
        self._account_info["free_margin"] = equity - self._account_info["margin"]

        if equity > self._kill_state.peak_equity:
            self._kill_state.peak_equity = equity

        return dict(self._account_info)

    # ------------------------------------------------------------------
    # Symbols
    # ------------------------------------------------------------------

    async def get_symbol_info(self, symbol: str) -> dict[str, Any] | None:
        await self._rate_limit_check()
        return self._symbols.get(symbol)

    async def get_symbols(self) -> list[str]:
        await self._rate_limit_check()
        return list(self._symbols.keys())

    # ------------------------------------------------------------------
    # Price
    # ------------------------------------------------------------------

    async def get_price(self, symbol: str) -> dict[str, Any]:
        await self._rate_limit_check()
        info = self._symbols.get(symbol)
        if info is None:
            return {"bid": 0.0, "ask": 0.0, "spread": 0, "time": datetime.now(timezone.utc).isoformat()}

        spread = info.get("spread", 15) * info["point"]
        base = 1.0
        if symbol == "EURUSD":
            base = 1.0850
        elif symbol == "GBPUSD":
            base = 1.2650
        elif symbol == "USDJPY":
            base = 149.50
        elif symbol == "GBPJPY":
            base = 189.20
        elif symbol == "XAUUSD":
            base = 2350.00

        bid = base
        ask = base + spread
        return {
            "bid": round(bid, info["digits"]),
            "ask": round(ask, info["digits"]),
            "spread": info.get("spread", 15),
            "time": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def send_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        volume: float,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        comment: str = "",
    ) -> dict[str, Any]:
        await self._rate_limit_check()
        self._check_kill_switch()
        if self._kill_state.triggered:
            raise KillSwitchTriggered(self._kill_state.reason, self._kill_state.details)

        info = self._symbols.get(symbol)
        if info is None:
            self._kill_state.consecutive_rejections += 1
            return {"error": f"Unknown symbol: {symbol}", "ticket": None}

        self._kill_state.consecutive_rejections = 0
        ticket = int(uuid4().int % 1_000_000_000)
        order = {
            "ticket": ticket,
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "volume": volume,
            "price": price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "comment": comment,
            "status": "filled",
            "time": datetime.now(timezone.utc).isoformat(),
        }
        self._orders[ticket] = order
        self._order_history.append(order)

        margin = volume * info.get("contract_size", 100000) * 0.01
        self._account_info["margin"] += margin
        self._account_info["free_margin"] = self._account_info["equity"] - self._account_info["margin"]

        logger.info("MT5 order sent: %s %s %s %.2f @ %s", side, symbol, order_type, volume, price)
        return order

    async def modify_order(
        self,
        ticket: int,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> dict[str, Any]:
        await self._rate_limit_check()
        order = self._orders.get(ticket)
        if order is None:
            return {"error": f"Order {ticket} not found"}

        if stop_loss is not None:
            order["stop_loss"] = stop_loss
        if take_profit is not None:
            order["take_profit"] = take_profit

        logger.info("MT5 order modified: ticket=%d", ticket)
        return order

    async def close_order(self, ticket: int) -> dict[str, Any]:
        await self._rate_limit_check()
        order = self._orders.pop(ticket, None)
        if order is None:
            return {"error": f"Order {ticket} not found"}

        order["status"] = "closed"
        order["close_time"] = datetime.now(timezone.utc).isoformat()
        self._order_history.append(order)

        info = self._symbols.get(order["symbol"])
        if info:
            margin = order["volume"] * info.get("contract_size", 100000) * 0.01
            self._account_info["margin"] = max(0, self._account_info["margin"] - margin)
            self._account_info["free_margin"] = self._account_info["equity"] - self._account_info["margin"]

        logger.info("MT5 order closed: ticket=%d", ticket)
        return order

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    async def get_positions(self) -> list[dict[str, Any]]:
        await self._rate_limit_check()
        return list(self._positions.values())

    async def get_position(self, ticket: int) -> dict[str, Any] | None:
        await self._rate_limit_check()
        return self._positions.get(ticket)

    # ------------------------------------------------------------------
    # Data feed health
    # ------------------------------------------------------------------

    def record_data_feed_tick(self) -> None:
        self._kill_state.last_data_feed_time = datetime.now(timezone.utc)

    def record_model_failure(self) -> None:
        self._kill_state.consecutive_model_failures += 1

    def record_model_success(self) -> None:
        self._kill_state.consecutive_model_failures = 0

    def record_daily_pnl(self, pnl: float) -> None:
        self._kill_state.daily_pnl += pnl

    def reset_daily_pnl(self) -> None:
        self._kill_state.daily_pnl = 0.0

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "account": dict(self._account_info),
            "kill_switch": self.get_kill_switch_status(),
            "open_positions": len(self._positions),
            "pending_orders": len([o for o in self._orders.values() if o.get("status") == "pending"]),
            "symbols_available": len(self._symbols),
        }

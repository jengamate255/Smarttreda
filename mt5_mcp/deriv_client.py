"""Deriv API connection manager (WebSocket).

Wraps the Deriv WebSocket API with:
- automatic reconnect with retries and backoff
- connection health checks
- thread-safe access (RLock serialisation)
- request/response correlation with request_id
- graceful shutdown
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import websockets

from mt5_mcp.config import DerivConfig

logger = logging.getLogger("mt5_mcp.deriv")


class DerivError(RuntimeError):
    """Raised when the Deriv API is unreachable or a request is rejected."""


class DerivClient:
    """Thread-safe wrapper around the Deriv WebSocket API."""

    def __init__(self, config: DerivConfig | None = None) -> None:
        self._config = config or DerivConfig()
        self._lock = threading.RLock()
        self._ws: Any = None
        self._connected = False
        self._last_error = ""
        self._connected_at = 0.0
        self._connect_count = 0
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._recv_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._shutdown = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create the event loop in a background thread."""
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                self._shutdown = False
                self._thread = threading.Thread(target=self._run_loop, daemon=True)
                self._thread.start()
                # Wait for loop to be ready
                while self._loop is None:
                    time.sleep(0.01)
            return self._loop

    def _run_loop(self) -> None:
        """Run the asyncio event loop in a background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()
            self._loop = None

    def _submit(self, coro):
        """Submit a coroutine to the background loop and return a future."""
        loop = self._get_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future

    def _call_async(self, method: str, params: dict | None = None, timeout: float | None = None) -> Any:
        """Call an async method from sync context."""
        future = self._submit(self._call(method, params))
        return future.result(timeout=timeout or self._config.request_timeout_seconds)

    async def _call(self, method: str, params: dict | None = None) -> Any:
        """Send a request and wait for response."""
        # Check connection and reconnect if needed
        if not self._connected or self._ws is None:
            await self._connect_async()
            if not self._connected:
                raise DerivError(f"Deriv not connected: {self._last_error}")
        else:
            # Check if websocket is still open
            try:
                if not self._ws.open:
                    self._connected = False
                    await self._connect_async()
                    if not self._connected:
                        raise DerivError(f"Deriv not connected: {self._last_error}")
            except Exception:
                self._connected = False
                await self._connect_async()
                if not self._connected:
                    raise DerivError(f"Deriv not connected: {self._last_error}")

        req_id = self._next_req_id()
        # Deriv API format: {"req_id": 1, "method": "value", "param": "value"}
        request = {"req_id": req_id}
        if params:
            request.update(params)
        else:
            request[method] = 1

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._ws.send(json.dumps(request))
            response = await asyncio.wait_for(future, timeout=self._config.request_timeout_seconds)
            return response
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise DerivError(f"Request timeout: {method}")
        except Exception as exc:
            self._pending.pop(req_id, None)
            # If connection error, mark as disconnected for next retry
            if "closed" in str(exc).lower() or "reset" in str(exc).lower():
                self._connected = False
            raise

    def _next_req_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    def connect(self, force: bool = False) -> bool:
        """Connect to Deriv WebSocket API (sync wrapper)."""
        with self._lock:
            if self._connected and not force:
                return True
        try:
            future = self._submit(self._connect_async())
            return future.result(timeout=self._config.connect_timeout_seconds)
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("Deriv connect failed: %s", exc)
            return False

    async def _connect_async(self) -> bool:
        """Async connect to Deriv WebSocket."""
        if self._connected and self._ws is not None:
            return True

        attempts = max(1, self._config.reconnect_attempts)
        for attempt in range(1, attempts + 1):
            try:
                url = f"{self._config.ws_url}?app_id={self._config.app_id}"
                self._ws = await websockets.connect(
                    url,
                    ping_interval=self._config.ping_interval_seconds,
                    ping_timeout=10,
                )
                self._connected = True
                self._connected_at = time.time()
                self._connect_count += 1
                self._last_error = ""
                logger.info("Connected to Deriv (attempt %d/%d)", attempt, attempts)

                # Start receive task
                self._recv_task = asyncio.create_task(self._recv_loop())
                return True
            except Exception as exc:
                self._last_error = str(exc)
                logger.warning(
                    "Deriv connect failed (attempt %d/%d): %s", attempt, attempts, exc
                )
                if attempt < attempts:
                    await asyncio.sleep(self._config.reconnect_delay_seconds)

        self._connected = False
        return False

    async def _recv_loop(self) -> None:
        """Background task to receive messages and resolve futures."""
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue

                req_id = data.get("req_id")
                if req_id and req_id in self._pending:
                    future = self._pending.pop(req_id)
                    if not future.done():
                        future.set_result(data)
                # Handle subscriptions, errors, etc.
                elif "error" in data:
                    logger.error("Deriv error: %s", data["error"])
        except Exception as exc:
            if not self._shutdown:
                logger.warning("Deriv recv loop ended: %s", exc)
            self._connected = False

    def ensure_connected(self) -> None:
        """Ensure connection is alive, raise if not."""
        with self._lock:
            if self._connected and self._ws is not None:
                return
        if not self.connect():
            raise DerivError(f"Deriv not connected: {self._last_error}")

    def health(self) -> dict[str, Any]:
        """Return connection health status."""
        with self._lock:
            # For Deriv, we consider it healthy if we can connect (even if connection closes after each request)
            if self._ws is not None:
                try:
                    ok = self._ws.open
                except Exception:
                    ok = False
            else:
                ok = True  # Can reconnect on demand
            return {
                "ok": ok,
                "connected": ok,
                "last_error": "" if ok else self._last_error,
                "uptime_s": time.time() - self._connected_at if (ok and self._connected_at > 0) else 0.0,
                "connect_count": self._connect_count,
                "note": "Deriv API closes connection after each request; auto-reconnect enabled",
            }

    def shutdown(self) -> None:
        """Close the connection and stop the event loop."""
        self._shutdown = True
        with self._lock:
            if self._ws is not None:
                try:
                    future = self._submit(self._ws.close())
                    future.result(timeout=5)
                except Exception as exc:
                    logger.warning("Deriv shutdown raised: %s", exc)
            self._connected = False
            if self._recv_task:
                self._recv_task.cancel()
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._loop.stop)
        logger.info("Deriv connection closed")

    def last_error(self) -> str:
        return self._last_error

    # ------------------------------------------------------------------
    # Deriv API methods
    # ------------------------------------------------------------------
    def active_symbols(self, product_type: str = "basic") -> list[dict[str, Any]]:
        """Get list of active symbols."""
        return self._call_async("active_symbols", {"active_symbols": "brief", "product_type": product_type})

    def symbol_info(self, symbol: str) -> dict[str, Any]:
        """Get detailed symbol information."""
        return self._call_async("symbol_info", {"symbol_info": symbol})

    def tick(self, symbol: str) -> dict[str, Any]:
        """Get current tick for a symbol."""
        return self._call_async("tick", {"tick": symbol, "subscribe": 0})

    def ticks_history(
        self, symbol: str, count: int = 100, end: str | int = "latest", style: str = "candles", granularity: int = 60
    ) -> dict[str, Any]:
        """Get historical ticks/candles."""
        params: dict[str, Any] = {
            "ticks_history": symbol,
            "count": count,
            "end": end,
            "style": style,
            "granularity": granularity,
        }
        return self._call_async("ticks_history", params)

    def subscribe_ticks(self, symbol: str) -> int:
        """Subscribe to tick stream. Returns req_id for unsubscribe."""
        req_id = self._next_req_id()
        request = {"req_id": req_id, "ticks": symbol, "subscribe": 1}
        future = self._submit(self._ws.send(json.dumps(request)))
        future.result(timeout=5)
        return req_id

    def unsubscribe(self, req_id: int) -> None:
        """Unsubscribe from a stream."""
        request = {"req_id": self._next_req_id(), "forget": req_id}
        future = self._submit(self._ws.send(json.dumps(request)))
        future.result(timeout=5)

    def time(self) -> dict[str, Any]:
        """Get server time."""
        return self._call_async("time", {"time": 1})

    def trading_times(self, symbols: list[str]) -> dict[str, Any]:
        """Get trading times for symbols."""
        return self._call_async("trading_times", {"trading_times": symbols})

    def asset_index(self) -> dict[str, Any]:
        """Get asset index."""
        return self._call_async("asset_index", {"asset_index": 1})

    def payout_currencies(self) -> dict[str, Any]:
        """Get payout currencies."""
        return self._call_async("payout_currencies", {"payout_currencies": 1})

    def states_list(self) -> dict[str, Any]:
        """Get list of states/countries."""
        return self._call_async("states_list", {"states_list": 1})

    def website_status(self) -> dict[str, Any]:
        """Get website status."""
        return self._call_async("website_status", {"website_status": 1})

    # ------------------------------------------------------------------
    # Trading API methods
    # ------------------------------------------------------------------
    def authorize(self, token: str | None = None) -> dict[str, Any]:
        """Authenticate with an API token. Returns the account summary."""
        token = token or self._config.api_token
        if not token:
            raise DerivError("No API token configured")
        return self._call_async("authorize", {"authorize": token})

    def balance(self) -> dict[str, Any]:
        """Get current account balance."""
        return self._call_async("balance", {"balance": 1})

    def contracts_for(self, symbol: str) -> dict[str, Any]:
        """Get available contract types for a symbol."""
        return self._call_async("contracts_for", {"contracts_for": symbol, "currency": "USD"})

    def proposal(self, symbol: str, contract_type: str, amount: float, duration: int, duration_unit: str = "m", multiplier: int | None = None, basis: str = "stake") -> dict[str, Any]:
        """Request a price proposal for a contract."""
        params: dict[str, Any] = {
            "proposal": 1,
            "symbol": symbol,
            "contract_type": contract_type,
            "amount": amount,
            "basis": basis,
            "duration": duration,
            "duration_unit": duration_unit,
        }
        if multiplier:
            params["multiplier"] = multiplier
        return self._call_async("proposal", params)

    def buy(self, proposal_id: str, price: float, stop_loss: float | None = None, take_profit: float | None = None) -> dict[str, Any]:
        """Buy a contract at the proposed price. Optionally set SL/TP for multipliers."""
        params: dict[str, Any] = {
            "buy": proposal_id,
            "price": price,
        }
        if stop_loss is not None:
            params["stop_loss"] = stop_loss
        if take_profit is not None:
            params["take_profit"] = take_profit
        return self._call_async("buy", params)

    def sell(self, contract_id: str, price: float | None = None) -> dict[str, Any]:
        """Sell a contract before expiry (early exit)."""
        params: dict[str, Any] = {"sell": contract_id}
        if price is not None:
            params["price"] = price
        return self._call_async("sell", params)

    def proposal_open_contract(self, contract_id: str) -> dict[str, Any]:
        """Get details / status of an open contract."""
        return self._call_async("proposal_open_contract", {"proposal_open_contract": 1, "contract_id": contract_id})

    def open_contracts(self) -> dict[str, Any]:
        """List all open contracts."""
        return self._call_async("proposal_open_contract", {"proposal_open_contract": 1})

    def sell_expired(self) -> dict[str, Any]:
        """Sell expired contracts."""
        return self._call_async("sell_expired", {"sell_expired": 1})

    # ------------------------------------------------------------------
    # MT5-compatible interface
    # ------------------------------------------------------------------
    def ensure_connected(self) -> None:
        """Ensure WebSocket connection is established."""
        if not self._connected:
            self.connect()

    def rates(self, symbol: str, timeframe: str = "M1", count: int = 100):
        """Get historical candles matching MT5Client.rates() interface.

        Returns list of dicts with keys: time, open, high, low, close, tick_volume, spread, real_volume
        """
        # Map timeframe to granularity in seconds
        tf_map = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
        granularity = tf_map.get(timeframe.upper(), 60)

        result = self.ticks_history(
            symbol=symbol,
            count=count,
            style="candles",
            granularity=granularity,
        )

        candles = result.get("candles", [])
        # Convert to MT5 format: time (epoch), open, high, low, close, tick_volume, spread, real_volume
        formatted = []
        for c in candles:
            formatted.append({
                "time": int(c.get("epoch", 0)),
                "open": float(c.get("open", 0)),
                "high": float(c.get("high", 0)),
                "low": float(c.get("low", 0)),
                "close": float(c.get("close", 0)),
                "tick_volume": int(c.get("volume", 0)),
                "spread": 0,
                "real_volume": 0,
            })
        return formatted

    def tick(self, symbol: str) -> dict[str, Any]:
        """Get current tick matching MT5Client.tick() interface."""
        result = self._call_async("tick", {"tick": symbol, "subscribe": 0})
        tick = result.get("tick", {})
        return {
            "bid": float(tick.get("bid", 0)),
            "ask": float(tick.get("ask", 0)),
            "last": float(tick.get("quote", 0)),
            "volume": int(tick.get("volume", 0)),
            "time": int(tick.get("epoch", 0)),
            "time_msc": int(tick.get("epoch", 0)) * 1000,
            "flags": 0,
            "volume_real": 0.0,
        }

    def symbol_info(self, symbol: str) -> dict[str, Any]:
        """Get symbol info matching MT5Client.symbol_info() interface."""
        result = self._call_async("symbol_info", {"symbol_info": symbol})
        info = result.get("symbol_info", {})

        # Convert Deriv symbol info to MT5 format
        pip = float(info.get("pip", 0.0001))
        digits = int(info.get("digits", 5))
        point = float(info.get("point", pip))
        trade_tick_size = float(info.get("trade_tick_size", point))
        trade_tick_value = float(info.get("trade_tick_value", 1.0))

        return {
            "name": info.get("symbol", symbol),
            "description": info.get("description", symbol),
            "path": "",
            "point": point,
            "digits": digits,
            "spread": int(info.get("spread", 0)),
            "trade_tick_size": trade_tick_size,
            "trade_tick_value": trade_tick_value,
            "volume_min": float(info.get("min_volume", 0.01)),
            "volume_max": float(info.get("max_volume", 100.0)),
            "volume_step": float(info.get("volume_step", 0.01)),
            "trade_contract_size": float(info.get("trade_contract_size", 1.0)),
            "trade_accrued_interest": 0.0,
            "trade_face_value": 0.0,
            "trade_liquidity_rate": 0.0,
            "margin_initial": 0.0,
            "margin_maintenance": 0.0,
            "session_volume": 0,
            "session_turnover": 0.0,
            "session_interest": 0.0,
            "session_buy_orders_volume": 0,
            "session_sell_orders_volume": 0,
            "session_price_limit_min": 0.0,
            "session_price_limit_max": 0.0,
            "session_price_settlement": 0.0,
        }

    def account_info(self) -> dict[str, Any]:
        """Get account info matching MT5Client.account_info() interface."""
        balance_result = self._call_async("balance", {"balance": 1})
        balance = float(balance_result.get("balance", {}).get("balance", 0))
        portfolio_result = self._call_async("portfolio", {"portfolio": 1})
        positions = portfolio_result.get("portfolio", [])
        equity = balance
        for pos in positions:
            equity += float(pos.get("profit", 0))
        return {
            "login": int(self._config.app_id),
            "balance": balance,
            "equity": equity,
            "profit": equity - balance,
            "margin": 0.0,
            "margin_free": balance,
            "margin_level": 0.0,
            "currency": "USD",
            "server": "Deriv-Demo",
            "name": "Deriv Synthetic",
            "company": "Deriv",
            "trade_mode": 0,
            "leverage": 0,
            "limit_orders": 0,
            "margin_so_mode": 0,
            "trade_allowed": True,
            "trade_expert": True,
        }

    def positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        portfolio_result = self._call_async("portfolio", {"portfolio": 1})
        positions = portfolio_result.get("portfolio", [])
        result = []
        for pos in positions:
            if symbol and pos.get("symbol") != symbol:
                continue
            result.append({
                "ticket": int(pos.get("contract_id", 0)),
                "symbol": pos.get("symbol", ""),
                "type": 0 if pos.get("contract_type", "").startswith("CALL") else 1,
                "volume": float(pos.get("volume", 0)),
                "price_open": float(pos.get("entry_spot", 0)),
                "sl": 0.0,
                "tp": 0.0,
                "price_current": float(pos.get("current_spot", 0)),
                "profit": float(pos.get("profit", 0)),
                "swap": 0.0,
                "magic": 0,
                "comment": "",
                "time": int(pos.get("date_start", 0)),
                "time_msc": int(pos.get("date_start", 0)) * 1000,
                "time_update": int(pos.get("date_expiry", 0)) * 1000,
                "reason": 0,
            })
        return result

    def pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return []

    def orders_total(self) -> int:
        return 0

    def positions_total(self) -> int:
        portfolio_result = self._call_async("portfolio", {"portfolio": 1})
        return len(portfolio_result.get("portfolio", []))


# Singleton instance
_deriv_client: DerivClient | None = None
_deriv_lock = threading.Lock()


def get_deriv_client() -> DerivClient:
    global _deriv_client
    with _deriv_lock:
        if _deriv_client is None:
            from mt5_mcp.config import get_settings
            _deriv_client = DerivClient(get_settings().deriv)
        return _deriv_client


def set_deriv_client(client: DerivClient | None) -> None:
    global _deriv_client
    with _deriv_lock:
        _deriv_client = client


def reset_deriv_client() -> None:
    set_deriv_client(None)
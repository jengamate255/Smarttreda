"""HTTP client for the MT5 Gateway with transparent local fallback.

GatewayClient mimics the MT5Client interface used across the codebase
(`tick`, `rates`, `positions`, `order_send`, ...). Calls are forwarded
to the gateway process over HTTP. If the gateway is down or a call
fails at the transport layer, the client falls back to a direct
in-process MT5Client so scripts and tools keep working standalone.

DataFrames: gateway returns {columns, rows}; this shim reassembles a
pandas DataFrame so `rates()` callers are unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import pandas as pd

logger = logging.getLogger("mt5_mcp.gateway_client")

_RATES_FETCHER = None  # set by lazy import to avoid cycles


def gateway_enabled() -> bool:
    return os.getenv("MT5_GATEWAY", "1").strip().lower() not in ("0", "false", "off")


def gateway_url() -> str:
    host = os.getenv("MT5_GATEWAY_HOST", "127.0.0.1")
    port = os.getenv("MT5_GATEWAY_PORT", "8060")
    return f"http://{host}:{port}"


def gateway_alive(timeout: float = 2.0) -> bool:
    if not gateway_enabled():
        return False
    try:
        req = urllib.request.Request(f"{gateway_url()}/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


class GatewayClient:
    """Drop-in MT5Client replacement backed by the gateway HTTP API."""

    def __init__(self, settings=None):
        self._fallback = None  # lazy direct MT5Client
        self._fb_lock = threading.Lock()
        self._last_alive_check = 0.0
        self._alive = False

    # -- transport ---------------------------------------------------------

    def _call_gateway(self, method: str, payload: dict[str, Any] | None = None,
                      timeout: float = 30.0) -> dict[str, Any] | None:
        body = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            f"{gateway_url()}/call/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        token = os.getenv("WEB_AUTH_TOKEN", "")
        if token:
            req.add_header("X-Auth-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
                if data.get("ok"):
                    return data.get("data")
                return None
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                detail = str(exc)
            logger.debug("gateway %s -> %s %s", method, exc.code, detail)
            if exc.code in (401, 403, 404):
                raise RuntimeError(f"gateway rejected {method}: {detail}") from exc
            return None  # transport/5xx -> fallback
        except Exception as exc:  # noqa: BLE001
            logger.debug("gateway %s transport error: %s", method, exc)
            return None

    def _fallback_client(self):
        with self._fb_lock:
            if self._fallback is None:
                from mt5_mcp.mt5_client import MT5Client
                from mt5_mcp.config import get_settings
                self._fallback = MT5Client(get_settings().mt5)
            return self._fallback

    def _probe_alive(self) -> bool:
        """Cheap liveness probe, cached for 5 seconds."""
        now = time.time()
        if now - self._last_alive_check > 5.0:
            self._alive = gateway_alive()
            self._last_alive_check = now
        return self._alive

    def _dispatch(self, method: str, payload: dict[str, Any] | None = None,
                  timeout: float = 30.0, use_fallback: bool = True) -> Any:
        """Call via gateway; on transport failure use the direct client."""
        if self._probe_alive():
            result = self._call_gateway(method, payload, timeout)
            if result is not None:
                return result
        if use_fallback:
            fb = self._fallback_client()
            fn = getattr(fb, method, None)
            if fn is None:
                raise RuntimeError(f"method {method} not available")
            if not payload:
                return fn()
            return fn(**payload)
        raise RuntimeError(f"gateway method {method} failed and fallback disabled")

    # -- MT5Client-compatible surface --------------------------------------

    def ensure_connected(self) -> None:
        if self._probe_alive() and gateway_alive():
            return
        self._fallback_client().ensure_connected()

    def is_connected(self) -> bool:
        if self._probe_alive():
            try:
                return bool(self._call_gateway("is_connected", timeout=5))
            except Exception:  # noqa: BLE001
                pass
        return self._fallback_client().is_connected()

    def health(self) -> dict[str, Any]:
        return self._dispatch("health")

    def account_info(self) -> dict[str, Any]:
        return self._dispatch("account_info")

    def terminal_info(self) -> dict[str, Any]:
        return self._dispatch("terminal_info")

    def symbols(self, group: str = "") -> list[str]:
        return self._dispatch("symbols", {"group": group})

    def symbol_info(self, symbol: str) -> dict[str, Any]:
        return self._dispatch("symbol_info", {"symbol": symbol})

    def symbol_exists(self, symbol: str) -> bool:
        return self._dispatch("symbol_exists", {"symbol": symbol})

    def tick(self, symbol: str) -> dict[str, Any]:
        return self._dispatch("tick", {"symbol": symbol})

    def rates(self, symbol: str = "EURUSD", timeframe: str = "H1",
              count: int = 500) -> pd.DataFrame | None:
        global _RATES_FETCHER
        if self._probe_alive():
            data = self._call_gateway(
                "rates", {"symbol": symbol, "timeframe": timeframe, "count": count},
                timeout=60.0,
            )
            if data is not None and isinstance(data, dict) and data.get("columns"):
                df = pd.DataFrame(data["rows"], columns=data["columns"])
                if "time" in df.columns:
                    # Gateway serializes datetime64 as ISO strings; parse back
                    # to naive datetime64 — identical to MT5Client.rates().
                    df["time"] = pd.to_datetime(df["time"], errors="coerce").dt.tz_localize(None)
                    df = df.dropna(subset=["time"]).reset_index(drop=True)
                return df
        # fallback: direct client rates (expects column names from MT5)
        return self._fallback_client().rates(symbol, timeframe, count)

    def positions(self, symbol: str = "") -> list[dict[str, Any]]:
        return self._dispatch("positions", {"symbol": symbol})

    def pending_orders(self, symbol: str = "") -> list[dict[str, Any]]:
        return self._dispatch("pending_orders", {"symbol": symbol})

    def closed_orders(self, days: int = 30, symbol: str = "") -> list[dict[str, Any]]:
        return self._dispatch("closed_orders", {"days": days, "symbol": symbol})

    def deals(self, days: int = 30, symbol: str = "") -> list[dict[str, Any]]:
        return self._dispatch("deals", {"days": days, "symbol": symbol})

    def account_deals_since(self, epoch_seconds: int) -> list[dict[str, Any]]:
        return self._dispatch("account_deals_since", {"epoch_seconds":int(epoch_seconds)})

    def deals_since(self, epoch_seconds: int, symbol: str = "") -> list[dict[str, Any]]:
        return self._dispatch("deals_since",
                              {"epoch_seconds": epoch_seconds, "symbol": symbol})

    def order_send(self, request: dict[str, Any]) -> dict[str, Any]:
        # Orders always attempt direct fallback too: never lose an order
        # to a transport hiccup.
        if self._probe_alive():
            result = self._call_gateway("order_send", {"request": request}, timeout=45)
            if result is not None:
                return result
        return self._fallback_client().order_send(request)

    def order_calc_margin(self, action: int, symbol: str, volume: float, price: float) -> float:
        return self._dispatch("order_calc_margin",
                               {"action": action, "symbol": symbol, "volume": volume, "price": price})

    def order_calc_profit(self, action: int, symbol: str, volume: float,
                          price_open: float, price_close: float) -> float:
        return self._dispatch("order_calc_profit",
                               {"action": action, "symbol": symbol, "volume": volume,
                                "price_open": price_open, "price_close": price_close})

    def market_open(self, symbol: str) -> tuple[bool, str]:
        res = self._dispatch("market_open", {"symbol": symbol})
        if isinstance(res, (list, tuple)) and len(res) == 2:
            return bool(res[0]), str(res[1])
        return False, "unexpected market_open response"

    def server_time(self):
        res = self._dispatch("server_time")
        if isinstance(res, str):
            return pd.Timestamp(res)
        return res

    def shutdown(self) -> None:
        if self._fallback is not None:
            try:
                self._fallback.shutdown()
            except Exception:  # noqa: BLE001
                pass

    def connect(self, force: bool = False) -> bool:
        if self._probe_alive():
            return True
        return self._fallback_client().connect(force=force)

    def reconnect(self) -> bool:
        if self._probe_alive():
            return True
        return self._fallback_client().reconnect()

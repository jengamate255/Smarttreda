"""Boom/Crash strategy API routes (extracted from web_dashboard.py).

Endpoints:
    GET /api/boom-crash/scan          - spike-drift opportunity scan
    GET /api/boom-crash/state/{sym}   - trend/RSI/spike market state
    GET /api/boom-crash/spike/{sym}   - single-symbol spike detection

Dashboard coupling (connection guard + scan cache) is injected via
:func:`configure` so this module stays import-clean.
"""

from __future__ import annotations

import datetime
import logging
import threading
from typing import Callable, Optional

from fastapi import APIRouter, Query

from mt5_mcp.common import VOLATILITY_SYMBOLS
from mt5_mcp.tools.boom_crash_strategy import (
    BOOM_CRASH_SYMBOLS,
    detect_spike,
    get_market_state,
    mt5_symbol,
    scan_boom_crash_opportunities,
)

logger = logging.getLogger("web_dashboard")

router = APIRouter()

# Injected dashboard dependencies -------------------------------------------------
_require_connected: Optional[Callable[[], None]] = None
_scan_lock: Optional[threading.Lock] = None  # dashboard RLock alias (kept for parity)


class _ScanCache:
    """Minimal TTL cache mirroring web_dashboard._TTLCache semantics."""

    def __init__(self, ttl_seconds: int = 30) -> None:
        self._ttl = ttl_seconds
        self._data: dict = {}

    def get(self, key):
        hit = self._data.get(key)
        if not hit:
            return None
        ts, value = hit
        if datetime.datetime.now().timestamp() > ts:
            del self._data[key]
            return None
        return value

    def set(self, key, value) -> None:
        self._data[key] = (datetime.datetime.now().timestamp() + self._ttl, value)


_scan_cache = _ScanCache(ttl_seconds=30)


def configure(require_connected: Callable[[], None], lock: threading.Lock) -> None:
    """Called once by web_dashboard at startup to inject shared guards."""
    global _require_connected, _scan_lock
    _require_connected = require_connected
    _scan_lock = lock


def _cache_key(*args, **kwargs) -> tuple:
    return (args, tuple(sorted(kwargs.items())))


@router.get("/api/boom-crash/scan")
def boom_crash_scan(min_confidence: float = Query(50.0, ge=0.0, le=100.0)):
    """Scan Boom/Crash and Volatility indices for spike-drift trading opportunities."""
    cache_key = _cache_key("boom_crash_scan", min_confidence=min_confidence)
    cached = _scan_cache.get(cache_key)
    if cached is not None:
        return cached

    if _require_connected:
        _require_connected()
    try:
        symbols = list(BOOM_CRASH_SYMBOLS) + list(VOLATILITY_SYMBOLS)
        signals = scan_boom_crash_opportunities(symbols, min_confidence)
        scanned_boom_crash = [s for s in symbols if s.startswith(("BOOM", "CRASH"))]
        scanned_volatility = [s for s in symbols if s.startswith("Volatility")]
        result = {
            "scanned_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "symbols_scanned": len(symbols),
            "symbols_scanned_boom_crash": len(scanned_boom_crash),
            "symbols_scanned_volatility": len(scanned_volatility),
            "signals": [
                {
                    "symbol": s.symbol,
                    "mt5_symbol": mt5_symbol(s.symbol),
                    "direction": s.direction,
                    "spike_high": s.spike_high,
                    "spike_low": s.spike_low,
                    "spike_time": s.spike_time.isoformat() if s.spike_time else None,
                    "drift_direction": s.drift_direction,
                    "entry_price": s.entry_price,
                    "sl_price": s.sl_price,
                    "tp_price": s.tp_price,
                    "confidence": s.confidence,
                    "spike_age_candles": s.spike_age_candles,
                    "spike_size": s.spike_size,
                    "atr": s.atr,
                    "retracement": s.retracement,
                    "current_price": s.current_price,
                    "reason": s.reason,
                }
                for s in signals
            ],
            "count": len(signals),
        }
        _scan_cache.set(cache_key, result)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("boom_crash_scan failed: %s", exc)
        return {"signals": [], "count": 0, "error": str(exc)}


@router.get("/api/boom-crash/state/{symbol}")
def boom_crash_state(symbol: str):
    """Get comprehensive market state for a Boom/Crash symbol (trend, RSI, spike detection)."""
    if _require_connected:
        _require_connected()
    try:
        return get_market_state(symbol.upper())
    except Exception as exc:  # noqa: BLE001
        logger.warning("boom_crash_state failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "error": str(exc)}


@router.get("/api/boom-crash/spike/{symbol}")
def boom_crash_spike(
    symbol: str,
    lookback_minutes: int = Query(60, ge=10, le=240),
    spike_threshold_atr: float = Query(2.5, ge=1.0, le=10.0),
    min_spike_ticks: int = Query(10, ge=5, le=50),
):
    """Detect spike pattern for a specific Boom/Crash symbol."""
    if _require_connected:
        _require_connected()
    try:
        signal = detect_spike(
            symbol.upper(),
            lookback_minutes=lookback_minutes,
            spike_threshold_atr=spike_threshold_atr,
            min_spike_ticks=min_spike_ticks,
        )
        if signal:
            return {
                "detected": True,
                "symbol": signal.symbol,
                "direction": signal.direction,
                "spike_high": signal.spike_high,
                "spike_low": signal.spike_low,
                "spike_time": signal.spike_time.isoformat() if signal.spike_time else None,
                "drift_direction": signal.drift_direction,
                "entry_price": signal.entry_price,
                "sl_price": signal.sl_price,
                "tp_price": signal.tp_price,
                "confidence": signal.confidence,
                "reason": signal.reason,
            }
        return {"detected": False, "symbol": symbol.upper()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("boom_crash_spike failed for %s: %s", symbol, exc)
        return {"detected": False, "symbol": symbol.upper(), "error": str(exc)}

"""Shared pydantic models, timeframes, pip helpers and tool logging."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field

logger = logging.getLogger("mt5_mcp")

Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]

# Standard Deriv Boom/Crash synthetic indices available on the connected MT5
# account. Flip indices are intentionally excluded because they do not follow
# the one-direction spike assumptions used by the Boom/Crash strategy.
BOOM_CRASH_SYMBOLS: tuple[str, ...] = (
    "BOOM1000",
    "BOOM900",
    "BOOM600",
    "BOOM500",
    "BOOM300",
    "BOOM200",
    "BOOM150",
    "BOOM100",
    "BOOM99",
    "BOOM50",
    "CRASH1000",
    "CRASH900",
    "CRASH600",
    "CRASH500",
    "CRASH300",
    "CRASH200",
    "CRASH150",
    "CRASH100",
    "CRASH99",
    "CRASH50",
)

# Volatility indices (Deriv) — oscillate both directions, faded via
# mean-reversion: spike-up → SELL, spike-down → BUY, chandelier trail exit.
# Symbols are lower-case "Volatility" prefix so the detector guard
# ``symbol.startswith("Volatility")`` admits them while rejecting
# arbitrary tickers.
VOLATILITY_SYMBOLS: tuple[str, ...] = (
    "Volatility 5 Index",
    "Volatility 10 Index",
    "Volatility 15 Index",
    "Volatility 25 Index",
    "Volatility 30 Index",
    "Volatility 50 Index",
    "Volatility 75 Index",
    "Volatility 90 Index",
    "Volatility 100 Index",
    "Volatility 150 Index",
    "Volatility 250 Index",
)

TIMEFRAME_MT5: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
    "W1": 10080,
    "MN1": 43200,
}

MT5_TIME_MINUTE = 1
MT5_TIME_HOUR = 3
MT5_TIME_DAY = 5
MT5_TIME_SPECIAL = 6

TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
TRADE_ACTION_REMOVE = 8

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5

ORDER_TIME_GTC = 0
ORDER_TIME_DAY = 1
MT5_TIME_GTC_ALIAS = 0

ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

RETCODE_DONE = 10009
RETCODE_PLACED = 10008

T = TypeVar("T", bound=Callable[..., Any])


class Candle(BaseModel):
    time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 0
    spread: int = 0
    real_volume: int = 0


class Zone(BaseModel):
    type: Literal["demand", "supply"]
    upper: float
    lower: float
    strength: float = 0.0
    touches: int = 0
    fresh: bool = False
    broken: bool = False
    retested: bool = False
    confidence: float = 0.0
    age_bars: int = 0
    formation_time: datetime | None = None
    last_touch_index: int | None = None
    base_size_atr: float = 0.0
    impulse_atr: float = 0.0


class PatternResult(BaseModel):
    pattern: str
    direction: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    index: int = 0
    price: float = 0.0
    detail: dict[str, Any] = Field(default_factory=dict)


class TrendResult(BaseModel):
    direction: Literal["bullish", "bearish", "sideways"]
    confidence: float = Field(ge=0.0, le=1.0)
    ema_fast: float | None = None
    ema_slow: float | None = None
    hh: int = 0
    hl: int = 0
    lh: int = 0
    ll: int = 0
    last_swing_high: float | None = None
    last_swing_low: float | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class TradePlan(BaseModel):
    action: Literal["BUY", "SELL", "NONE"]
    symbol: str
    timeframe: str = ""
    valid: bool = False
    confidence: float = 0.0
    trade_score: float = 0.0
    reason: str = ""
    entry: float | None = None
    sl: float | None = None
    tp: float | None = None
    sl_pips: float | None = None
    tp_pips: float | None = None
    risk_reward: float | None = None
    htf_trend: str = ""
    confirmation: str = ""
    confirmation_confidence: float = 0.0
    zone_strength: float = 0.0
    zone_type: str = ""
    zone_upper: float | None = None
    zone_lower: float | None = None
    lots: float | None = None
    risk_amount: float | None = None
    risk_percent: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    ml_score: float = 0.0
    ml_note: str = ""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PositionView(BaseModel):
    ticket: int
    symbol: str
    side: str
    volume: float
    open_price: float
    sl: float | None = None
    tp: float | None = None
    current_price: float = 0.0
    floating_profit: float = 0.0
    pips: float = 0.0
    risk_amount: float | None = None
    risk_percent: float | None = None
    rr: float | None = None
    duration_seconds: int = 0
    health_score: float = 0.0
    health_status: str = ""
    open_time: datetime | None = None
    comment: str = ""


class TradeResult(BaseModel):
    accepted: bool
    ticket: int | None = None
    retcode: int | None = None
    error: str = ""
    checks: list[str] = Field(default_factory=list)
    request: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class NewsEvent(BaseModel):
    time: datetime
    currency: str = ""
    impact: Literal["low", "medium", "high"] = "low"
    title: str = ""
    actual: str = ""
    forecast: str = ""
    previous: str = ""


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def pip_size(info: Any) -> float:
    """One pip in price units, normalized per quote convention.

    Digits >= 4 (FX): pip = point * 10^(digits - 4), i.e. 0.0001 for 4/5-digit
    pairs. Digits 3 (JPY): point * 10 = 0.01. Digits 2 (indices, gold):
    point * 100 = 1.00 price point, so "20 pip SL" = $20 on XAUUSD or 20 pts
    on NDX100 instead of 0.2.
    """
    point = _attr(info, "point") or 0.00001
    digits = _attr(info, "digits", 5)
    if digits >= 4:
        return point * 10 ** (digits - 4)
    if digits == 3:
        return point * 10
    if digits == 2:
        return point * 100
    return point * 10


def price_to_pips(distance: float, info: Any) -> float:
    pip = pip_size(info)
    return distance / pip if pip else 0.0


def pip_value_per_lot(info: Any) -> float:
    tick_size = _attr(info, "trade_tick_size") or _attr(info, "tick_size") or 0.0
    tick_value = _attr(info, "trade_tick_value") or _attr(info, "tick_value") or 0.0
    if not tick_size:
        return 0.0
    return tick_value / tick_size * pip_size(info)


def round_to_step(
    value: float, step: float, floor: float = 0.0, ceil: float | None = None
) -> float:
    if step <= 0:
        step = 1.0
    result = round(value / step) * step
    result = max(floor, result)
    if ceil is not None:
        result = min(ceil, result)
    return round(result, 10)


def truncate(text: str, limit: int = 2000) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def import_optional(name: str) -> Any:
    try:
        return __import__(name)
    except ImportError:
        return None


def get_doc_summary(fn: Callable[..., Any]) -> str:
    doc = inspect.getdoc(fn) or ""
    return doc.split("\n\n")[0].strip()

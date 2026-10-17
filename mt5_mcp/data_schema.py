"""Normalized market data schema for multi-asset compatibility."""

from __future__ import annotations

from .asset import AssetClass
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

# Timeframe constants
TIMEFRAME_M1 = "M1"
TIMEFRAME_M5 = "M5"
TIMEFRAME_M15 = "M15"
TIMEFRAME_M30 = "M30"
TIMEFRAME_H1 = "H1"
TIMEFRAME_H4 = "H4"
TIMEFRAME_D1 = "D1"
TIMEFRAME_W1 = "W1"

# All supported timeframes
ALL_TIMEFRAMES = [
    TIMEFRAME_M1,
    TIMEFRAME_M5,
    TIMEFRAME_M15,
    TIMEFRAME_M30,
    TIMEFRAME_H1,
    TIMEFRAME_H4,
    TIMEFRAME_D1,
    TIMEFRAME_W1,
]

# Phase 1 primary timeframes
PRIMARY_TF = [TIMEFRAME_W1, TIMEFRAME_D1, TIMEFRAME_H4]
CONFIRMATION_TF = [TIMEFRAME_H1]
ENTRY_TF = [TIMEFRAME_M15, TIMEFRAME_M5]

# Normalized candle fields
CANDLE_FIELDS = ("timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "spread")


@dataclass
class NormalizedCandle:
    """Normalized candle data with consistent schema across all asset classes."""

    timestamp: datetime  # UTC
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float  # tick_volume or real_volume - caller must distinguish
    spread: float  # spread in pips or points
    real_volume: float | None = None  # Optional: real volume if available

    # Derived/auxiliary fields
    asset_class: AssetClass = field(default=AssetClass.FOREX, repr=False)

    def __post_init__(self) -> None:
        """Validate candle data after initialization."""
        if self.high < max(self.open, self.close):
            raise ValueError(f"Invalid candle: high {self.high} below max(open, close)")
        if self.low > min(self.open, self.close):
            raise ValueError(f"Invalid candle: low {self.low} above min(open, close)")
        if self.volume < 0:
            raise ValueError(f"Invalid candle: volume {self.volume} cannot be negative")
        if self.spread < 0:
            raise ValueError(f"Invalid candle: spread {self.spread} cannot be negative")

    @property
    def is_bullish(self) -> bool:
        """Check if candle is bullish (close > open)."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """Check if candle is bearish (close < open)."""
        return self.close < self.open

    @property
    def body_size(self) -> float:
        """Absolute body size."""
        return abs(self.close - self.open)

    @property
    def upper_shadow(self) -> float:
        """Upper shadow length."""
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> float:
        """Lower shadow length."""
        return min(self.open, self.close) - self.low

    @property
    def total_range(self) -> float:
        """Total candle range (high - low)."""
        return self.high - self.low

    def contains(self, other: NormalizedCandle, tolerance: float = 0.0) -> bool:
        """Check if this candle contains another's body."""
        return (self.open - tolerance <= other.open <= self.close + tolerance and
                self.open - tolerance <= other.close <= self.close + tolerance)


@dataclass
class SymbolInfo:
    """Normalized symbol information."""
    symbol: str
    asset_class: AssetClass
    point: float
    digits: int
    trade_mode: int
    filling_mode: int
    volume_min: float
    volume_max: float
    volume_step: float
    tick_size: float
    tick_value: float
    contract_size: float
    trade_stops_level: float = 0.0
    margin_rate: float = 0.0  # Margin requirement as percentage


@dataclass
class DataQualityReport:
    """Report from DataQualityAgent validation."""
    symbol: str
    timeframe: str
    is_valid: bool = True
    issues: list[str] = field(default_factory=list)
    missing_candles: int = 0
    duplicate_candles: int = 0
    bad_timestamps: int = 0
    timezone_issues: int = 0
    invalid_ohlc: int = 0
    gaps: int = 0
    abnormal_spreads: int = 0
    missing_symbols: int = 0
    insufficient_history: bool = False


# Data quality status constants
class DataQualityStatus:
    """Status constants for data quality validation."""
    DATA_VALID = "DATA_VALID"
    DATA_INVALID = "DATA_INVALID"
    WARNING = "WARNING"


# Type alias for candle data streams
CandleData = list[NormalizedCandle]
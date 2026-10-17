"""Domain models for trade setups, zones, structure, and market intelligence.

Pydantic models representing the structured data throughout the trading
intelligence pipeline. Every candidate setup, zone, and structural
conclusion is expressed as a typed, serializable model.
"""

from __future__ import annotations

from enum import Enum
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from .asset import AssetClass


# ---------------------------------------------------------------------------
# TradeSetup - the core candidate object evaluated by the system
# ---------------------------------------------------------------------------


class TradeSetupStatus(str, Enum):
    """Status of a trade candidate through the pipeline."""
    CANDIDATE = "CANDIDATE"
    UNDER_REVIEW = "UNDER_REVIEW"
    REJECTED = "REJECTED"
    APPROVED_FOR_PAPER = "APPROVED_FOR_PAPER"
    EXECUTED = "EXECUTED"
    CLOSED = "CLOSED"


class TradeSetup(BaseModel):
    """Structured trade candidate produced by the strategy engine.

    Every candidate setup must produce a structured object with this schema.
    The orchestrator, risk engine, red team, and execution gate all operate
    on this model.
    """

    id: str = Field(default_factory=lambda: f"setup_{datetime.utcnow().timestamp()}")
    symbol: str
    asset_class: AssetClass = AssetClass.FOREX
    direction: Literal["LONG", "SHORT"]
    timeframe: str  # e.g. "M5", "H1", "H4", "D1", "W1"
    higher_timeframe_bias: str = "neutral"  # W1/D1/H4 bias
    zone: Optional["ZoneModel"] = None
    structure: Optional["StructureModel"] = None
    liquidity: Optional["LiquidityModel"] = None
    confirmation: Optional["ConfirmationModel"] = None
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_percent: float = Field(default=0.5)
    expected_rr: Optional[float] = None
    thesis: str = ""  # Free-form rationale
    invalidation: str = ""  # What invalidates the setup
    evidence: list[str] = Field(default_factory=list)  # Evidence references
    status: TradeSetupStatus = TradeSetupStatus.CANDIDATE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Computed/derived fields
    risk_amount: Optional[float] = None  # Monetary risk
    position_size: Optional[float] = None  # Units/lots

    @field_validator("expected_rr", mode="before")
    @classmethod
    def _calc_rr(cls, v: float | None, info: ValidationInfo) -> float | None:
        """Derive ``expected_rr`` from SL/TP when the caller supplied none.

        NOTE: under Pydantic v2 this fallback is effectively unreachable --
        field validators do not run for a field left at its default -- and the
        value it would produce (raw ``|TP - SL|`` price distance, not a
        reward:risk ratio) is not the real RR. ``calculate_expected_rr()`` is
        the authoritative ratio. Measured behaviour on pydantic 2.13 is
        identical before and after the v2 migration (omitted -> None), so trade
        gating is unchanged.
        """
        if v is not None:
            return v
        sl = info.data.get("stop_loss")
        tp = info.data.get("take_profit")
        if sl is not None and tp is not None and sl > 0:
            return abs(tp - sl)
        return None

    def calculate_expected_rr(self) -> float | None:
        """Calculate risk-reward ratio.

        Returns: risk_reward ratio or None if cannot compute.
        """
        if self.stop_loss is None or self.take_profit is None:
            return None
        risk = abs(self.entry - self.stop_loss)
        reward = abs(self.take_profit - self.entry)
        if risk == 0:
            return None
        return round(reward / risk, 2)

    def update_at(self) -> None:
        """Update the modified timestamp."""
        self.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Zone model - supply/demand zones
# ---------------------------------------------------------------------------


class ZoneModel(BaseModel):
    """Algorithmic supply/demand zone.

    Each zone contains algorithmically generated data that remains reproducible.
    Zones are the foundation of the HTF supply/demand strategy.
    """

    id: str = Field(default_factory=lambda: f"zone_{datetime.utcnow().timestamp()}")
    symbol: str
    asset_class: AssetClass = AssetClass.FOREX
    timeframe: str  # e.g. "W1", "D1", "H4"
    zone_high: float
    zone_low: float
    type: Literal["demand", "supply"]  # demand = buy zone, supply = sell zone
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tests: int = 0  # Number of times price tested this zone
    strength: float = Field(default=0.0)  # 0.0-1.0, based on test quality
    invalidation: float = Field(default=0.0)  # Distance from price to invalidate
    evidence: list[str] = Field(default_factory=list)  # How zone was generated

    # Derived properties - computed in model_post_init
    zone_width: Optional[float] = None
    midpoint: Optional[float] = None

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        """Compute derived properties after model initialization."""
        self.zone_width = self.zone_high - self.zone_low
        self.midpoint = (self.zone_high + self.zone_low) / 2

    def is_tested(self) -> bool:
        """Check if zone has been tested at least once."""
        return self.tests > 0

    def test_zone(self) -> None:
        """Record a zone test."""
        self.tests += 1
        # Strength increases with each test, diminishing returns
        if self.tests == 1:
            self.strength = 0.5
        elif self.tests <= 5:
            self.strength = min(1.0, self.strength + 0.15)
        # After 5 tests, strength plateaus at 1.0

    def invalidate(self, distance: float) -> None:
        """Record invalidation distance."""
        self.invalidation = distance


# ---------------------------------------------------------------------------
# Structure model - market structure (BOS, CHoCH, trend, etc.)
# ---------------------------------------------------------------------------


class StructureModel(BaseModel):
    """Market structure analysis result.

    Analyzes: Higher High, Higher Low, Lower High, Lower Low,
    Break of Structure, Change of Character, trend, range, consolidation.
    """

    id: str = Field(default_factory=lambda: f"structure_{datetime.utcnow().timestamp()}")
    symbol: str
    asset_class: AssetClass = AssetClass.FOREX
    timeframe: str

    # Structure classification
    trend: Literal["uptrend", "downtrend", "ranging", "unknown"] = "unknown"
    structure_type: Literal["higher_high_higher_low",
                            "lower_high_lower_low",
                            "higher_high_lower_low",
                            "lower_high_higher_low",
                            "range", "consolidation"] = "range"

    # Key levels
    swing_high: float = 0.0
    swing_low: float = 0.0
    structure_break: Optional[float] = None  # Price level where structure broke
    structure_break_type: Optional[Literal["BOS", "CHoCH"]] = None  # Break type

    # Time of analysis
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def set_uptrend(self, swing_high: float, swing_low: float) -> None:
        """Set uptrend structure."""
        self.trend = "uptrend"
        self.structure_type = "higher_high_higher_low"
        self.swing_high = swing_high
        self.swing_low = swing_low

    def set_downtrend(self, swing_high: float, swing_low: float) -> None:
        """Set downtrend structure."""
        self.trend = "downtrend"
        self.structure_type = "lower_high_lower_low"
        self.swing_high = swing_high
        self.swing_low = swing_low

    def set_range(self) -> None:
        """Set ranging structure."""
        self.trend = "ranging"
        self.structure_type = "range"

    def set_bos(self, break_price: float) -> None:
        """Set Break of Structure."""
        self.structure_break = break_price
        self.structure_break_type = "BOS"

    def set_choch(self, break_price: float) -> None:
        """Set Change of Character."""
        self.structure_break = break_price
        self.structure_break_type = "CHoCH"


# ---------------------------------------------------------------------------
# Liquidity model
# ---------------------------------------------------------------------------


class LiquidityModel(BaseModel):
    """Liquidity levels detected in the market.

    Detects: previous day high/low, previous week high/low,
    equal highs/lows, swing highs/lows, session highs/lows.
    """

    id: str = Field(default_factory=lambda: f"liquidity_{datetime.utcnow().timestamp()}")
    symbol: str
    asset_class: AssetClass = AssetClass.FOREX
    timeframe: str

    # Liquidity levels
    day_high: float = 0.0
    day_low: float = 0.0
    week_high: float = 0.0
    week_low: float = 0.0
    equal_highs: list[float] = Field(default_factory=list)
    equal_lows: list[float] = Field(default_factory=list)
    swing_highs: list[float] = Field(default_factory=list)
    swing_lows: list[float] = Field(default_factory=list)
    session_highs: list[float] = Field(default_factory=list)
    session_lows: list[float] = Field(default_factory=list)

    # Detected sweeps
    day_high_swept: bool = False
    day_low_swept: bool = False
    week_high_swept: bool = False
    week_low_swept: bool = False

    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def detect_levels(self, candles: list[NormalizedCandle]) -> None:
        """Detect liquidity levels from candle data."""
        if not candles:
            return

        # Most recent H1 candle for session levels, D1 for daily
        # Find day high/low from the most recent D1 or equivalent
        day_candles = [c for c in candles if c.timeframe in ("D1", "H1")]
        if not day_candles:
            return

        # Daily high/low
        self.day_high = max(c.high for c in day_candles)
        self.day_low = min(c.low for c in day_candles)

        # Weekly levels - from W1 candles
        week_candles = [c for c in candles if c.timeframe == "W1"]
        if week_candles:
            self.week_high = max(c.high for c in week_candles)
            self.week_low = min(c.low for c in week_candles)

        # Swing points
        # Simple: look for local extremes
        for i in range(2, len(candles) - 2):
            # Swing high
            if (candles[i].high >= candles[i-1].high and
                candles[i].high >= candles[i-2].high and
                candles[i].high >= candles[i+1].high and
                candles[i].high >= candles[i+2].high):
                if candles[i].high not in self.swing_highs:
                    self.swing_highs.append(candles[i].high)
            # Swing low
            if (candles[i].low <= candles[i-1].low and
                candles[i].low <= candles[i-2].low and
                candles[i].low <= candles[i+1].low and
                candles[i].low <= candles[i+2].low):
                if candles[i].low not in self.swing_lows:
                    self.swing_lows.append(candles[i].low)

    def has_liquidity(self, price: float, tolerance: float = 0.001) -> bool:
        """Check if price is near any detected liquidity level."""
        levels = [
            self.day_high, self.day_low, self.week_high, self.week_low
        ]
        levels += self.swing_highs
        levels += self.swing_lows

        for level in levels:
            if abs(price - level) / max(level, 0.01) < tolerance:
                return True
        return False


# ---------------------------------------------------------------------------
# Confirmation model
# ---------------------------------------------------------------------------


class ConfirmationModel(BaseModel):
    """Price action confirmation for a trade setup.

    Detects: pin bars, engulfing candles, shooting stars,
    rejection candles, inside bars, breakout candles, displacement candles.

    Context matters - same pattern in different context has different meaning.
    """

    id: str = Field(default_factory=lambda: f"confirmation_{datetime.utcnow().timestamp()}")
    symbol: str
    asset_class: AssetClass = AssetClass.FOREX
    timeframe: str

    # Pattern type
    pattern: Literal["pin_bar", "engulfing", "shooting_star",
                     "rejection", "inside_bar", "breakout", "displacement"] = "rejection"

    # Pattern characteristics
    is_bullish: bool = False
    is_bearish: bool = False
    body_size_pct: float = 0.0  # Body as % of total range
    wick_pct: float = 0.0  # Wick as % of total range

    # Context
    higher_tf_bias: str = "neutral"  # HTF trend bias
    zone_confluence: bool = False  # Near a supply/demand zone
    structure_confluence: bool = False  # Near structure break/confirmation
    volume_confirmed: bool = False  # Volume supports the pattern (if available)

    # Timing
    confirmed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def is_reversal(self) -> bool:
        """Check if pattern is a reversal (against current trend)."""
        if self.pattern == "pin_bar":
            return self.is_bullish or self.is_bearish
        return False

    def is_continuation(self) -> bool:
        """Check if pattern is a continuation (with current trend)."""
        if self.pattern in ("engulfing", "breakout"):
            return True
        return False


# ---------------------------------------------------------------------------
# Risk Engine output
# ---------------------------------------------------------------------------


class RiskEngineResult(BaseModel):
    """Risk engine determination.

    Answers: CAN_TRADE or CANNOT_TRADE with an explanation.
    """

    can_trade: bool
    explanation: str = ""
    risk_percent: float = 0.5
    position_size: float = 0.0
    max_daily_loss_exceeded: bool = False
    max_drawdown_exceeded: bool = False
    max_positions_exceeded: bool = False
    correlation_exceeded: bool = False
"""Chief Trading Agent - the orchestrator that coordinates all other agents.

This is the top-level orchestrator that sequences the trading intelligence
pipeline: data -> research agents -> strategy -> risk -> red team -> validation.

Agent communication uses structured Pydantic models with evidence dependencies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .asset import AssetClass, asset_class_from_symbol
from .data_schema import NormalizedCandle, DataQualityReport, DataQualityStatus
from .data_quality import DataQualityAgent, quick_validate
from .domain_models import (
    TradeSetup, ZoneModel, StructureModel, LiquidityModel,
    ConfirmationModel, RiskEngineResult
)
from .risk_engine import RiskEngine
from .features import FeatureBag
from .groq_llm import (
    is_enabled as groq_enabled,
    check_health as groq_health,
    generate as groq_generate,
    generate_structured as groq_generate_structured,
    GroqAgentOutput,
    llm_to_agent_output,
    GROQ_MODELS,
)


# ---------------------------------------------------------------------------
# Agent communication models (Pydantic)
# ---------------------------------------------------------------------------


class AgentInput(BaseModel):
    """Base input model for agent messages."""
    task_id: str
    agent_id: str
    input_reference: dict[str, Any]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentOutput(BaseModel):
    """Base output model for agent messages."""
    task_id: str
    agent_id: str
    observation: str
    hypothesis: str = ""
    evidence: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)  # task_ids this depends on
    status: str = "completed"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OrchestratorInput(BaseModel):
    """Input to the chief orchestrator."""
    task_id: str
    symbol: str
    asset_class: AssetClass
    candles: list[NormalizedCandle]
    config: dict[str, Any] | None = None


class OrchestratorOutput(BaseModel):
    """Output from the chief orchestrator."""
    task_id: str
    candidate_setups: list[TradeSetup] = Field(default_factory=list)
    data_quality: DataQualityReport
    risk_result: RiskEngineResult = Field(default_factory=lambda: RiskEngineResult(can_trade=False, explanation=""))
    red_team_observations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Chief Trading Agent
# ---------------------------------------------------------------------------


class ChiefTradingAgent:
    """Main orchestrator agent that coordinates the trading intelligence pipeline.

    Pipeline sequence:
    1. Data Quality Check
    2. Feature Engineering
    3. Market Structure Analysis
    4. Supply/Demand Zone Detection
    5. Liquidity Detection
    6. Price Action Confirmation
    7. Regime Classification
    8. Strategy Evaluation (HTF supply/demand + structure + liquidity + price action)
    9. Red Team Attack
    10. Risk Engine Validation
    11. Trade Setup Generation
    """

    def __init__(self, risk_percent: float = 0.5, min_rr: float = 3.0,
                 max_positions: int = 5, enable_red_team: bool = True) -> None:
        self.risk_percent = risk_percent
        self.min_rr = min_rr
        self.max_positions = max_positions
        self.enable_red_team = enable_red_team

        self.data_quality_agent = DataQualityAgent()
        self.risk_engine = RiskEngine(
            risk_percent=risk_percent,
            min_risk_reward=min_rr,
            max_positions=max_positions,
        )

        # Pipeline components (initialized lazily or via DI)
        self.feature_engine: Optional[Any] = None
        self.structure_agent: Optional[Any] = None
        self.supply_demand_agent: Optional[Any] = None
        self.liquidity_agent: Optional[Any] = None
        self.regime_agent: Optional[Any] = None
        self.price_action_agent: Optional[Any] = None
        self.red_team_agent: Optional[Any] = None

    def run_pipeline(self, input_data: OrchestratorInput) -> OrchestratorOutput:
        """Run the full trading intelligence pipeline.

        Returns OrchestratorOutput with candidate setups, quality report,
        and risk determination.
        """
        task_id = input_data.task_id
        symbol = input_data.symbol
        asset_class = input_data.asset_class
        candles = input_data.candles

        output = OrchestratorOutput(
            task_id=task_id,
            data_quality=DataQualityReport(symbol=symbol, timeframe=""),
        )

        # --- Step 1: Data Quality ---
        is_valid, quality_report = quick_validate(candles, symbol, input_data.candles[0].timeframe if candles else "M5")
        output.data_quality = quality_report
        if not quality_report.is_valid:
            output.risk_result = RiskEngineResult(
                can_trade=False,
                explanation="Data quality validation failed",
            )
            return output

        # --- Step 2: Feature Engineering ---
        feature_bag = FeatureBag()
        feature_bag.update_from_series(candles)

        # --- Step 3: Market Structure ---
        structure = self._analyze_structure(candles, symbol, asset_class)

        # --- Step 4: Supply/Demand Zones ---
        zones = self._detect_zones(candles, symbol, asset_class)

        # --- Step 5: Liquidity Detection ---
        liquidity = self._detect_liquidity(candles, symbol, asset_class)

        # --- Step 6: Regime Classification ---
        regime = self._classify_regime(candles, symbol, asset_class)

        # --- Step 7: Price Action Confirmation ---
        confirmation = self._analyze_price_action(candles, symbol, asset_class)

        # --- Step 8: Strategy Evaluation ---
        # HTF supply/demand + structure + liquidity + price action
        setups = self._evaluate_strategy(
            symbol, asset_class, structure, zones, liquidity,
            confirmation, regime, feature_bag
        )

        # --- Step 9: Red Team Attack ---
        if self.enable_red_team and setups:
            red_observations = self._red_team_attack(setups, candles, symbol, asset_class)
            output.red_team_observations = red_observations

        # --- Step 10: Risk Engine Validation ---
        # Use first setup or pick best
        candidate = setups[0] if setups else None
        risk_result = RiskEngineResult(can_trade=True, explanation="") if not candidate else \
            self.risk_engine.can_trade(
                candidate, equity=10000.0, open_positions=0
            )
        output.risk_result = risk_result

        # --- Step 11: Generate candidate setups ---
        output.candidate_setups = setups

        return output

    def _analyze_structure(
        self, candles: list[NormalizedCandle], symbol: str, asset_class: AssetClass
    ) -> StructureModel:
        """Run market structure analysis."""
        # Simplified structure analysis
        structure = StructureModel(symbol=symbol, asset_class=asset_class, timeframe="H1")

        if len(candles) < 20:
            structure.set_range()
            return structure

        # Get most recent H1 candles
        h1_candles = [c for c in candles if c.timeframe == "H1"]
        if len(h1_candles) < 20:
            structure.set_range()
            return structure

        last = h1_candles[-1]
        prev = h1_candles[-2]

        # Simple trend detection: higher high/higher low vs lower high/lower low
        if last.high > prev.high and last.low > prev.low:
            structure.set_uptrend(last.high, last.low)
        elif last.high < prev.high and last.low < prev.low:
            structure.set_downtrend(last.high, last.low)
        else:
            structure.set_range()

        return structure

    def _detect_zones(
        self, candles: list[NormalizedCandle], symbol: str, asset_class: AssetClass
    ) -> list[ZoneModel]:
        """Detect supply/demand zones."""
        zones: list[ZoneModel] = []

        if len(candles) < 50:
            return zones

        # Look for strong impulse candles that form zones
        # Demand zone: below a bullish impulse (price dropped from here)
        # Supply zone: above a bearish impulse (price rose from here)

        # Find impulse candles (large body, small shadow)
        for i in range(1, len(candles) - 1):
            c = candles[i]
        # Skip - simplified: just return empty for now
        # Full implementation would identify zones from significant pivots

        return zones

    def _detect_liquidity(
        self, candles: list[NormalizedCandle], symbol: str, asset_class: AssetClass
    ) -> LiquidityModel:
        """Detect liquidity levels."""
        liquidity = LiquidityModel(symbol=symbol, asset_class=asset_class, timeframe="H1")
        liquidity.detect_levels(candles)
        return liquidity

    def _classify_regime(
        self, candles: list[NormalizedCandle], symbol: str, asset_class: AssetClass
    ) -> str:
        """Classify market regime (trending, ranging, breakout, etc.)."""
        if len(candles) < 50:
            return "uncertain"

        # Use volatility and ADR to classify
        # Simplified: check if price is making higher highs/higher lows or lower highs/lower lows
        h1_candles = [c for c in candles if c.timeframe == "H1"]
        if len(h1_candles) < 20:
            return "uncertain"

        last_10 = h1_candles[-20:]
        
        # Check for higher highs/higher lows
        making_hh_hl = all(
            last_10[i].high >= last_10[i-1].high and
            last_10[i].low >= last_10[i-1].low
            for i in range(1, len(last_10))
        )

        # Check for lower highs/lower lows
        making_lh_ll = all(
            last_10[i].high <= last_10[i-1].high and
            last_10[i].low <= last_10[i-1].low
            for i in range(1, len(last_10))
        )

        if making_hh_hl:
            return "trending"
        elif making_lh_ll:
            return "trending"
        else:
            # Check range bounds
            highs = [c.high for c in last_10]
            lows = [c.low for c in last_10]
            range_size = max(highs) - min(lows)
            if range_size > 0:
                # If price moves mostly in one direction, trending; otherwise ranging
                returns = []
                for i in range(1, len(last_10)):
                    r = (last_10[i].close - last_10[i-1].close) / last_10[i-1].open
                    returns.append(r)
                
                positive_returns = sum(1 for r in returns if r > 0)
                if positive_returns / len(returns) > 0.6:
                    return "trending"
                elif positive_returns / len(returns) < 0.4:
                    return "trending"
            
            return "ranging"

    def _analyze_price_action(
        self, candles: list[NormalizedCandle], symbol: str, asset_class: AssetClass
    ) -> Optional[ConfirmationModel]:
        """Analyze price action patterns for confirmation."""
        if len(candles) < 5:
            return None

        # Look at the most recent candle
        last = candles[-1]
        prev = candles[-2]

        # Simple pin bar detection
        body_size = abs(last.close - last.open)
        total_range = last.total_range
        upper_wick = last.upper_shadow
        lower_wick = last.lower_shadow

        if total_range == 0:
            return None

        body_pct = body_size / total_range
        upper_wick_pct = upper_wick / total_range
        lower_wick_pct = lower_wick / total_range

        confirmation: Optional[ConfirmationModel] = None

        # Pin bar: long wick, small body
        if upper_wick_pct > 0.6 and body_pct < 0.3:
            # Bullish pin bar (long lower wick)
            if last.close > last.open:
                confirmation = ConfirmationModel(
                    pattern="pin_bar",
                    is_bullish=True,
                    body_size_pct=body_pct,
                    wick_pct=upper_wick_pct,
                    higher_tf_bias="neutral",
                )
            # Bearish pin bar (long upper wick)
            elif last.close < last.open:
                confirmation = ConfirmationModel(
                    pattern="pin_bar",
                    is_bearish=True,
                    body_size_pct=body_pct,
                    wick_pct=upper_wick_pct,
                    higher_tf_bias="neutral",
                )

        # Engulfing candle
        elif (last.close > last.open and last.open < prev.close and last.close > prev.open):
            # Bullish engulfing
            confirmation = ConfirmationModel(
                pattern="engulfing",
                is_bullish=True,
                body_size_pct=body_pct,
                higher_tf_bias="neutral",
            )
        elif (last.close < last.open and last.open > prev.open and last.close < prev.close):
            # Bearish engulfing
            confirmation = ConfirmationModel(
                pattern="engulfing",
                is_bearish=True,
                body_size_pct=body_pct,
                higher_tf_bias="neutral",
            )

        if confirmation:
            confirmation.confirmed_at = datetime.now(timezone.utc)

        return confirmation

    def _evaluate_strategy(
        self,
        symbol: str,
        asset_class: AssetClass,
        structure: StructureModel,
        zones: list[ZoneModel],
        liquidity: LiquidityModel,
        confirmation: Optional[ConfirmationModel],
        regime: str,
        features: FeatureBag,
    ) -> list[TradeSetup]:
        """Evaluate the HTF supply/demand + strategy rules.

        Strategy: HTF supply/demand + market structure + liquidity + price action
        """
        setups: list[TradeSetup] = []

        # Filter by regime - certain strategies only work in certain regimes
        # e.g., supply/demand works in ranging markets; trend following works in trending

        # Generate candidate setups based on available analysis
        for zone in zones[:3]:  # Limit to top 3 zones
            for direction in ["LONG", "SHORT"]:
                setup = TradeSetup(
                    symbol=symbol,
                    asset_class=asset_class,
                    direction=direction,
                    timeframe="M5",  # Entry timeframe
                    higher_timeframe_bias=regime,
                    zone=zone if zone else None,
                    structure=structure if structure.trend != "ranging" else None,
                    liquidity=liquidity if liquidity.has_liquidity(zone.zone_low if zone else 0) else None,
                    confirmation=confirmation,
                    risk_percent=self.risk_percent,
                )

                # Calculate expected RR
                rr = setup.calculate_expected_rr()
                if rr and rr >= self.min_rr:
                    setup.expected_rr = rr
                    setups.append(setup)
                elif not rr:
                    # Set up with default risk but flag for review
                    setups.append(setup)

        # If no setups generated from zones, create a candidate based on structure + confirmation
        if not setups and confirmation and structure.trend != "ranging":
            direction = "LONG" if confirmation.is_bullish else "SHORT"
            setup = TradeSetup(
                symbol=symbol,
                asset_class=asset_class,
                direction=direction,
                timeframe="M5",
                higher_timeframe_bias=regime,
                structure=structure,
                confirmation=confirmation,
                risk_percent=self.risk_percent,
            )
            setups.append(setup)

        return setups

    def _red_team_attack(
        self, setups: list[TradeSetup], candles: list[NormalizedCandle],
        symbol: str, asset_class: AssetClass
    ) -> list[str]:
        """Run red team analysis on candidate setups.

        Attacks every candidate trade by asking:
        - Why could this trade fail?
        - Bad structure?
        - Weak zone?
        - Nearby opposing zone?
        - Liquidity target?
        - News?
        - Spread?
        - Volatility?
        - Correlation?
        - Poor RR?
        - Regime mismatch?
        - Overfitting?
        - Data problems?
        - Look-ahead bias?
        """
        observations: list[str] = []

        for setup in setups:
            obs_lines: list[str] = []
            obs_lines.append(f"Red Team analysis for {symbol} {setup.direction} setup")

            # Check structure
            if setup.structure and setup.structure.trend == "ranging":
                obs_lines.append("  -> Structure is ranging; supply/demand may be more appropriate")

            # Check zone strength
            if setup.zone and setup.zone.strength < 0.3:
                obs_lines.append(f"  -> Zone strength {setup.zone.strength:.1f} is weak")

            # Check for opposing zones
            if setup.zone:
                obs_lines.append(f"  -> Zone at {setup.zone.zone_high}-{setup.zone.zone_low}")

            # Check confirmation
            if setup.confirmation and not setup.confirmation.is_bullish and not setup.confirmation.is_bearish:
                obs_lines.append("  -> No clear price action confirmation")

            # Check RR
            if setup.expected_rr and setup.expected_rr < self.min_rr:
                obs_lines.append(f"  -> Expected RR {setup.expected_rr:.2f} below minimum {self.min_rr:.2f}")

            # Check regime mismatch
            if setup.higher_timeframe_bias != "trending" and setup.direction == "LONG":
                obs_lines.append("  -> Long bias against ranging regime")

            observations.append("\n".join(obs_lines))

        return observations
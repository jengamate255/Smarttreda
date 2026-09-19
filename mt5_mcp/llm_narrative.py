"""LLM-powered narrative generation for the trading intelligence pipeline.

This module uses the Groq LLM to generate contextual narratives from
strategy agent outputs. All outputs are treated as hypotheses, not signals.

Key principle: LLM generates descriptions and analysis, but all conclusions
must be validated by market data and go through the full agent pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .domain_models import (
    TradeSetup, ZoneModel, StructureModel, LiquidityModel,
    ConfirmationModel, RiskEngineResult
)
from .features import FeatureBag
from .groq_llm import (
    is_enabled as groq_enabled,
    generate_structured,
    GroqResponse,
)

logger = logging.getLogger("mt5_mcp.llm_narrative")


# ---------------------------------------------------------------------------
# System prompts for different narrative types
# ---------------------------------------------------------------------------

TRADE_THESIS_PROMPT = """You are a market analyst generating a trade thesis.
Given the following market analysis data, provide a concise trade thesis.

IMPORTANT: Your output is informational only, NOT a trading signal.
All conclusions must be supported by the data provided.

Format your response as:
THESIS: [Your trade thesis in 1-2 sentences]
EVIDENCE: [List 2-3 key pieces of supporting evidence]
RISKS: [List 1-2 key risks to this thesis]
INVALIDATION: [What would invalidate this thesis]

Keep your response under 200 words."""

RED_TEAM_PROMPT = """You are a red team analyst tasked with finding flaws in a trade thesis.
Given the following trade setup and market data, identify potential problems.

Focus on:
- Structural weaknesses in the thesis
- Missing or conflicting evidence
- Risk factors not addressed
- Alternative scenarios that could play over
- Overfitting or confirmation bias

Format your response as:
CRITIQUE: [Your main critique in 1-2 sentences]
ISSUES: [List 2-3 specific issues found]
ALTERNATIVE: [Brief alternative scenario]
SEVERITY: [LOW/MEDIUM/HIGH]

Keep your response under 200 words."""

MARKET_NARRATIVE_PROMPT = """You are a market analyst providing context on current market conditions.
Given the following analysis data, provide a brief market narrative.

Include:
- Current market regime and trend
- Key levels and zones
- Recent price action patterns
- Overall market sentiment

Format your response as:
REGIME: [Current market regime]
KEY_LEVELS: [Key price levels]
PATTERN: [Recent patterns observed]
SENTIMENT: [Overall market sentiment]

Keep your response under 150 words."""


class LLMNarrativeGenerator:
    """Generates narratives from strategy agent outputs using Groq LLM.

    This class bridges the gap between algorithmic analysis and human-readable
    narratives. All outputs are treated as hypotheses, not trading signals.
    """

    def __init__(self, enabled: bool | None = None):
        """Initialize the narrative generator.

        Args:
            enabled: Override LLM enabled state. None uses global setting.
        """
        self._enabled = enabled if enabled is not None else groq_enabled()

    def generate_trade_thesis(
        self,
        symbol: str,
        direction: str,
        structure: StructureModel | None = None,
        zones: list[ZoneModel] | None = None,
        confirmation: ConfirmationModel | None = None,
        regime: str = "unknown",
        features: FeatureBag | None = None,
    ) -> dict[str, Any]:
        """Generate a trade thesis from strategy analysis.

        Args:
            symbol: Trading symbol
            direction: Trade direction (LONG/SHORT)
            structure: Market structure analysis
            zones: Detected supply/demand zones
            confirmation: Price action confirmation
            regime: Current market regime
            features: Computed features

        Returns:
            Dict with thesis, evidence, risks, invalidation
        """
        if not self._enabled:
            return self._fallback_thesis(symbol, direction, regime)

        # Build context from analysis
        context = self._build_thesis_context(
            symbol, direction, structure, zones, confirmation, regime, features
        )

        try:
            response = generate_structured(
                system_prompt=TRADE_THESIS_PROMPT,
                user_prompt=context,
                temperature=0.3,
                max_tokens=500,
            )
            return self._parse_thesis_response(response.content)
        except Exception as exc:
            logger.warning("LLM thesis generation failed: %s", exc)
            return self._fallback_thesis(symbol, direction, regime)

    def generate_red_team_analysis(
        self,
        setup: TradeSetup,
        market_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate red team analysis for a trade setup.

        Args:
            setup: The trade setup to analyze
            market_data: Additional market context

        Returns:
            Dict with critique, issues, alternative, severity
        """
        if not self._enabled:
            return self._fallback_red_team(setup)

        context = self._build_red_team_context(setup, market_data)

        try:
            response = generate_structured(
                system_prompt=RED_TEAM_PROMPT,
                user_prompt=context,
                temperature=0.4,
                max_tokens=500,
            )
            return self._parse_red_team_response(response.content)
        except Exception as exc:
            logger.warning("LLM red team analysis failed: %s", exc)
            return self._fallback_red_team(setup)

    def generate_market_narrative(
        self,
        symbol: str,
        structure: StructureModel | None = None,
        zones: list[ZoneModel] | None = None,
        regime: str = "unknown",
        features: FeatureBag | None = None,
    ) -> dict[str, Any]:
        """Generate a market narrative from analysis data.

        Args:
            symbol: Trading symbol
            structure: Market structure analysis
            zones: Detected zones
            regime: Current regime
            features: Computed features

        Returns:
            Dict with regime, key_levels, pattern, sentiment
        """
        if not self._enabled:
            return self._fallback_narrative(symbol, structure, regime)

        context = self._build_narrative_context(
            symbol, structure, zones, regime, features
        )

        try:
            response = generate_structured(
                system_prompt=MARKET_NARRATIVE_PROMPT,
                user_prompt=context,
                temperature=0.3,
                max_tokens=400,
            )
            return self._parse_narrative_response(response.content)
        except Exception as exc:
            logger.warning("LLM narrative generation failed: %s", exc)
            return self._fallback_narrative(symbol, structure, regime)

    # -----------------------------------------------------------------------
    # Context builders
    # -----------------------------------------------------------------------

    def _build_thesis_context(
        self,
        symbol: str,
        direction: str,
        structure: StructureModel | None,
        zones: list[ZoneModel] | None,
        confirmation: ConfirmationModel | None,
        regime: str,
        features: FeatureBag | None,
    ) -> str:
        """Build context string for thesis generation."""
        parts = [f"Symbol: {symbol}", f"Direction: {direction}", f"Regime: {regime}"]

        if structure:
            parts.append(f"Structure: {structure.trend} ({structure.structure_type})")
            if structure.structure_break:
                parts.append(f"Structure break: {structure.structure_break_type} at {structure.structure_break}")

        if zones:
            zone_info = []
            for z in zones[:3]:
                zone_info.append(f"{z.type} {z.zone_low:.5f}-{z.zone_high:.5f}")
            parts.append(f"Zones: {', '.join(zone_info)}")

        if confirmation:
            parts.append(f"Pattern: {confirmation.pattern} (bullish={confirmation.is_bullish}, bearish={confirmation.is_bearish})")

        if features:
            if features.returns:
                parts.append(f"Recent returns: {[f'{r:.4f}' for r in features.returns[-3:]]}")
            if features.atr is not None:
                parts.append(f"ATR: {features.atr:.5f}")

        return "\n".join(parts)

    def _build_red_team_context(
        self, setup: TradeSetup, market_data: dict[str, Any] | None
    ) -> str:
        """Build context string for red team analysis."""
        parts = [
            f"Symbol: {setup.symbol}",
            f"Direction: {setup.direction}",
            f"Timeframe: {setup.timeframe}",
            f"Entry: {setup.entry or 'not set'}",
            f"Stop Loss: {setup.stop_loss or 'not set'}",
            f"Take Profit: {setup.take_profit or 'not set'}",
            f"Expected RR: {setup.expected_rr or 'not calculated'}",
            f"Thesis: {setup.thesis or 'not provided'}",
        ]

        if setup.zone:
            parts.append(f"Zone: {setup.zone.type} {setup.zone.zone_low:.5f}-{setup.zone.zone_high:.5f}")

        if setup.structure:
            parts.append(f"Structure: {setup.structure.trend}")

        if setup.confirmation:
            parts.append(f"Confirmation: {setup.confirmation.pattern}")

        if market_data:
            for k, v in market_data.items():
                parts.append(f"{k}: {v}")

        return "\n".join(parts)

    def _build_narrative_context(
        self,
        symbol: str,
        structure: StructureModel | None,
        zones: list[ZoneModel] | None,
        regime: str,
        features: FeatureBag | None,
    ) -> str:
        """Build context string for narrative generation."""
        parts = [f"Symbol: {symbol}", f"Regime: {regime}"]

        if structure:
            parts.append(f"Trend: {structure.trend}")
            parts.append(f"Structure: {structure.structure_type}")
            if structure.swing_high > 0:
                parts.append(f"Swing high: {structure.swing_high:.5f}")
            if structure.swing_low > 0:
                parts.append(f"Swing low: {structure.swing_low:.5f}")

        if zones:
            for z in zones[:3]:
                parts.append(f"Zone: {z.type} at {z.zone_low:.5f}-{z.zone_high:.5f}")

        if features:
            if features.atr is not None:
                parts.append(f"ATR: {features.atr:.5f}")

        return "\n".join(parts)

    # -----------------------------------------------------------------------
    # Response parsers
    # -----------------------------------------------------------------------

    def _parse_thesis_response(self, content: str) -> dict[str, Any]:
        """Parse LLM thesis response into structured dict."""
        result = {
            "thesis": "",
            "evidence": [],
            "risks": [],
            "invalidation": "",
        }

        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("THESIS:"):
                result["thesis"] = line[7:].strip()
            elif line.startswith("EVIDENCE:"):
                result["evidence"] = [e.strip() for e in line[9:].split(",")]
            elif line.startswith("RISKS:"):
                result["risks"] = [r.strip() for r in line[6:].split(",")]
            elif line.startswith("INVALIDATION:"):
                result["invalidation"] = line[13:].strip()

        return result

    def _parse_red_team_response(self, content: str) -> dict[str, Any]:
        """Parse LLM red team response into structured dict."""
        result = {
            "critique": "",
            "issues": [],
            "alternative": "",
            "severity": "MEDIUM",
        }

        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("CRITIQUE:"):
                result["critique"] = line[9:].strip()
            elif line.startswith("ISSUES:"):
                result["issues"] = [i.strip() for i in line[7:].split(",")]
            elif line.startswith("ALTERNATIVE:"):
                result["alternative"] = line[12:].strip()
            elif line.startswith("SEVERITY:"):
                result["severity"] = line[9:].strip().upper()

        return result

    def _parse_narrative_response(self, content: str) -> dict[str, Any]:
        """Parse LLM narrative response into structured dict."""
        result = {
            "regime": "",
            "key_levels": "",
            "pattern": "",
            "sentiment": "",
        }

        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("REGIME:"):
                result["regime"] = line[7:].strip()
            elif line.startswith("KEY_LEVELS:"):
                result["key_levels"] = line[11:].strip()
            elif line.startswith("PATTERN:"):
                result["pattern"] = line[8:].strip()
            elif line.startswith("SENTIMENT:"):
                result["sentiment"] = line[10:].strip()

        return result

    # -----------------------------------------------------------------------
    # Fallback methods (when LLM is disabled or fails)
    # -----------------------------------------------------------------------

    def _fallback_thesis(
        self, symbol: str, direction: str, regime: str
    ) -> dict[str, Any]:
        """Generate fallback thesis without LLM."""
        return {
            "thesis": f"{direction} setup for {symbol} in {regime} regime",
            "evidence": [f"Regime: {regime}", f"Direction: {direction}"],
            "risks": ["LLM analysis unavailable"],
            "invalidation": "Regime change or structure break",
        }

    def _fallback_red_team(self, setup: TradeSetup) -> dict[str, Any]:
        """Generate fallback red team analysis without LLM."""
        issues = []
        if not setup.entry:
            issues.append("Entry price not defined")
        if not setup.stop_loss:
            issues.append("Stop loss not defined")
        if not setup.expected_rr:
            issues.append("Risk-reward not calculated")

        return {
            "critique": "LLM red team analysis unavailable",
            "issues": issues or ["Manual review required"],
            "alternative": "Wait for additional confirmation",
            "severity": "MEDIUM",
        }

    def _fallback_narrative(
        self, symbol: str, structure: StructureModel | None, regime: str
    ) -> dict[str, Any]:
        """Generate fallback narrative without LLM."""
        trend = structure.trend if structure else "unknown"
        return {
            "regime": regime,
            "key_levels": f"Swing high: {structure.swing_high:.5f}, Swing low: {structure.swing_low:.5f}" if structure else "N/A",
            "pattern": "LLM analysis unavailable",
            "sentiment": f"Market is {trend} in {regime} regime",
        }

"""Tests for LLM Narrative Generator."""

import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mt5_mcp.llm_narrative import LLMNarrativeGenerator
from mt5_mcp.domain_models import (
    TradeSetup, ZoneModel, StructureModel, ConfirmationModel
)
from mt5_mcp.features import FeatureBag


class TestLLMNarrativeGenerator:
    """Test LLM narrative generator."""

    def test_init_disabled(self):
        gen = LLMNarrativeGenerator(enabled=False)
        assert not gen._enabled

    def test_init_enabled(self):
        gen = LLMNarrativeGenerator(enabled=True)
        assert gen._enabled

    def test_fallback_thesis(self):
        gen = LLMNarrativeGenerator(enabled=False)
        result = gen.generate_trade_thesis("EURUSD", "LONG", regime="trending")
        assert "thesis" in result
        assert "EURUSD" in result["thesis"]
        assert "LONG" in result["thesis"]
        assert len(result["evidence"]) > 0

    def test_fallback_red_team(self):
        gen = LLMNarrativeGenerator(enabled=False)
        setup = TradeSetup(
            symbol="EURUSD",
            direction="LONG",
            timeframe="H1",
        )
        result = gen.generate_red_team_analysis(setup)
        assert "critique" in result
        assert "issues" in result
        assert len(result["issues"]) > 0

    def test_fallback_narrative(self):
        gen = LLMNarrativeGenerator(enabled=False)
        structure = StructureModel(
            symbol="EURUSD",
            timeframe="H1",
            trend="uptrend",
            swing_high=1.1050,
            swing_low=1.1000,
        )
        result = gen.generate_market_narrative("EURUSD", structure, regime="trending")
        assert "regime" in result
        assert result["regime"] == "trending"

    def test_parse_thesis_response(self):
        gen = LLMNarrativeGenerator(enabled=False)
        content = """THESIS: EURUSD looks bullish
EVIDENCE: Strong uptrend, price near demand zone
RISKS: Could break structure
INVALIDATION: Close below 1.1000"""
        result = gen._parse_thesis_response(content)
        assert result["thesis"] == "EURUSD looks bullish"
        assert "Strong uptrend" in result["evidence"][0]
        assert "Could break structure" in result["risks"][0]
        assert result["invalidation"] == "Close below 1.1000"

    def test_parse_red_team_response(self):
        gen = LLMNarrativeGenerator(enabled=False)
        content = """CRITIQUE: Setup lacks confirmation
ISSUES: No pin bar, weak zone
ALTERNATIVE: Wait for breakout
SEVERITY: HIGH"""
        result = gen._parse_red_team_response(content)
        assert result["critique"] == "Setup lacks confirmation"
        assert "No pin bar" in result["issues"][0]
        assert result["severity"] == "HIGH"

    def test_parse_narrative_response(self):
        gen = LLMNarrativeGenerator(enabled=False)
        content = """REGIME: Trending market
KEY_LEVELS: 1.1050, 1.1000
PATTERN: Bullish engulfing
SENTIMENT: Bullish"""
        result = gen._parse_narrative_response(content)
        assert result["regime"] == "Trending market"
        assert result["sentiment"] == "Bullish"


class TestLLMNarrativeWithLLM:
    """Test LLM narrative generator with mocked LLM."""

    @patch("mt5_mcp.llm_narrative.generate_structured")
    def test_generate_thesis_with_llm(self, mock_generate):
        mock_generate.return_value = MagicMock(
            content="THESIS: Bullish EURUSD\nEVIDENCE: Uptrend\nRISKS: None\nINVALIDATION: Break below"
        )
        gen = LLMNarrativeGenerator(enabled=True)
        result = gen.generate_trade_thesis("EURUSD", "LONG", regime="trending")
        assert result["thesis"] == "Bullish EURUSD"
        mock_generate.assert_called_once()

    @patch("mt5_mcp.llm_narrative.generate_structured")
    def test_generate_red_team_with_llm(self, mock_generate):
        mock_generate.return_value = MagicMock(
            content="CRITIQUE: Weak setup\nISSUES: No confirmation\nALTERNATIVE: Wait\nSEVERITY: MEDIUM"
        )
        gen = LLMNarrativeGenerator(enabled=True)
        setup = TradeSetup(symbol="EURUSD", direction="LONG", timeframe="H1")
        result = gen.generate_red_team_analysis(setup)
        assert result["critique"] == "Weak setup"
        mock_generate.assert_called_once()

    @patch("mt5_mcp.llm_narrative.generate_structured")
    def test_generate_narrative_with_llm(self, mock_generate):
        mock_generate.return_value = MagicMock(
            content="REGIME: Trending\nKEY_LEVELS: 1.1050\nPATTERN: Engulfing\nSENTIMENT: Bullish"
        )
        gen = LLMNarrativeGenerator(enabled=True)
        result = gen.generate_market_narrative("EURUSD", regime="trending")
        assert result["regime"] == "Trending"
        mock_generate.assert_called_once()

    @patch("mt5_mcp.llm_narrative.generate_structured")
    def test_llm_failure_fallback(self, mock_generate):
        mock_generate.side_effect = Exception("API error")
        gen = LLMNarrativeGenerator(enabled=True)
        result = gen.generate_trade_thesis("EURUSD", "LONG", regime="trending")
        # Should fallback gracefully
        assert "thesis" in result
        assert "LLM analysis unavailable" in result["risks"][0]

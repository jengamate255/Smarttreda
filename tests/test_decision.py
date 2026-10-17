"""Tests for the trade decision engine."""

from __future__ import annotations

import pandas as pd

from ml_engine.decision import Decision, decide
from ml_engine.models.scorer import ScorerResult
from ml_engine.setups.composer import Setup


def _make_setup(**overrides) -> Setup:
    defaults = {
        "setup_id": "TEST-EURUSD-demand-100",
        "symbol": "EURUSD",
        "time": pd.Timestamp("2025-09-01 12:00"),
        "index": 100,
        "direction": "long",
        "entry": 1.1000,
        "stop": 1.0950,
        "target": 1.1100,
        "risk_reward": 2.0,
        "timeframes": ["H1"],
        "structure_context": {"bias": 1.0, "htf_bias": 1.0},
        "zone_context": {"zone_id": "Z1", "strength": 70, "status": "fresh", "width_atr": 0.5,
                       "departure_strength_atr": 1.2, "volume_context": "normal"},
        "liquidity_context": {},
        "price_action_context": {"pattern": "bullish_engulfing", "direction": "bullish", "props": {}},
        "location_context": {"kind": "support", "price_high": 1.1010, "price_low": 1.0990,
                          "strength": 75, "retests": 1, "dist_atr": 0.3},
        "market_regime": "trending_up",
        "vol_regime": "normal_vol",
        "htf_bias": "bullish",
        "definition": "demand_retest_long",
    }
    defaults.update(overrides)
    return Setup(**defaults)


def _make_ml(**overrides) -> ScorerResult:
    defaults = {
        "p_1r": 0.65, "p_2r": 0.35, "p_3r": 0.15,
        "expected_r": 0.5,
        "base_rate_1r": 0.5, "base_rate_2r": 0.25, "base_rate_3r": 0.1,
    }
    defaults.update(overrides)
    return ScorerResult(**defaults)


def _make_cfg(**overrides):
    from ml_engine.config import EngineConfig
    cfg = EngineConfig()
    for k, v in overrides.items():
        parts = k.split(".")
        obj = cfg
        for p in parts[:-1]:
            obj = getattr(obj, p)
        setattr(obj, parts[-1], v)
    return cfg


class TestDecisionNoTrade:
    def test_no_trade_on_low_expected_r(self):
        setup = _make_setup()
        ml = _make_ml(expected_r=-0.5)
        cfg = _make_cfg()
        d = decide(setup, ml, cfg)
        assert d.kind == "NO_TRADE"
        assert any("expected_R" in r for r in d.reason)

    def test_no_trade_on_htf_conflict(self):
        setup = _make_setup(htf_bias="bearish", direction="long")
        ml = _make_ml()
        cfg = _make_cfg()
        d = decide(setup, ml, cfg)
        assert d.kind == "NO_TRADE"
        assert any("htf_bias" in r for r in d.reason)

    def test_no_trade_on_daily_loss_limit(self):
        setup = _make_setup()
        ml = _make_ml()
        cfg = _make_cfg()
        # daily_loss_used > 10x per-trade risk => blocked
        d = decide(setup, ml, cfg, daily_loss_used=100.0)
        assert d.kind == "NO_TRADE"
        assert any("daily loss" in r for r in d.reason)


class TestDecisionTrade:
    def test_trade_on_good_setup(self):
        setup = _make_setup()
        ml = _make_ml(expected_r=1.8)
        cfg = _make_cfg()
        d = decide(setup, ml, cfg, account_balance=10000)
        assert d.kind == "TRADE"
        assert d.direction == "long"
        assert d.position_size > 0
        assert d.risk_reward == 2.0
        assert d.p_1r == 0.65
        assert d.p_2r == 0.35
        assert d.p_3r == 0.15

    def test_trade_has_invalidation(self):
        setup = _make_setup()
        ml = _make_ml(expected_r=1.8)
        cfg = _make_cfg()
        d = decide(setup, ml, cfg)
        assert any("stop" in inv.lower() for inv in d.invalidation)

    def test_trade_has_all_context(self):
        setup = _make_setup()
        ml = _make_ml(expected_r=1.8)
        cfg = _make_cfg()
        d = decide(setup, ml, cfg)
        assert d.regime == "trending_up"
        assert d.htf_bias == "bullish"
        assert d.definition == "demand_retest_long"


class TestDecisionDict:
    def test_to_dict(self):
        setup = _make_setup()
        ml = _make_ml(expected_r=1.8)
        cfg = _make_cfg()
        d = decide(setup, ml, cfg)
        dic = d.to_dict()
        assert dic["kind"] == "TRADE"
        assert isinstance(dic["reason"], list)
        assert isinstance(dic["invalidation"], list)

    def test_no_trade_static(self):
        d = Decision.no_trade("EURUSD", ["test reason"])
        assert d.kind == "NO_TRADE"
        assert d.reason == ["test reason"]
        assert d.position_size == 0.0

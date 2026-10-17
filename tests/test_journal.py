"""Tests for the trade journal (SQLite-backed)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ml_engine.backtest.management import ManagementResult
from ml_engine.decision import Decision
from ml_engine.journal import TradeJournal


@pytest.fixture
def journal():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    j = TradeJournal(db_path)
    yield j
    j.close()
    db_path.unlink(missing_ok=True)


def _sample_decision(**overrides) -> Decision:
    defaults = {
        "kind": "TRADE", "symbol": "EURUSD", "direction": "long",
        "entry": 1.1000, "stop": 1.0950, "target": 1.1100, "risk_reward": 2.0,
        "position_size": 0.1, "risk_percent": 0.5,
        "regime": "trending_up", "vol_regime": "normal_vol",
        "htf_bias": "bullish", "structure_bias": "bullish",
        "location_context": {}, "zone_context": {},
        "liquidity_context": {}, "price_action_context": {},
        "definition": "demand_retest_long",
        "p_1r": 0.65, "p_2r": 0.35, "p_3r": 0.15, "expected_r": 0.5,
        "base_rate_1r": 0.5, "base_rate_2r": 0.25, "base_rate_3r": 0.1,
        "reason": ["test"], "invalidation": ["test inv"],
        "timestamp": "2025-09-01T12:00:00Z",
    }
    defaults.update(overrides)
    return Decision(**defaults)


class TestTradeJournal:
    def test_log_and_fetch(self, journal):
        d = _sample_decision()
        did = journal.log_decision(d)
        assert did > 0
        trades = journal.get_trades()
        assert len(trades) == 1
        assert trades[0]["symbol"] == "EURUSD"
        assert trades[0]["kind"] == "TRADE"

    def test_log_no_trade(self, journal):
        d = Decision.no_trade("EURUSD", ["low expected_r"])
        journal.log_decision(d)
        trades = journal.get_trades(kind="NO_TRADE")
        assert len(trades) == 1

    def test_filter_by_symbol(self, journal):
        journal.log_decision(_sample_decision(symbol="EURUSD"))
        journal.log_decision(_sample_decision(symbol="GBPUSD"))
        eurusd = journal.get_trades(symbol="EURUSD")
        assert len(eurusd) == 1
        assert eurusd[0]["symbol"] == "EURUSD"

    def test_log_management(self, journal):
        did = journal.log_decision(_sample_decision())
        mr = ManagementResult(
            setup_id="S1", rule="hold_to_target", r_multiple=2.0,
            exit_bar=110, exit_price=1.110, max_favorable_r=2.5,
            max_adverse_r=0.3, bars_held=10,
        )
        mid = journal.log_management(did, mr)
        assert mid > 0
        mgmt = journal.get_management_for_trade(did)
        assert len(mgmt) == 1
        assert mgmt[0]["rule"] == "hold_to_target"
        assert mgmt[0]["r_multiple"] == 2.0

    def test_summary_empty(self, journal):
        s = journal.summary()
        assert s["total_trades"] == 0

    def test_summary_with_trades(self, journal):
        journal.log_decision(_sample_decision(expected_r=1.5))
        journal.log_decision(_sample_decision(expected_r=-0.5))
        s = journal.summary()
        assert s["total_trades"] == 2
        assert s["win_rate"] == 0.5
        assert s["avg_expected_r"] == 0.5
        assert "EURUSD" in s["symbols"]

    def test_clear(self, journal):
        journal.log_decision(_sample_decision())
        assert len(journal.get_trades()) == 1
        journal.clear()
        assert len(journal.get_trades()) == 0

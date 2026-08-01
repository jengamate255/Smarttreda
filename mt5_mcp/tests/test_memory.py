"""Tests for the SQLite trade journal, learning queries and statistics."""

from __future__ import annotations

import datetime

import pytest

from memory.learning import best_pairs, best_setups, repeated_mistakes
from memory.statistics import dashboard, equity_curve, max_drawdown
from memory.trade_journal import Journal


@pytest.fixture
def journal(tmp_path, monkeypatch) -> Journal:
    journal = Journal(tmp_path / "test_journal.db")
    monkeypatch.setattr("memory.trade_journal._journal", journal)
    return journal


def _seed(journal: Journal) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    trades = [
        dict(
            ticket=1,
            symbol="EURUSD",
            side="BUY",
            setup="demand_retest",
            pnl=350.0,
            outcome="win",
            mistakes="entered_before_confirmation",
            lessons="wait_for_engulfing",
            confidence=75.0,
            session_hour=8,
            rr=3.0,
            close_time=(now - datetime.timedelta(days=10)).isoformat(),
        ),
        dict(
            ticket=2,
            symbol="EURUSD",
            side="SELL",
            setup="supply_retest",
            pnl=-120.0,
            outcome="loss",
            mistakes="entered_before_confirmation; oversized_lot",
            lessons="",
            confidence=80.0,
            session_hour=14,
            rr=3.0,
            close_time=(now - datetime.timedelta(days=8)).isoformat(),
        ),
        dict(
            ticket=3,
            symbol="GBPUSD",
            side="BUY",
            setup="demand_retest",
            pnl=210.0,
            outcome="win",
            mistakes="",
            lessons="",
            confidence=70.0,
            session_hour=8,
            rr=2.9,
            close_time=(now - datetime.timedelta(days=5)).isoformat(),
        ),
        dict(
            ticket=4,
            symbol="GBPUSD",
            side="BUY",
            setup="breakout",
            pnl=-80.0,
            outcome="loss",
            mistakes="entered_before_confirmation",
            lessons="check_htf_trend",
            confidence=65.0,
            session_hour=10,
            rr=2.5,
            close_time=(now - datetime.timedelta(days=2)).isoformat(),
        ),
        dict(
            ticket=5,
            symbol="XAUUSD",
            side="SELL",
            setup="supply_retest",
            pnl=500.0,
            outcome="win",
            mistakes="",
            lessons="",
            confidence=90.0,
            session_hour=14,
            rr=3.5,
            close_time=(now - datetime.timedelta(days=1)).isoformat(),
        ),
    ]
    for trade in trades:
        journal.record_trade(**trade)


def test_record_and_notes(journal):
    trade_id = journal.record_trade(
        ticket=99, symbol="EURUSD", side="BUY", pnl=100.0, outcome="win"
    )
    assert trade_id > 0
    assert journal.update_notes(99, mistakes="chased_entry", lessons="wait")
    trade = journal.get_trade(99)
    assert trade["mistakes"] == "chased_entry"
    assert trade["lessons"] == "wait"


def test_sync_from_mt5(fake_client, journal, tmp_path):
    fake_client.closed = [
        {
            "ticket": 501,
            "symbol": "EURUSD",
            "type": 0,
            "volume": 0.1,
            "profit": 150.0,
            "price_open": 1.1,
            "price_current": 1.105,
            "sl": 1.09,
            "tp": 1.12,
            "time_setup": 1700000000,
            "time_done": 1700003600,
        },
        {
            "ticket": 502,
            "symbol": "GBPUSD",
            "type": 1,
            "volume": 0.2,
            "profit": -50.0,
            "price_open": 1.3,
            "price_current": 1.295,
            "sl": 1.31,
            "tp": 1.28,
            "time_setup": 1700000000,
            "time_done": 1700003600,
        },
        {
            "ticket": 503,
            "position_id": 905,
            "symbol": "EURUSD",
            "type": 0,
            "volume_initial": 0.15,
            "profit": 0.0,
            "price_open": 1.45,
            "price_current": 1.455,
            "sl": 1.44,
            "tp": 1.47,
            "time_setup": 1700000000,
            "time_done": 0,
        },
        {
            "ticket": 504,
            "position_id": 905,
            "symbol": "EURUSD",
            "type": 1,
            "volume_initial": 0.15,
            "profit": 25.0,
            "price_open": 1.455,
            "price_current": 1.455,
            "sl": 0.0,
            "tp": 0.0,
            "time_setup": 1700001000,
            "time_done": 1700003600,
        },
    ]
    result = journal.sync_from_mt5(fake_client, days=30)
    assert result["added"] == 3
    trade = journal.get_trade(501)
    assert trade["pnl"] == 150.0
    assert trade["outcome"] == "win"
    assert trade["session_hour"] is not None
    assert journal.get_trade(502)["outcome"] == "loss"
    position = journal.get_trade(905)
    assert position["pnl"] == 25.0
    assert position["outcome"] == "win"
    assert position["side"] == "BUY"
    assert position["entry"] == 1.45
    assert position["lots"] == 0.15


def test_sync_from_mt5_deals(fake_client, journal, tmp_path):
    fake_client.deals_history = [
        {
            "ticket": 1001,
            "order": 6001,
            "time": 1700000000,
            "type": 0,
            "entry": 0,
            "position_id": 999,
            "volume": 0.3,
            "price": 1.2,
            "profit": 0.0,
            "swap": 0.0,
            "fee": 0.0,
            "symbol": "EURUSD",
        },
        {
            "ticket": 1002,
            "order": 6002,
            "time": 1700005000,
            "type": 1,
            "entry": 1,
            "position_id": 999,
            "volume": 0.3,
            "price": 1.25,
            "profit": 150.0,
            "swap": -5.0,
            "fee": 0.0,
            "symbol": "EURUSD",
        },
    ]
    result = journal.sync_from_mt5(fake_client, days=30)
    assert result["added"] == 1
    trade = journal.get_trade(999)
    assert trade["side"] == "BUY"
    assert trade["entry"] == 1.2
    assert trade["exit_price"] == 1.25
    assert trade["lots"] == 0.3
    assert trade["pnl"] == 145.0
    assert trade["outcome"] == "win"


def test_repeated_mistakes(journal):
    _seed(journal)
    mistakes = repeated_mistakes()
    names = [m["mistake"] for m in mistakes]
    assert "entered_before_confirmation" in names
    entry = next(m for m in mistakes if m["mistake"] == "entered_before_confirmation")
    assert entry["occurrences"] == 3
    assert entry["total_pnl"] == 150.0


def test_best_setups_and_pairs(journal):
    _seed(journal)
    setups = best_setups()
    assert setups[0]["setup"] == "demand_retest"
    assert setups[0]["net_pnl"] == 560.0
    assert setups[1]["setup"] == "supply_retest"
    assert setups[1]["net_pnl"] == 380.0
    pairs = best_pairs()
    assert pairs[0]["symbol"] == "XAUUSD"
    assert pairs[0]["win_rate"] == 100.0


def test_statistics_dashboard(journal):
    _seed(journal)
    report = dashboard()
    assert report["trades"] == 5
    assert report["win_rate"] == 60.0
    assert report["expectancy"] == pytest.approx(172.0)
    assert report["profit_factor"] > 1.0
    assert report["sharpe_ratio"] != 0.0
    assert len(report["equity_curve"]) == 6
    assert report["monthly_profits"]
    assert report["best_setups"]


def test_equity_curve_and_drawdown(journal):
    _seed(journal)
    curve = equity_curve(journal, start_balance=10000.0)
    assert curve[0]["equity"] == 10000.0
    assert curve[-1]["equity"] == 10860.0
    dd = max_drawdown(curve)
    assert dd["max_drawdown"] > 0.0
    assert dd["max_drawdown_percent"] > 0.0


def test_audit_log(journal):
    journal.audit_write("test_tool", "agent-1", {"a": 1}, "output", 12.5, None)
    journal.audit_write("broken_tool", "agent-1", {}, "", 1.0, "boom")
    entries = journal.recent_audit(10)
    assert len(entries) == 2
    assert entries[0]["tool"] == "broken_tool"
    assert entries[0]["error"] == "boom"

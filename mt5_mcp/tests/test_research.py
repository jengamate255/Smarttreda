"""Tests for the Research Agent."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from memory.trade_journal import Journal
from research import _markdown, _suggestions, report


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
            session_hour=8,
            rr=3.0,
            close_time=(now - datetime.timedelta(days=1)).isoformat(),
        ),
        dict(
            ticket=2,
            symbol="EURUSD",
            side="SELL",
            setup="supply_retest",
            pnl=-350.0,
            outcome="loss",
            session_hour=8,
            rr=3.0,
            close_time=(now - datetime.timedelta(days=2)).isoformat(),
            mistakes="entered_before_confirmation",
        ),
        dict(
            ticket=3,
            symbol="EURUSD",
            side="BUY",
            setup="breakout",
            pnl=-120.0,
            outcome="loss",
            session_hour=8,
            rr=3.0,
            close_time=(now - datetime.timedelta(days=3)).isoformat(),
            mistakes="entered_before_confirmation",
        ),
        dict(
            ticket=5,
            symbol="EURUSD",
            side="BUY",
            setup="breakout",
            pnl=-50.0,
            outcome="loss",
            session_hour=8,
            rr=3.0,
            close_time=(now - datetime.timedelta(days=4)).isoformat(),
        ),
        dict(
            ticket=6,
            symbol="EURUSD",
            side="SELL",
            setup="supply_retest",
            pnl=-60.0,
            outcome="loss",
            session_hour=8,
            rr=3.0,
            close_time=(now - datetime.timedelta(days=5)).isoformat(),
        ),
        dict(
            ticket=4,
            symbol="GBPUSD",
            side="BUY",
            setup="demand_retest",
            pnl=210.0,
            outcome="win",
            session_hour=14,
            rr=3.0,
            close_time=(now - datetime.timedelta(days=20)).isoformat(),
        ),
    ]
    for trade in trades:
        journal.record_trade(**trade)


def test_report_window_stats(journal):
    _seed(journal)
    result = report(days=7, save=False)
    assert result["trades"] == 5
    assert result["win_rate"] == pytest.approx(20.0)
    assert result["net_pnl"] == pytest.approx(-230.0)
    assert result["expectancy"] == pytest.approx(-46.0)
    assert result["profit_factor"] == pytest.approx(0.6)
    assert result["average_rr"] == pytest.approx(3.0)
    assert result["max_drawdown"]["max_drawdown_percent"] > 0.0
    assert result["suggestions"]


def test_report_suggestions_are_data_driven(journal):
    _seed(journal)
    result = report(days=7, save=False)
    suggestions = result["suggestions"]
    text = " ".join(s["suggestion"] for s in suggestions).lower()
    assert "risk per trade" in text
    assert any(s["support"] for s in suggestions)
    mistakes = [m["mistake"] for m in result["mistakes"]]
    assert "entered_before_confirmation" in mistakes


def test_report_excludes_old_trades(journal):
    _seed(journal)
    result = report(days=100, save=False)
    assert result["trades"] == 6
    best = [s["name"] for s in result["best_symbols"]]
    assert "GBPUSD" in best


def test_report_empty(journal):
    result = report(days=7, save=False)
    assert result["trades"] == 0
    assert result["suggestions"] == []
    assert "not enough data" in _markdown(result).lower()


def test_report_saves(journal, tmp_path, monkeypatch):
    _seed(journal)
    monkeypatch.setattr("research.REPORT_DIR", tmp_path)
    result = report(days=7, save=True)
    assert result["saved_to"]["json"]
    assert result["saved_to"]["markdown"]
    assert Path(result["saved_to"]["json"]).exists()
    assert result["markdown"]


def test_suggestions_no_data():
    stats = {"trades": 3, "win_rate": 50.0, "net_pnl": 100.0, "average_rr": 3.0,
             "profit_factor": 2.0, "max_drawdown": {"max_drawdown_percent": 1.0}}
    out = _suggestions([], stats, [], [], [], [])
    assert out == []

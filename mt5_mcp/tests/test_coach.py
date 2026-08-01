"""Tests for the AI Coach."""

from __future__ import annotations

import datetime

import pytest

from coach import coach_review, similarity
from memory.trade_journal import Journal


@pytest.fixture
def journal(tmp_path, monkeypatch) -> Journal:
    journal = Journal(tmp_path / "test_journal.db")
    monkeypatch.setattr("memory.trade_journal._journal", journal)
    return journal


def _seed(journal: Journal) -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    base = dict(
        side="BUY",
        setup="demand_retest",
        confidence=95.0,
        trade_score=80.0,
        zone_strength=90.0,
        rr=3.0,
        session_hour=8,
        entry=1.1,
        sl=1.095,
        tp=1.13,
    )
    journal.record_trade(
        ticket=1,
        symbol="EURUSD",
        **base,
        exit_price=1.12,
        pnl=200.0,
        outcome="win",
        reason="demand retest with engulfing confirmation",
        close_time=(now - datetime.timedelta(days=1)).isoformat(),
    )
    journal.record_trade(
        ticket=2,
        symbol="EURUSD",
        **base,
        exit_price=1.095,
        pnl=-150.0,
        outcome="loss",
        mistakes="entered_before_confirmation",
        lessons="wait_for_candle_close",
        close_time=(now - datetime.timedelta(days=2)).isoformat(),
    )
    journal.record_trade(
        ticket=3,
        symbol="GBPUSD",
        **base,
        exit_price=1.12,
        pnl=120.0,
        outcome="win",
        close_time=(now - datetime.timedelta(days=3)).isoformat(),
    )


def test_similarity():
    target = dict(symbol="EURUSD", side="BUY", setup="demand_retest", rr=3.0,
                  trade_score=80.0, zone_strength=90.0,
                  close_time="2026-01-01T08:00:00+00:00")
    identical = dict(target)
    assert similarity(target, identical) == 100.0
    different = dict(target, symbol="GBPUSD")
    assert similarity(target, different) == 75.0
    assert similarity(target, {}) < 40.0


def test_coach_review_by_ticket(journal):
    _seed(journal)
    review = coach_review(ticket=2)
    assert review["ticket"] == 2
    assert review["outcome"] == "loss"
    assert review["evidence_for"]
    assert review["evidence_against"]
    assert review["improvement"]
    assert any("wait_for_candle_close" in line for line in review["improvement"])
    assert review["similar_trades"]["count"] >= 1
    assert review["verdict"]


def test_coach_review_latest(journal):
    _seed(journal)
    review = coach_review()
    assert review["ticket"] == 1
    assert review["why_taken"] == "demand retest with engulfing confirmation"
    assert review["outcome"] == "win"


def test_coach_review_winning_trade(journal):
    _seed(journal)
    review = coach_review(ticket=1)
    assert review["evidence_for"]
    assert "confidence" in " ".join(review["evidence_for"]).lower()


def test_coach_review_unknown_ticket(journal):
    _seed(journal)
    review = coach_review(ticket=999)
    assert "error" in review


def test_coach_review_empty_journal(journal):
    review = coach_review()
    assert "error" in review

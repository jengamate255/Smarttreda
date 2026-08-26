from __future__ import annotations

from datetime import datetime, timezone

from mt5_mcp.memory import trade_guard
from mt5_mcp.memory.trade_journal import Journal


def _loss(journal: Journal, ticket: int, symbol: str, mistakes: str) -> None:
    journal.record_trade(
        ticket=ticket,
        symbol=symbol,
        side="BUY",
        setup=trade_guard.SETUP,
        entry=100.0,
        sl=98.0,
        tp=103.0,
        lots=1.0,
        close_time=datetime.now(timezone.utc).isoformat(),
        pnl=-20.0,
        outcome="loss",
        mistakes=mistakes,
        lessons="lesson",
    )


def test_one_stale_loss_tightens_age_without_global_block(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "journal.db")
    _loss(journal, 1, "Crash 900 Index", "stale_spike_age")
    monkeypatch.setattr(trade_guard, "get_journal", lambda: journal)

    result = trade_guard.evaluate("Crash 900 Index")

    assert result["allowed"] is True
    assert result["max_spike_age"] == 10
    assert result["evidence"]["stale_spike_losses"] == 1


def test_two_losses_pause_same_symbol(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "journal.db")
    _loss(journal, 1, "Crash 900 Index", "stale_spike_age")
    _loss(journal, 2, "Crash 900 Index", "entered_before_confirmation")
    monkeypatch.setattr(trade_guard, "get_journal", lambda: journal)

    result = trade_guard.evaluate("Crash 900 Index")

    assert result["allowed"] is False
    assert result["evidence"]["symbol_losses"] == 2

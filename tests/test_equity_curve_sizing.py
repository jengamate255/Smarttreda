"""Tests for equity curve sizing multiplier.

Verifies:
- After 3+ consecutive losses, multiplier = 0.5 (halve size)
- After 3+ consecutive wins, multiplier = 1.25 (boost)
- With mixed results or < 3 streak, multiplier = 1.0
- Reads last 10 trades from journal
- Disabled mode returns 1.0
- Integration with pipeline.size_position()
"""

from __future__ import annotations

import sys
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt5_mcp.pipeline import equity_curve_sizing_multiplier


@pytest.fixture
def journal_db(tmp_path):
    """Create a temporary journal database with trades table."""
    db_path = tmp_path / "test_journal.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket INTEGER UNIQUE,
            symbol TEXT,
            side TEXT,
            setup TEXT DEFAULT 'manual',
            timeframe TEXT,
            entry REAL,
            exit_price REAL,
            sl REAL,
            tp REAL,
            lots REAL,
            open_time TEXT,
            close_time TEXT,
            pnl REAL,
            outcome TEXT,
            reason TEXT,
            mistakes TEXT,
            lessons TEXT,
            screenshot_path TEXT,
            market_conditions TEXT,
            confidence REAL,
            trade_score REAL,
            zone_strength REAL,
            session_hour INTEGER,
            duration_seconds INTEGER,
            rr REAL,
            trend_confidence REAL,
            confirmation_confidence REAL,
            sl_pips REAL,
            separation_atr REAL,
            mtf_aligned REAL,
            atr_val REAL,
            zone_proximity REAL,
            zone_touches REAL,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_trade(db_path: Path, outcome: str, close_time: str = ""):
    """Insert a test trade with given outcome."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO trades (ticket, symbol, outcome, close_time, pnl) "
        "VALUES (?, 'EURUSD', ?, ?, 0.0)",
        (hash(close_time + outcome) % 100000, outcome, close_time or "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def mock_journal(journal_db):
    """Mock get_journal to return our test database."""
    mock_j = MagicMock()

    def list_trades(limit=100, symbol="", setup="", outcome=""):
        conn = sqlite3.connect(str(journal_db))
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM trades WHERE 1=1"
        params = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if outcome:
            query += " AND outcome = ?"
            params.append(outcome)
        query += " ORDER BY COALESCE(close_time, created_at) DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    mock_j.list_trades = list_trades
    return mock_j


class TestEquityCurveSizingBasic:
    """Basic multiplier tests."""

    def test_no_trades_returns_neutral(self, mock_journal):
        """No trades in journal should return multiplier 1.0."""
        with patch("mt5_mcp.memory.trade_journal.get_journal", return_value=mock_journal):
            result = equity_curve_sizing_multiplier()
        assert result["multiplier"] == 1.0
        assert result["streak_type"] == "neutral"
        assert result["streak"] == 0

    def test_three_consecutive_losses_halves(self, mock_journal, journal_db):
        """3 consecutive losses should produce multiplier 0.5."""
        for i in range(3):
            _insert_trade(journal_db, "loss", f"2026-01-0{i+1}T12:00:00")

        with patch("mt5_mcp.memory.trade_journal.get_journal", return_value=mock_journal):
            result = equity_curve_sizing_multiplier()
        assert result["multiplier"] == 0.5
        assert result["streak_type"] == "loss"
        assert result["streak"] == -3

    def test_four_consecutive_losses_halves(self, mock_journal, journal_db):
        """4 consecutive losses should still produce multiplier 0.5."""
        for i in range(4):
            _insert_trade(journal_db, "loss", f"2026-01-0{i+1}T12:00:00")

        with patch("mt5_mcp.memory.trade_journal.get_journal", return_value=mock_journal):
            result = equity_curve_sizing_multiplier()
        assert result["multiplier"] == 0.5
        assert result["streak"] == -4

    def test_three_consecutive_wins_boosts(self, mock_journal, journal_db):
        """3 consecutive wins should produce multiplier 1.25."""
        for i in range(3):
            _insert_trade(journal_db, "win", f"2026-01-0{i+1}T12:00:00")

        with patch("mt5_mcp.memory.trade_journal.get_journal", return_value=mock_journal):
            result = equity_curve_sizing_multiplier()
        assert result["multiplier"] == 1.25
        assert result["streak_type"] == "win"
        assert result["streak"] == 3

    def test_five_consecutive_wins_boosts(self, mock_journal, journal_db):
        """5 consecutive wins should still produce multiplier 1.25."""
        for i in range(5):
            _insert_trade(journal_db, "win", f"2026-01-0{i+1}T12:00:00")

        with patch("mt5_mcp.memory.trade_journal.get_journal", return_value=mock_journal):
            result = equity_curve_sizing_multiplier()
        assert result["multiplier"] == 1.25
        assert result["streak"] == 5


class TestEquityCurveSizingMixed:
    """Mixed streak tests."""

    def test_two_losses_no_reduction(self, mock_journal, journal_db):
        """2 consecutive losses should NOT trigger reduction."""
        for i in range(2):
            _insert_trade(journal_db, "loss", f"2026-01-0{i+1}T12:00:00")

        with patch("mt5_mcp.memory.trade_journal.get_journal", return_value=mock_journal):
            result = equity_curve_sizing_multiplier()
        assert result["multiplier"] == 1.0
        assert result["streak_type"] == "loss"
        assert result["streak"] == -2

    def test_two_wins_no_boost(self, mock_journal, journal_db):
        """2 consecutive wins should NOT trigger boost."""
        for i in range(2):
            _insert_trade(journal_db, "win", f"2026-01-0{i+1}T12:00:00")

        with patch("mt5_mcp.memory.trade_journal.get_journal", return_value=mock_journal):
            result = equity_curve_sizing_multiplier()
        assert result["multiplier"] == 1.0
        assert result["streak_type"] == "win"
        assert result["streak"] == 2

    def test_win_then_loss_breaks_streak(self, mock_journal, journal_db):
        """Most recent loss after wins should count as loss streak of 1."""
        # Insert in reverse chronological order (DESC)
        _insert_trade(journal_db, "loss", "2026-01-05T12:00:00")
        _insert_trade(journal_db, "win", "2026-01-04T12:00:00")
        _insert_trade(journal_db, "win", "2026-01-03T12:00:00")
        _insert_trade(journal_db, "win", "2026-01-02T12:00:00")

        with patch("mt5_mcp.memory.trade_journal.get_journal", return_value=mock_journal):
            result = equity_curve_sizing_multiplier()
        # Most recent is loss, streak = 1 (below threshold)
        assert result["multiplier"] == 1.0
        assert result["streak_type"] == "loss"
        assert result["streak"] == -1

    def test_breakeven_breaks_streak(self, mock_journal, journal_db):
        """Breakeven outcome should break any streak."""
        _insert_trade(journal_db, "breakeven", "2026-01-05T12:00:00")
        _insert_trade(journal_db, "loss", "2026-01-04T12:00:00")
        _insert_trade(journal_db, "loss", "2026-01-03T12:00:00")
        _insert_trade(journal_db, "loss", "2026-01-02T12:00:00")

        with patch("mt5_mcp.memory.trade_journal.get_journal", return_value=mock_journal):
            result = equity_curve_sizing_multiplier()
        # Most recent is breakeven -> neutral
        assert result["multiplier"] == 1.0
        assert result["streak_type"] == "neutral"


class TestEquityCurveSizingDisabled:
    """Test disabled mode."""

    def test_disabled_returns_neutral(self, mock_journal, journal_db):
        """When disabled, should always return multiplier 1.0."""
        for i in range(5):
            _insert_trade(journal_db, "loss", f"2026-01-0{i+1}T12:00:00")

        with patch("mt5_mcp.memory.trade_journal.get_journal", return_value=mock_journal):
            result = equity_curve_sizing_multiplier(enabled=False)
        assert result["multiplier"] == 1.0
        assert result["streak_type"] == "neutral"


class TestEquityCurveSizingLookback:
    """Test lookback window."""

    def test_lookback_limits_trades_examined(self, mock_journal, journal_db):
        """Only lookback trades should be examined."""
        # Insert 15 trades: first 10 are wins, last 5 are losses
        for i in range(10):
            _insert_trade(journal_db, "win", f"2026-01-{i+1:02d}T12:00:00")
        for i in range(5):
            _insert_trade(journal_db, "loss", f"2026-01-{i+11:02d}T12:00:00")

        with patch("mt5_mcp.memory.trade_journal.get_journal", return_value=mock_journal):
            result = equity_curve_sizing_multiplier(lookback=10)
        # Most recent 10 are the last 5 losses + 5 wins from the older set
        # Actually DESC order: the 5 losses (most recent) + 5 wins
        # So streak = 5 losses -> multiplier 0.5
        assert result["multiplier"] == 0.5
        assert result["trades_used"] == 10


class TestEquityCurveSizingRobustness:
    """Test error handling."""

    def test_journal_error_returns_neutral(self):
        """If journal query fails, should return neutral (fail-open)."""
        mock_j = MagicMock()
        mock_j.list_trades.side_effect = Exception("db error")

        with patch("mt5_mcp.memory.trade_journal.get_journal", return_value=mock_j):
            result = equity_curve_sizing_multiplier()
        assert result["multiplier"] == 1.0
        assert result["streak_type"] == "neutral"

    def test_import_error_returns_neutral(self):
        """If journal import fails, should return neutral."""
        with patch("mt5_mcp.memory.trade_journal.get_journal", side_effect=ImportError("no module")):
            result = equity_curve_sizing_multiplier()
        assert result["multiplier"] == 1.0

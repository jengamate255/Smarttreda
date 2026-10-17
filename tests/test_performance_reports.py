"""Tests for weekly_report, divergence_monitor, and strategy performance API."""

from __future__ import annotations

import datetime
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from mt5_mcp.memory.trade_journal import Journal


@pytest.fixture
def journal_db():
    """Create a temporary journal database."""
    tmpdir = tempfile.mkdtemp(suffix="_journal_test")
    db_path = Path(tmpdir) / "test_journal.db"
    j = Journal(db_path)
    yield j
    j.close()
    import shutil
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass


def _add_trade(journal, **overrides):
    """Insert a trade into the journal with sensible defaults."""
    defaults = {
        "ticket": int(overrides.get("ticket", 100000)),
        "symbol": overrides.get("symbol", "EURUSD"),
        "side": overrides.get("side", "BUY"),
        "setup": overrides.get("setup", "trend_zone"),
        "entry": overrides.get("entry", 1.1000),
        "exit_price": overrides.get("exit_price", 1.1050),
        "sl": overrides.get("sl", 1.0950),
        "tp": overrides.get("tp", 1.1100),
        "lots": overrides.get("lots", 0.1),
        "open_time": overrides.get("open_time", "2026-10-01T10:00:00+00:00"),
        "close_time": overrides.get("close_time", datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()),
        "pnl": overrides.get("pnl", 50.0),
        "outcome": overrides.get("outcome", "win"),
        "rr": overrides.get("rr", 1.5),
    }
    return journal.record_trade(**defaults)


# ========== Weekly Report Tests ==========


class TestWeeklyReport:
    def test_calc_stats_empty(self):
        from mt5_mcp.weekly_report import _calc_stats
        stats = _calc_stats([])
        assert stats["total_trades"] == 0
        assert stats["total_pnl"] == 0.0
        assert stats["win_rate"] == 0.0

    def test_calc_stats_basic(self):
        from mt5_mcp.weekly_report import _calc_stats
        trades = [
            {"pnl": 100.0, "rr": 2.0, "setup": "trend_zone"},
            {"pnl": -50.0, "rr": 1.0, "setup": "trend_zone"},
            {"pnl": 75.0, "rr": 1.5, "setup": "mean_reversion"},
        ]
        stats = _calc_stats(trades)
        assert stats["total_trades"] == 3
        assert stats["wins"] == 2
        assert stats["losses"] == 1
        assert stats["total_pnl"] == 125.0
        assert stats["win_rate"] == pytest.approx(66.7, abs=0.1)
        assert stats["avg_win"] == 87.5
        assert stats["avg_loss"] == 50.0
        assert stats["largest_win"] == 100.0
        assert stats["largest_loss"] == -50.0
        assert stats["profit_factor"] == pytest.approx(3.5, abs=0.01)

    def test_calc_stats_per_strategy(self):
        from mt5_mcp.weekly_report import _calc_stats
        trades = [
            {"pnl": 100.0, "setup": "trend_zone", "rr": 2.0},
            {"pnl": -30.0, "setup": "trend_zone", "rr": 1.0},
            {"pnl": 50.0, "setup": "mean_reversion", "rr": 1.5},
        ]
        stats = _calc_stats(trades)
        assert "trend_zone" in stats["by_strategy"]
        assert "mean_reversion" in stats["by_strategy"]
        tz = stats["by_strategy"]["trend_zone"]
        assert tz["trades"] == 2
        assert tz["wins"] == 1
        assert tz["pnl"] == 70.0
        mr = stats["by_strategy"]["mean_reversion"]
        assert mr["trades"] == 1
        assert mr["pnl"] == 50.0

    def test_format_html_empty(self):
        from mt5_mcp.weekly_report import _format_html
        stats = {
            "total_trades": 0, "total_pnl": 0.0, "wins": 0, "losses": 0,
            "breakeven": 0, "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "largest_win": 0.0, "largest_loss": 0.0, "profit_factor": 0.0,
            "avg_rr": 0.0, "by_strategy": {},
        }
        html = _format_html(stats)
        assert "No closed trades" in html

    def test_format_html_with_trades(self):
        from mt5_mcp.weekly_report import _format_html
        stats = {
            "total_trades": 5, "total_pnl": 250.0, "wins": 4, "losses": 1,
            "breakeven": 0, "win_rate": 80.0, "avg_win": 100.0, "avg_loss": 50.0,
            "largest_win": 200.0, "largest_loss": -50.0, "profit_factor": 4.0,
            "avg_rr": 1.8, "by_strategy": {
                "trend_zone": {"trades": 3, "wins": 2, "losses": 1,
                               "pnl": 150.0, "win_rate": 66.7, "pf": 2.0,
                               "gross_win": 200.0, "gross_loss": 50.0},
            },
        }
        html = _format_html(stats)
        assert "Weekly Performance Report" in html
        assert "$+250.00" in html
        assert "80.0%" in html
        assert "trend_zone" in html

    def test_generate_report_with_journal(self, journal_db):
        from mt5_mcp.weekly_report import generate_weekly_report
        # Add some trades
        _add_trade(journal_db, ticket=1001, pnl=80.0, setup="trend_zone")
        _add_trade(journal_db, ticket=1002, pnl=-40.0, setup="trend_zone")
        _add_trade(journal_db, ticket=1003, pnl=60.0, setup="mean_reversion")

        with patch("mt5_mcp.weekly_report.get_journal", return_value=journal_db):
            report = generate_weekly_report(days=7)

        assert report["trade_count"] == 3
        assert report["stats"]["total_trades"] == 3
        assert report["stats"]["wins"] == 2
        assert report["stats"]["losses"] == 1
        assert "trend_zone" in report["stats"]["by_strategy"]

    def test_send_report_calls_notify(self, journal_db):
        from mt5_mcp.weekly_report import send_weekly_report
        _add_trade(journal_db, ticket=2001, pnl=100.0)

        with patch("mt5_mcp.weekly_report.get_journal", return_value=journal_db):
            with patch("mt5_mcp.weekly_report.notify", return_value=True) as mock_notify:
                result = send_weekly_report()
                assert result is True
                mock_notify.assert_called_once()
                call_args = mock_notify.call_args
                assert call_args[0][0] == "weekly_report"
                assert call_args[1]["force"] is True
                assert "<b>" in call_args[1]["html"]


# ========== Divergence Monitor Tests ==========


class TestDivergenceMonitor:
    def test_calc_live_metrics_empty(self):
        from mt5_mcp.divergence_monitor import _calc_live_metrics
        m = _calc_live_metrics([])
        assert m["n"] == 0
        assert m["win_rate"] == 0.0

    def test_calc_live_metrics_basic(self):
        from mt5_mcp.divergence_monitor import _calc_live_metrics
        trades = [
            {"pnl": 100.0}, {"pnl": -50.0}, {"pnl": 80.0}, {"pnl": -30.0},
        ]
        m = _calc_live_metrics(trades)
        assert m["n"] == 4
        assert m["wins"] == 2
        assert m["losses"] == 2
        assert m["win_rate"] == 50.0
        assert m["pf"] == pytest.approx(180 / 80, abs=0.01)

    def test_check_divergence_insufficient_data(self, journal_db):
        from mt5_mcp.divergence_monitor import check_divergence
        _add_trade(journal_db, ticket=3001, pnl=50.0, symbol="EURUSD", setup="trend_zone")
        _add_trade(journal_db, ticket=3002, pnl=-20.0, symbol="EURUSD", setup="trend_zone")

        with patch("mt5_mcp.divergence_monitor.get_journal", return_value=journal_db):
            result = check_divergence(limit=30, send_alert=False)

        assert result["status"] == "insufficient_data"

    def test_check_divergence_no_divergence(self, journal_db):
        from mt5_mcp.divergence_monitor import check_divergence
        # Add trades that match backtest expectations (EURUSD trend_zone PF=3.25)
        for i in range(15):
            _add_trade(
                journal_db, ticket=4000 + i,
                pnl=80.0 if i % 3 != 2 else -30.0,
                symbol="EURUSD", setup="trend_zone",
            )

        with patch("mt5_mcp.divergence_monitor.get_journal", return_value=journal_db):
            result = check_divergence(limit=30, send_alert=False)

        # With mostly wins, should not trigger divergence
        assert result["status"] in ("ok", "insufficient_data")

    def test_check_divergence_triggers_alert(self, journal_db):
        from mt5_mcp.divergence_monitor import check_divergence
        # Add many losing trades for a high-PF strategy
        for i in range(20):
            _add_trade(
                journal_db, ticket=5000 + i,
                pnl=-40.0 if i % 4 != 0 else 20.0,  # 25% WR vs backtest ~62%
                symbol="EURUSD", setup="trend_zone",
            )

        with patch("mt5_mcp.divergence_monitor.get_journal", return_value=journal_db):
            with patch("mt5_mcp.divergence_monitor.notify", return_value=True) as mock_n:
                result = check_divergence(limit=30, send_alert=True)

        assert result["status"] == "divergence"
        assert len(result["alerts"]) > 0
        mock_n.assert_called_once()

    def test_per_strategy_divergence(self):
        from mt5_mcp.divergence_monitor import _per_strategy_divergence
        # 10 trades for EURUSD trend_zone (backtest PF=3.25), all losses
        trades = [
            {"symbol": "EURUSD", "setup": "trend_zone", "pnl": -50.0}
            for _ in range(10)
        ]
        alerts = _per_strategy_divergence(trades)
        assert len(alerts) > 0
        assert alerts[0]["symbol"] == "EURUSD"
        assert alerts[0]["type"] == "profit_factor"

    def test_format_divergence_alert(self):
        from mt5_mcp.divergence_monitor import _format_divergence_alert
        result = {
            "trades_analyzed": 20,
            "live": {"win_rate": 25.0, "pf": 0.5, "n": 20, "wins": 5, "losses": 15,
                     "gross_win": 100.0, "gross_loss": 200.0},
            "backtest": {"avg_bt_pf": 2.5, "avg_bt_wr": 55.0, "n_matched": 20},
            "alerts": [
                {"type": "win_rate", "live": 25.0, "expected": 55.0,
                 "threshold": 44.0, "detail": "Live WR 25.0% < expected 55.0% * 0.8"},
                {"symbol": "EURUSD", "setup": "trend_zone", "type": "profit_factor",
                 "live_value": 0.5, "backtest_value": 3.25, "threshold": 2.6,
                 "trades": 10, "severity": "high"},
            ],
        }
        html = _format_divergence_alert(result)
        assert "Divergence Alert" in html
        assert "EURUSD" in html
        assert "trend_zone" in html


# ========== Strategy Performance API Tests ==========


class TestStrategyPerformance:
    def test_endpoint_structure(self, journal_db):
        """Test that the strategy_performance logic returns correct structure."""
        now = datetime.datetime.now(datetime.timezone.utc)

        # Add trades across different time ranges
        for i in range(5):
            _add_trade(
                journal_db, ticket=6000 + i,
                pnl=50.0 if i % 2 == 0 else -20.0,
                setup="trend_zone", symbol="EURUSD",
                rr=1.5,
            )
        for i in range(3):
            _add_trade(
                journal_db, ticket=6100 + i,
                pnl=30.0 if i == 0 else -15.0,
                setup="mean_reversion", symbol="EURJPY",
                rr=2.0,
            )

        # Simulate the endpoint logic
        cutoff_30 = (now - datetime.timedelta(days=30)).isoformat()
        with journal_db._lock:
            conn = journal_db._conn()
            rows = conn.execute(
                "SELECT setup, pnl, rr, outcome FROM trades "
                "WHERE close_time >= ? AND pnl IS NOT NULL",
                [cutoff_30],
            ).fetchall()

        by_setup = {}
        for row in rows:
            setup = row["setup"] or "manual"
            if setup not in by_setup:
                by_setup[setup] = {"trades": 0, "wins": 0, "pnl": 0.0}
            by_setup[setup]["trades"] += 1
            pnl = float(row["pnl"] or 0)
            by_setup[setup]["pnl"] += pnl
            if pnl > 0:
                by_setup[setup]["wins"] += 1

        assert "trend_zone" in by_setup
        assert "mean_reversion" in by_setup
        assert by_setup["trend_zone"]["trades"] == 5
        assert by_setup["mean_reversion"]["trades"] == 3

    def test_empty_journal(self, journal_db):
        """Empty journal should return zero-trade windows."""
        now = datetime.datetime.now(datetime.timezone.utc)
        cutoff = (now - datetime.timedelta(days=30)).isoformat()
        with journal_db._lock:
            conn = journal_db._conn()
            rows = conn.execute(
                "SELECT setup, pnl, rr FROM trades WHERE close_time >= ? AND pnl IS NOT NULL",
                [cutoff],
            ).fetchall()
        assert len(rows) == 0

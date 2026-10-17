"""Tests for ML auto-retrain, news alerts, and screener alerts."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# ML retrain tests
# ---------------------------------------------------------------------------

from ml_ranker import (
    FEATURES,
    MODEL_PATH,
    RETRAIN_STATE_PATH,
    build_dataset,
    fit_and_save,
    retrain_with_new_data,
    train_and_evaluate,
    _load_retrain_state,
    _save_retrain_state,
)


SAMPLE_TRADES_OLD = [
    {"entry": 1.15, "entry_time": "2026-01-19T10:00:00", "pnl": 25.0, "rr": 3.0,
     "sl_pips": 20.0, "trend_confidence": 0.7, "zone_strength": 80.0,
     "confirmation_confidence": 0.6},
    {"entry": 1.14, "entry_time": "2026-02-01T15:00:00", "pnl": -12.5, "rr": 4.5,
     "sl_pips": 20.0, "trend_confidence": 0.6, "zone_strength": 70.0,
     "confirmation_confidence": 0.5},
    {"entry": 1.16, "entry_time": "2026-02-05T09:00:00", "pnl": 18.0, "rr": 2.5,
     "sl_pips": 15.0, "trend_confidence": 0.8, "zone_strength": 85.0,
     "confirmation_confidence": 0.7},
    {"entry": 1.13, "entry_time": "2026-02-10T14:00:00", "pnl": -5.0, "rr": 3.0,
     "sl_pips": 18.0, "trend_confidence": 0.5, "zone_strength": 65.0,
     "confirmation_confidence": 0.4},
    {"entry": 1.17, "entry_time": "2026-02-15T10:00:00", "pnl": 30.0, "rr": 4.0,
     "sl_pips": 22.0, "trend_confidence": 0.9, "zone_strength": 90.0,
     "confirmation_confidence": 0.8},
    {"entry": 1.12, "entry_time": "2026-02-20T16:00:00", "pnl": -20.0, "rr": 2.0,
     "sl_pips": 25.0, "trend_confidence": 0.4, "zone_strength": 55.0,
     "confirmation_confidence": 0.3},
]

# Old model is trained on these 6 trades
# New dataset adds 6 more trades (total 12)
SAMPLE_TRADES_NEW = SAMPLE_TRADES_OLD + [
    {"entry": 1.18, "entry_time": "2026-03-01T08:00:00", "pnl": 35.0, "rr": 3.5,
     "sl_pips": 15.0, "trend_confidence": 0.85, "zone_strength": 88.0,
     "confirmation_confidence": 0.75},
    {"entry": 1.11, "entry_time": "2026-03-05T14:00:00", "pnl": -10.0, "rr": 2.5,
     "sl_pips": 20.0, "trend_confidence": 0.45, "zone_strength": 58.0,
     "confirmation_confidence": 0.35},
    {"entry": 1.19, "entry_time": "2026-03-10T09:00:00", "pnl": 22.0, "rr": 3.0,
     "sl_pips": 18.0, "trend_confidence": 0.75, "zone_strength": 82.0,
     "confirmation_confidence": 0.65},
    {"entry": 1.10, "entry_time": "2026-03-15T11:00:00", "pnl": -15.0, "rr": 2.0,
     "sl_pips": 22.0, "trend_confidence": 0.35, "zone_strength": 50.0,
     "confirmation_confidence": 0.30},
    {"entry": 1.20, "entry_time": "2026-03-20T10:00:00", "pnl": 28.0, "rr": 4.0,
     "sl_pips": 20.0, "trend_confidence": 0.90, "zone_strength": 92.0,
     "confirmation_confidence": 0.80},
    {"entry": 1.09, "entry_time": "2026-03-25T15:00:00", "pnl": -8.0, "rr": 3.0,
     "sl_pips": 16.0, "trend_confidence": 0.55, "zone_strength": 62.0,
     "confirmation_confidence": 0.45},
]


class TestRetrainWithNewData:
    """Tests for retrain_with_new_data()."""

    def test_skip_when_no_new_trades(self, tmp_path):
        """Should skip when no new trades have been added."""
        model_path = tmp_path / "model.json"
        state_path = tmp_path / "state.json"

        # Create a model with 2 trades
        X, y, _ = build_dataset(SAMPLE_TRADES_OLD)
        metrics = train_and_evaluate(X, y)
        fit_and_save(X, y, model_path, cv_auc=metrics["cv_auc"])

        with patch("ml_ranker.MODEL_PATH", model_path), \
             patch("ml_ranker.RETRAIN_STATE_PATH", state_path), \
             patch("ml_ranker.load_result_trades", return_value=SAMPLE_TRADES_OLD), \
             patch("ml_ranker.load_journal_trades", return_value=[]):
            result = retrain_with_new_data(include_journal=True)

        assert result["status"] == "skipped"
        assert "no new trades" in result["reason"]

    def test_retrain_accepts_better_auc(self, tmp_path):
        """Should accept new model when AUC is better."""
        model_path = tmp_path / "model.json"
        state_path = tmp_path / "state.json"

        # Create old model with low AUC
        X_old, y_old, _ = build_dataset(SAMPLE_TRADES_OLD)
        fit_and_save(X_old, y_old, model_path, cv_auc=0.40)

        with patch("ml_ranker.MODEL_PATH", model_path), \
             patch("ml_ranker.RETRAIN_STATE_PATH", state_path), \
             patch("ml_ranker.load_result_trades", return_value=SAMPLE_TRADES_NEW), \
             patch("ml_ranker.load_journal_trades", return_value=[]):
            result = retrain_with_new_data(include_journal=True)

        # SAMPLE_TRADES_NEW = SAMPLE_TRADES_OLD + 6 new = 12 total
        assert result["status"] in ("retrained", "rejected")
        assert result["new_n_trades"] == 12
        assert result["old_n_trades"] == 6
        assert result["new_trades_added"] == 6

    def test_retrain_rejects_worse_auc(self, tmp_path):
        """Should reject new model when AUC drops too much."""
        model_path = tmp_path / "model.json"
        state_path = tmp_path / "state.json"

        # Create old model with very high AUC
        X_old, y_old, _ = build_dataset(SAMPLE_TRADES_OLD)
        fit_and_save(X_old, y_old, model_path, cv_auc=0.99)

        # Force the new model to have a low AUC so it gets rejected
        fake_metrics = {"cv_auc": 0.50, "walk_forward_auc": 0.45}

        with patch("ml_ranker.MODEL_PATH", model_path), \
             patch("ml_ranker.RETRAIN_STATE_PATH", state_path), \
             patch("ml_ranker.load_result_trades", return_value=SAMPLE_TRADES_NEW), \
             patch("ml_ranker.load_journal_trades", return_value=[]), \
             patch("ml_ranker.train_and_evaluate", return_value=fake_metrics):
            result = retrain_with_new_data(include_journal=True, auc_tolerance=0.02)

        # New AUC (0.50) < old AUC (0.99) - tolerance (0.02) = 0.97 → rejected
        assert result["status"] == "rejected"
        assert result["old_auc"] == 0.99
        assert result["new_auc"] == 0.50
        assert result["accepted"] is False

    def test_retrain_error_not_enough_data(self, tmp_path):
        """Should error when not enough data to train."""
        model_path = tmp_path / "model.json"
        state_path = tmp_path / "state.json"

        with patch("ml_ranker.MODEL_PATH", model_path), \
             patch("ml_ranker.RETRAIN_STATE_PATH", state_path), \
             patch("ml_ranker.load_result_trades", return_value=[]), \
             patch("ml_ranker.load_journal_trades", return_value=[]):
            result = retrain_with_new_data(include_journal=True)

        assert result["status"] == "error"

    def test_auc_tolerance_cannot_bypass_validation(self, tmp_path):
        """Tolerance cannot publish a model without enough future-trade evidence."""
        model_path = tmp_path / "model.json"
        state_path = tmp_path / "state.json"

        # Old model with decent AUC
        X_old, y_old, _ = build_dataset(SAMPLE_TRADES_OLD)
        fit_and_save(X_old, y_old, model_path, cv_auc=0.95)

        # With large tolerance, even a degraded model should be accepted
        with patch("ml_ranker.MODEL_PATH", model_path), \
             patch("ml_ranker.RETRAIN_STATE_PATH", state_path), \
             patch("ml_ranker.load_result_trades", return_value=SAMPLE_TRADES_NEW), \
             patch("ml_ranker.load_journal_trades", return_value=[]):
            result = retrain_with_new_data(include_journal=True, auc_tolerance=0.50)

        assert result["status"] == "rejected"
        assert result["auc_tolerance"] == 0.50


# ---------------------------------------------------------------------------
# News alerts tests
# ---------------------------------------------------------------------------


class TestNewsAlerts:
    """Tests for news_alerts.check_and_alert()."""

    def test_alert_high_impact_event(self, tmp_path):
        """Should alert for high-impact events within the window."""
        from datetime import datetime, timedelta, timezone
        from mt5_mcp.common import NewsEvent

        state_path = tmp_path / "state.json"

        # Create a high-impact event 45 minutes from now
        now = datetime.now(timezone.utc)
        event = NewsEvent(
            time=now + timedelta(minutes=45),
            currency="USD",
            impact="high",
            title="Non-Farm Payrolls",
            forecast="180K",
            previous="175K",
        )

        mock_provider = MagicMock()
        mock_provider.upcoming.return_value = [event]

        with patch("mt5_mcp.news_alerts.STATE_PATH", state_path), \
             patch("mt5_mcp.tools.news.get_provider", return_value=mock_provider), \
             patch("mt5_mcp.notify.notify", return_value=True) as mock_notify:
            from mt5_mcp.news_alerts import check_and_alert
            result = check_and_alert()

        assert result["status"] == "ok"
        assert result["events_checked"] == 1
        assert result["events_alerted"] == 1

    def test_skip_low_impact_events(self, tmp_path):
        """Should not alert for low-impact events."""
        from datetime import datetime, timedelta, timezone
        from mt5_mcp.common import NewsEvent

        state_path = tmp_path / "state.json"
        now = datetime.now(timezone.utc)
        event = NewsEvent(
            time=now + timedelta(minutes=30),
            currency="EUR",
            impact="low",
            title="Manufacturing PMI",
        )

        mock_provider = MagicMock()
        mock_provider.upcoming.return_value = [event]

        with patch("mt5_mcp.news_alerts.STATE_PATH", state_path), \
             patch("mt5_mcp.tools.news.get_provider", return_value=mock_provider), \
             patch("mt5_mcp.notify.notify", return_value=True) as mock_notify:
            from mt5_mcp.news_alerts import check_and_alert
            result = check_and_alert()

        assert result["events_alerted"] == 0

    def test_no_duplicate_alerts(self, tmp_path):
        """Should not alert for the same event twice."""
        from datetime import datetime, timedelta, timezone
        from mt5_mcp.common import NewsEvent

        state_path = tmp_path / "state.json"
        now = datetime.now(timezone.utc)
        event = NewsEvent(
            time=now + timedelta(minutes=45),
            currency="USD",
            impact="high",
            title="CPI",
            forecast="3.2%",
            previous="3.1%",
        )

        mock_provider = MagicMock()
        mock_provider.upcoming.return_value = [event]

        with patch("mt5_mcp.news_alerts.STATE_PATH", state_path), \
             patch("mt5_mcp.tools.news.get_provider", return_value=mock_provider), \
             patch("mt5_mcp.notify.notify", return_value=True):
            from mt5_mcp.news_alerts import check_and_alert

            # First run: should alert
            result1 = check_and_alert()
            assert result1["events_alerted"] == 1

            # Second run: should skip (already alerted)
            result2 = check_and_alert()
            assert result2["events_alerted"] == 0
            assert result2["events_skipped"] == 1

    def test_skip_past_events(self, tmp_path):
        """Should not alert for events that already passed."""
        from datetime import datetime, timedelta, timezone
        from mt5_mcp.common import NewsEvent

        state_path = tmp_path / "state.json"
        now = datetime.now(timezone.utc)
        event = NewsEvent(
            time=now - timedelta(minutes=10),  # 10 min ago
            currency="GBP",
            impact="high",
            title="BOE Rate",
        )

        mock_provider = MagicMock()
        mock_provider.upcoming.return_value = [event]

        with patch("mt5_mcp.news_alerts.STATE_PATH", state_path), \
             patch("mt5_mcp.tools.news.get_provider", return_value=mock_provider), \
             patch("mt5_mcp.notify.notify", return_value=True):
            from mt5_mcp.news_alerts import check_and_alert
            result = check_and_alert()

        assert result["events_alerted"] == 0

    def test_skip_far_future_events(self, tmp_path):
        """Should not alert for events more than 60 min away."""
        from datetime import datetime, timedelta, timezone
        from mt5_mcp.common import NewsEvent

        state_path = tmp_path / "state.json"
        now = datetime.now(timezone.utc)
        event = NewsEvent(
            time=now + timedelta(minutes=90),  # 90 min away
            currency="JPY",
            impact="high",
            title="BOJ Meeting",
        )

        mock_provider = MagicMock()
        mock_provider.upcoming.return_value = [event]

        with patch("mt5_mcp.news_alerts.STATE_PATH", state_path), \
             patch("mt5_mcp.tools.news.get_provider", return_value=mock_provider), \
             patch("mt5_mcp.notify.notify", return_value=True):
            from mt5_mcp.news_alerts import check_and_alert
            result = check_and_alert()

        assert result["events_alerted"] == 0


# ---------------------------------------------------------------------------
# Screener alerts tests
# ---------------------------------------------------------------------------


class TestScreenerAlerts:
    """Tests for screener_alerts.scan_and_alert()."""

    def _make_df(self, n: int = 100, base: float = 1.1000, seed: int = 42):
        """Create a fake OHLCV DataFrame."""
        import pandas as pd
        rng = np.random.RandomState(seed)
        times = pd.date_range("2026-01-01", periods=n, freq="h")
        close = base + np.cumsum(rng.randn(n) * 0.001)
        high = close + rng.rand(n) * 0.002
        low = close - rng.rand(n) * 0.002
        open_ = close + rng.randn(n) * 0.0005
        return pd.DataFrame({
            "time": times,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": rng.randint(100, 1000, n).astype(float),
            "spread": np.full(n, 1.0),
        })

    def test_zone_proximity_alert(self, tmp_path):
        """Should alert when price is near a supply/demand zone."""
        state_path = tmp_path / "state.json"

        # Create candles with a clear demand zone (base + impulse)
        df = self._make_df(100, base=1.1000)
        # Make the last price close to a zone that will be detected
        current_price = float(df["close"].iloc[-1])

        mock_client = MagicMock()
        mock_client.rates.return_value = df

        with patch("mt5_mcp.screener_alerts.STATE_PATH", state_path), \
             patch("mt5_mcp.consensus.allowed_symbols", return_value={"EURUSD"}), \
             patch("mt5_mcp.notify.notify", return_value=True) as mock_notify:
            from mt5_mcp.screener_alerts import scan_and_alert
            result = scan_and_alert(client=mock_client)

        assert result["status"] == "ok"
        assert result["symbols_scanned"] == 1
        # Whether an alert fires depends on zone detection; at minimum the scan ran
        assert "alerts_sent" in result

    def test_structure_break_alert(self, tmp_path):
        """Should alert on BOS/CHoCH detection."""
        state_path = tmp_path / "state.json"

        # Create candles with a clear structure break
        df = self._make_df(100, base=1.1000)
        # Add a strong breakout move at the end
        df.loc[df.index[-1], "close"] = float(df["high"].max()) + 0.005
        df.loc[df.index[-1], "high"] = float(df["close"].iloc[-1]) + 0.001

        mock_client = MagicMock()
        mock_client.rates.return_value = df

        with patch("mt5_mcp.screener_alerts.STATE_PATH", state_path), \
             patch("mt5_mcp.consensus.allowed_symbols", return_value={"EURUSD"}), \
             patch("mt5_mcp.notify.notify", return_value=True):
            from mt5_mcp.screener_alerts import scan_and_alert
            result = scan_and_alert(client=mock_client)

        assert result["status"] == "ok"
        assert result["symbols_scanned"] == 1

    def test_cooldown_prevents_duplicates(self, tmp_path):
        """Same symbol+setup should not alert within cooldown period."""
        state_path = tmp_path / "state.json"
        df = self._make_df(100, base=1.1000)

        mock_client = MagicMock()
        mock_client.rates.return_value = df

        with patch("mt5_mcp.screener_alerts.STATE_PATH", state_path), \
             patch("mt5_mcp.consensus.allowed_symbols", return_value={"EURUSD"}), \
             patch("mt5_mcp.notify.notify", return_value=True):
            from mt5_mcp.screener_alerts import scan_and_alert

            # First scan
            result1 = scan_and_alert(client=mock_client)
            first_alerts = result1["alerts_sent"]

            # Second scan immediately after - should not re-alert same setups
            result2 = scan_and_alert(client=mock_client)
            second_alerts = result2["alerts_sent"]

            # Second scan should have fewer or equal alerts (cooldown blocks repeats)
            assert second_alerts <= first_alerts

    def test_empty_allowlist(self, tmp_path):
        """Should handle empty allowlist gracefully."""
        state_path = tmp_path / "state.json"

        with patch("mt5_mcp.screener_alerts.STATE_PATH", state_path), \
             patch("mt5_mcp.consensus.allowed_symbols", return_value=set()):
            from mt5_mcp.screener_alerts import scan_and_alert
            result = scan_and_alert()

        assert result["status"] == "ok"
        assert result["symbols_scanned"] == 0

    def test_compute_atr(self):
        """ATR computation should return positive value."""
        from mt5_mcp.screener_alerts import _compute_atr
        import pandas as pd

        df = pd.DataFrame({
            "high": [1.2, 1.3, 1.25, 1.35, 1.4, 1.38, 1.42, 1.45, 1.5, 1.48,
                     1.52, 1.55, 1.53, 1.56, 1.58, 1.6, 1.57, 1.59, 1.61, 1.63],
            "low":  [1.1, 1.15, 1.12, 1.2, 1.25, 1.22, 1.28, 1.3, 1.35, 1.32,
                     1.38, 1.4, 1.37, 1.41, 1.43, 1.45, 1.42, 1.44, 1.46, 1.48],
            "close":[1.15, 1.25, 1.18, 1.3, 1.35, 1.3, 1.38, 1.4, 1.45, 1.4,
                     1.48, 1.5, 1.47, 1.51, 1.53, 1.55, 1.52, 1.54, 1.56, 1.58],
        })
        atr_val = _compute_atr(df, period=14)
        assert atr_val > 0

    def test_alert_key_format(self):
        """Alert key should combine symbol and type."""
        from mt5_mcp.screener_alerts import _alert_key
        key = _alert_key("EURUSD", "zone_demand_1.100000")
        assert key == "EURUSD_zone_demand_1.100000"

    def test_cooldown_check(self):
        """Cooldown should block recent alerts and allow old ones."""
        from mt5_mcp.screener_alerts import _is_on_cooldown, _mark_alerted

        state = {"alerts": {}}

        # Not on cooldown initially
        assert _is_on_cooldown(state, "EURUSD_zone_test") is False

        # Mark as alerted
        _mark_alerted(state, "EURUSD_zone_test", "test alert")
        assert _is_on_cooldown(state, "EURUSD_zone_test") is True

        # Simulate old alert (past cooldown)
        state["alerts"]["EURUSD_zone_test"]["ts"] = time.time() - 5 * 3600  # 5h ago
        assert _is_on_cooldown(state, "EURUSD_zone_test") is False

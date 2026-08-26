from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from backtest_boom_crash import Trade, _metrics, _normalize


def test_normalize_handles_epoch_seconds_and_mt5_datetime_resolution():
    raw = pd.DataFrame(
        {
            "time": [1786384320, 1786384380],
            "open": [1.0, 1.0],
            "high": [1.1, 1.1],
            "low": [0.9, 0.9],
            "close": [1.0, 1.0],
            "volume": [10, 10],
        }
    )
    assert _normalize(raw)["timestamp"].tolist() == [1786384320, 1786384380]


def test_metrics_reports_hold_time():
    opened = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trade = Trade(
        symbol="CRASH900",
        entry_time=opened,
        exit_time=opened + timedelta(minutes=37),
        entry_price=100.0,
        exit_price=102.0,
        sl_price=98.0,
        tp_price=103.0,
        direction="long",
        risk_price=2.0,
        pnl_price=2.0,
    )
    result = _metrics([trade], ["CRASH900"], {})
    assert result["metrics"]["avg_hold_minutes"] == 37.0

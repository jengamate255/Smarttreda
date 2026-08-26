import numpy as np
import pandas as pd

from chart_patterns import analyze_chart, detect_chart_patterns


def _frame(rows: int = 260) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    close = 100.0 + index * 0.03 + 1.8 * np.sin(index / 8.0)
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=rows, freq="h", tz="UTC"),
            "open": close - 0.05,
            "high": close + 0.45,
            "low": close - 0.45,
            "close": close,
            "tick_volume": 100 + (index % 7),
        }
    )


def test_chart_detection_report_is_explainable():
    report = analyze_chart(_frame(), symbol="TEST", timeframe="H1")
    assert report["symbol"] == "TEST"
    assert report["verdict"] in {"high_quality", "watch", "no_pattern"}
    assert isinstance(report["patterns"], list)
    assert isinstance(report["support_resistance"], list)
    assert isinstance(report["market"], dict)
    for pattern in report["patterns"]:
        assert 0 <= pattern["ai_score"] <= 100
        assert pattern["status"] in {"emerging", "confirmed"}
        assert pattern["evidence"]


def test_chart_detection_handles_short_input():
    assert detect_chart_patterns(_frame(20)) == []

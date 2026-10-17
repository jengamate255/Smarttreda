"""Chart timestamps must use epoch seconds regardless of pandas storage units."""
from types import SimpleNamespace

import pandas as pd
import pytest


@pytest.mark.parametrize("unit", ["ns", "us", "ms", "s", "numeric"])
def test_rates_timestamps_are_epoch_seconds(monkeypatch, unit):
    import web_dashboard as dashboard

    expected = [1791849600, 1791853200]
    dates = pd.to_datetime(expected, unit="s", utc=True)
    times = expected if unit == "numeric" else dates.as_unit(unit)
    frame = pd.DataFrame({"time": times, "open": [1, 1], "high": [2, 2],
                          "low": [0.5, 0.5], "close": [1.5, 1.5], "tick_volume": [10, 20]})
    monkeypatch.setattr(dashboard, "_require_connected", lambda: None)
    monkeypatch.setattr(dashboard, "get_client", lambda: SimpleNamespace(rates=lambda *_: frame))
    result = dashboard._fetch_rates_raw("EURUSD", "H1", 2)
    assert result[0] == expected


def test_rates_reports_broker_precision(monkeypatch):
    import web_dashboard as dashboard

    count = 210
    monkeypatch.setattr(dashboard, "_rates_cache", {})
    monkeypatch.setattr(dashboard, "_fetch_rates_raw", lambda *_: (
        list(range(1791849600, 1791849600 + count * 3600, 3600)),
        [1.12345] * count, [1.12456] * count, [1.12234] * count,
        [1.12345] * count, [10] * count,
    ))
    monkeypatch.setattr(dashboard.mt5, "symbol_info_tick", lambda _: None)
    monkeypatch.setattr(dashboard.mt5, "symbol_info", lambda _: SimpleNamespace(digits=5, point=0.00001))
    result = dashboard.rates("EURUSD", "H1", count)
    assert result["digits"] == 5
    assert result["point"] == 0.00001

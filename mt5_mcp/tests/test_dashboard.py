"""Tests for the web dashboard (overview, backtesting, forward testing)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import dashboard
from dashboard import app


def _frame(n: int, start: str = "2026-01-01") -> pd.DataFrame:
    times = pd.date_range(start=start, periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "time": times,
            "open": np.linspace(1.0, 1.3, n),
            "high": np.linspace(1.0, 1.3, n) + 0.001,
            "low": np.linspace(1.0, 1.3, n) - 0.001,
            "close": np.linspace(1.0, 1.3, n),
            "tick_volume": np.ones(n, dtype=int),
            "spread": np.zeros(n, dtype=int),
            "real_volume": np.zeros(n, dtype=int),
        }
    )


@pytest.fixture()
def fake_frames(monkeypatch, tmp_path):
    def _load(symbol, tfs, bars):
        base = _frame(max(bars, 500), "2026-04-01")
        return {tf: base for tf in tfs}

    monkeypatch.setattr(dashboard, "load_frames", _load)
    monkeypatch.setattr(dashboard, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(dashboard, "DEFAULT_OUT", tmp_path / "results" / "month")
    monkeypatch.setattr(dashboard, "BASE", tmp_path)
    return dashboard


def test_overview_page_renders(fake_frames) -> None:
    with TestClient(app) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert "Backtesting" in resp.text


def test_backtest_page_runs_and_renders(fake_frames, tmp_path) -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/backtest",
            data={
                "symbol": "EURUSD",
                "trend_tf": "H4",
                "zone_tfs": "H1,H4",
                "min_zone_score": "40",
                "min_rr": "3",
                "tp_min": "80",
                "tp_max": "100",
                "sl_min": "20",
                "risk": "0.5",
                "bars": "500",
                "start_balance": "10000",
                "out": str(tmp_path / "month"),
            },
        )
    assert resp.status_code == 200
    assert "Backtest - EURUSD" in resp.text
    assert (tmp_path / "month" / "summary.csv").exists()


def test_backtest_form_page(fake_frames) -> None:
    with TestClient(app) as client:
        resp = client.get("/backtest")
    assert resp.status_code == 200
    assert "Run backtest" in resp.text


def test_forward_page_runs_windowed(fake_frames, tmp_path) -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/forward",
            data={
                "symbol": "EURUSD",
                "since": "2026-06-01",
                "trend_tf": "H4",
                "zone_tfs": "H1,H4",
                "min_zone_score": "40",
                "min_rr": "3",
                "tp_min": "80",
                "tp_max": "100",
                "risk": "0.5",
                "bars": "500",
            },
        )
    assert resp.status_code == 200
    assert "Forward Test - EURUSD" in resp.text
    assert "Forward window" in resp.text
    assert "Full backtest" in resp.text


def test_forward_form_page(fake_frames) -> None:
    with TestClient(app) as client:
        resp = client.get("/forward")
    assert resp.status_code == 200
    assert "Run forward test" in resp.text

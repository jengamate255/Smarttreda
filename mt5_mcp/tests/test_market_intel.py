"""Tests for the Market Intelligence Agent."""

from __future__ import annotations

from helpers import build_frame, rising_series

from market_intel import (
    atr_percentile,
    current_session,
    market_health,
    market_regime,
    scan_markets,
    session_name,
)

TRENDING = rising_series(n=400, step=0.0005)


def _install(fake_client, frame, symbol: str = "EURUSD", tf: str = "H1") -> str:
    fake_client.frames[(symbol, tf)] = frame
    fake_client.bid = float(frame["close"].iloc[-1]) - 0.00005
    fake_client.ask = float(frame["close"].iloc[-1]) + 0.00005
    return symbol


def test_session_name():
    primary, active = session_name(14)
    assert primary == "new_york"
    assert "london" in active
    assert session_name(22)[0] == "sydney"
    assert session_name(3)[0] == "tokyo"
    assert current_session(14)["primary"] == "new_york"


def test_atr_percentile():
    result = atr_percentile(TRENDING)
    assert 0.0 <= result["atr_percentile"] <= 100.0
    assert "abnormal_volatility" in result
    assert result["atr"] > 0.0


def test_regime_trending_bullish():
    regime = market_regime(TRENDING)
    assert regime["regime"] == "trending_bullish"
    assert regime["trend"] == "bullish"
    assert regime["evidence"]
    assert regime["abnormal_volatility"] is False


def test_regime_ranging():
    flat = build_frame([1.2] * 300, wick=0.0001)
    regime = market_regime(flat)
    assert regime["regime"] in ("ranging", "quiet_contraction")
    assert regime["trend"] == "sideways"


def test_regime_not_enough_data():
    small = rising_series(n=30)
    regime = market_regime(small)
    assert regime["regime"] == "unknown"


def test_market_health(fake_client, client_module, monkeypatch):
    symbol = _install(fake_client, TRENDING)
    client_module.set_client(fake_client)
    monkeypatch.setattr("market_intel._session_score", lambda primary: 70.0)
    monkeypatch.setattr("market_intel._news_score", lambda settings: 100.0)
    report = market_health(symbol)
    assert report["symbol"] == symbol
    assert 0.0 <= report["health_score"] <= 100.0
    assert report["verdict"] in ("tradeable", "cautious", "avoid")
    assert report["regime"] == "trending_bullish"
    names = {c["name"] for c in report["components"]}
    assert names == {
        "trend",
        "volatility",
        "regime",
        "spread",
        "session",
        "news",
        "liquidity",
    }
    assert report["evidence"]
    assert report["session"]["primary"] in (
        "new_york",
        "london",
        "tokyo",
        "sydney",
        "off_hours",
    )


def test_market_health_unavailable(fake_client, client_module):
    client_module.set_client(fake_client)
    report = market_health("NOPE")
    assert report["health_score"] == 0.0
    assert report["verdict"] == "unavailable"
    assert report["errors"]


def test_scan_markets(fake_client, client_module, monkeypatch):
    _install(fake_client, TRENDING, symbol="EURUSD")
    _install(fake_client, build_frame([1.3] * 300, wick=0.0001), symbol="GBPUSD")
    client_module.set_client(fake_client)
    monkeypatch.setattr("market_intel._session_score", lambda primary: 70.0)
    monkeypatch.setattr("market_intel._news_score", lambda settings: 100.0)
    result = scan_markets(symbols=["EURUSD", "GBPUSD"], min_score=50.0)
    assert result["scanned"] == 2
    assert result["ranked"]
    assert result["ranked"][0]["symbol"] == "EURUSD"
    assert result["ranked"][0]["health_score"] >= 50.0
    assert "failed" in result

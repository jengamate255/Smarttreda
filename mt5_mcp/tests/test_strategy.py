"""End-to-end strategy test with synthetic data."""

from __future__ import annotations

from helpers import downtrend_then_base_drop, rising_series, uptrend_then_base_rally

from strategy import analyze_trend, evaluate_strategy
from tools.strategy import trend_analysis


def _install(fake_client, symbol: str = "EURUSD") -> str:
    h4 = rising_series(n=400, step=0.0005)
    h1 = uptrend_then_base_rally()
    fake_client.frames[(symbol, "H4")] = h4
    fake_client.frames[(symbol, "H1")] = h1
    fake_client.bid = float(h1["close"].iloc[-1]) - 0.00005
    fake_client.ask = float(h1["close"].iloc[-1]) + 0.00005
    return symbol


def test_analyze_trend_bullish():
    frame = rising_series(n=300, step=0.0005)
    trend = analyze_trend(frame)
    assert trend.direction == "bullish"
    assert trend.ema_fast > trend.ema_slow
    assert trend.confidence > 0.5


def test_analyze_trend_bearish():
    frame = downtrend_then_base_drop()
    trend = analyze_trend(frame)
    assert trend.direction == "bearish"


def test_evaluate_strategy_buy_plan(fake_client, client_module):
    symbol = _install(fake_client)
    client_module.set_client(fake_client)
    plan = evaluate_strategy(
        symbol,
        risk_percent=0.5,
        min_zone_score=50,
        min_trade_score=60,
        trend_timeframe="H4",
        zone_timeframes=["H1"],
        confirmation_timeframe="H1",
    )
    assert plan.action == "BUY"
    assert plan.valid is True, plan.reason
    assert plan.entry is not None and plan.sl is not None and plan.tp is not None
    assert plan.sl < plan.entry < plan.tp
    assert plan.risk_reward >= 3.0, plan.reason
    assert 80.0 <= plan.tp_pips <= 100.0 or plan.tp_pips > 100.0
    assert plan.sl_pips >= 20.0
    assert plan.lots > 0
    assert plan.trade_score >= 60.0
    assert plan.confidence > 0.0
    assert "confirmation" in plan.model_dump()
    assert 0.0 < plan.ml_score < 1.0, "ML ranker should score an actionable plan"
    assert "ML ranker" in plan.ml_note


def test_evaluate_strategy_rejects_sideways(fake_client, client_module):
    from helpers import build_frame

    symbol = "EURUSD"
    flat = build_frame([1.2] * 300, wick=0.0001)
    fake_client.frames[(symbol, "H4")] = flat
    fake_client.frames[(symbol, "H1")] = flat
    fake_client.bid = fake_client.ask = 1.2
    client_module.set_client(fake_client)
    plan = evaluate_strategy(
        symbol,
        trend_timeframe="H4",
        zone_timeframes=["H1"],
        confirmation_timeframe="H1",
    )
    assert plan.action == "NONE"
    assert plan.valid is False
    assert plan.reason


def test_trend_tool(fake_client, client_module):
    symbol = _install(fake_client)
    client_module.set_client(fake_client)
    result = trend_analysis(symbol, timeframe="H4", candles=400)
    assert result.direction == "bullish"

"""Strategy tools: full strategy evaluation and standalone trend analysis."""

from __future__ import annotations

from common import TradePlan, TrendResult
from strategy import evaluate_strategy, trend_analysis


def register(mcp) -> None:
    @mcp.tool(
        name="evaluate_strategy",
        description="Evaluate the price action strategy for a symbol: EMA50/200 trend + H1/H4 supply/demand zones + "
        "confirmation (engulfing, pin bar, hammer, BOS, CHOCH). Outputs BUY/SELL with confidence, entry, SL, TP, "
        "risk:reward, lot size and trade score. Risk 0.5% per trade, SL 20-30 pips or beyond zone, TP 80-100 pips, min RR 1:3.",
    )
    def tool_evaluate_strategy(
        symbol: str,
        risk_percent: float | None = None,
        tp_min: float | None = None,
        tp_max: float | None = None,
        sl_min: float | None = None,
        sl_max: float | None = None,
        min_rr: float | None = None,
        min_zone_score: float | None = None,
        min_trade_score: float | None = None,
        min_confidence_percent: float | None = None,
        trend_timeframe: str | None = None,
        zone_timeframes: list[str] | None = None,
        confirmation_timeframe: str | None = None,
        lookback: int | None = None,
    ) -> TradePlan:
        return evaluate_strategy(
            symbol,
            risk_percent=risk_percent,
            tp_min=tp_min,
            tp_max=tp_max,
            sl_min=sl_min,
            sl_max=sl_max,
            min_rr=min_rr,
            min_zone_score=min_zone_score,
            min_trade_score=min_trade_score,
            min_confidence_percent=min_confidence_percent,
            trend_timeframe=trend_timeframe,
            zone_timeframes=zone_timeframes,
            confirmation_timeframe=confirmation_timeframe,
            lookback=lookback,
        )

    @mcp.tool(
        name="trend_analysis",
        description="Determine market trend (bullish/bearish/sideways) using EMA 50/200 and market structure HH/HL/LH/LL.",
    )
    def tool_trend_analysis(
        symbol: str, timeframe: str = "H4", candles: int = 400
    ) -> TrendResult:
        return trend_analysis(symbol, timeframe, candles)

"""Indicator tools: EMA, SMA, ATR, RSI, MACD, Bollinger, VWAP, volume, swings."""

from __future__ import annotations

import pandas as pd

from client import get_client
from common import Timeframe
from indicators import (
    atr,
    bollinger,
    ema,
    macd,
    rsi,
    sma,
    swing_highs,
    swing_lows,
    vwap,
)

DEFAULT_COUNT = 400


def _frame(symbol: str, timeframe: Timeframe, count: int) -> pd.DataFrame:
    client = get_client()
    client.ensure_connected()
    return client.rates(symbol, timeframe, count)


def _last_points(series: pd.Series, limit: int = 120) -> dict:
    tail = series.dropna().tail(limit)
    return {
        "values": [float(v) for v in tail.tolist()],
        "timestamps": [
            t.isoformat() if hasattr(t, "isoformat") else str(t)
            for t in tail.index.tolist()
        ],
        "last": float(series.dropna().iloc[-1]) if series.notna().any() else None,
    }


def _series_view(series: pd.Series, limit: int = 120) -> list[dict]:
    tail = series.dropna().tail(limit)
    return [
        {
            "time": t.isoformat() if hasattr(t, "isoformat") else str(t),
            "value": round(float(v), 6),
        }
        for t, v in tail.items()
    ]


def indicator_ema(
    symbol: str,
    timeframe: Timeframe = "H1",
    period: int = 50,
    count: int = DEFAULT_COUNT,
) -> dict:
    df = _frame(symbol, timeframe, count)
    values = ema(df["close"], period)
    return {"indicator": "EMA", "period": period, "series": _series_view(values)}


def indicator_sma(
    symbol: str,
    timeframe: Timeframe = "H1",
    period: int = 50,
    count: int = DEFAULT_COUNT,
) -> dict:
    df = _frame(symbol, timeframe, count)
    values = sma(df["close"], period)
    return {"indicator": "SMA", "period": period, "series": _series_view(values)}


def indicator_atr(
    symbol: str,
    timeframe: Timeframe = "H1",
    period: int = 14,
    count: int = DEFAULT_COUNT,
) -> dict:
    df = _frame(symbol, timeframe, count)
    values = atr(df, period)
    return {"indicator": "ATR", "period": period, "series": _series_view(values)}


def indicator_rsi(
    symbol: str,
    timeframe: Timeframe = "H1",
    period: int = 14,
    count: int = DEFAULT_COUNT,
) -> dict:
    df = _frame(symbol, timeframe, count)
    values = rsi(df["close"], period)
    return {
        "indicator": "RSI",
        "period": period,
        "last": float(values.dropna().iloc[-1]),
        "oversold": float(values.dropna().iloc[-1]) < 30,
        "overbought": float(values.dropna().iloc[-1]) > 70,
        "series": _series_view(values),
    }


def indicator_macd(
    symbol: str,
    timeframe: Timeframe = "H1",
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    count: int = DEFAULT_COUNT,
) -> dict:
    df = _frame(symbol, timeframe, count)
    result = macd(df["close"], fast, slow, signal)
    last = result.dropna().iloc[-1]
    return {
        "indicator": "MACD",
        "fast": fast,
        "slow": slow,
        "signal": signal,
        "last": {
            "macd": round(float(last.macd), 6),
            "signal": round(float(last.signal), 6),
            "histogram": round(float(last.histogram), 6),
        },
        "series": {
            "macd": _series_view(result["macd"]),
            "signal": _series_view(result["signal"]),
            "histogram": _series_view(result["histogram"]),
        },
    }


def indicator_bollinger(
    symbol: str,
    timeframe: Timeframe = "H1",
    period: int = 20,
    num_std: float = 2.0,
    count: int = DEFAULT_COUNT,
) -> dict:
    df = _frame(symbol, timeframe, count)
    result = bollinger(df["close"], period, num_std)
    last = result.dropna().iloc[-1]
    close = float(df["close"].iloc[-1])
    return {
        "indicator": "BOLLINGER",
        "period": period,
        "num_std": num_std,
        "last": {
            "upper": round(float(last.upper), 6),
            "middle": round(float(last.middle), 6),
            "lower": round(float(last.lower), 6),
            "close": close,
            "position": "upper"
            if close >= last.upper
            else "lower"
            if close <= last.lower
            else "inside",
        },
        "series": {
            "upper": _series_view(result["upper"]),
            "middle": _series_view(result["middle"]),
            "lower": _series_view(result["lower"]),
        },
    }


def indicator_vwap(
    symbol: str,
    timeframe: Timeframe = "H1",
    rolling: int = 100,
    count: int = DEFAULT_COUNT,
) -> dict:
    df = _frame(symbol, timeframe, count)
    values = vwap(df, rolling=rolling)
    return {"indicator": "VWAP", "rolling": rolling, "series": _series_view(values)}


def indicator_volume(
    symbol: str,
    timeframe: Timeframe = "H1",
    period: int = 20,
    count: int = DEFAULT_COUNT,
) -> dict:
    df = _frame(symbol, timeframe, count)
    avg = df["tick_volume"].rolling(int(period), min_periods=1).mean()
    vol = df["tick_volume"]
    last_ratio = float(vol.iloc[-1] / avg.iloc[-1]) if float(avg.iloc[-1]) else 0.0
    return {
        "indicator": "VOLUME",
        "last_volume": int(vol.iloc[-1]),
        "average_volume": float(avg.iloc[-1]),
        "ratio_to_average": round(last_ratio, 2),
        "series": _series_view(vol),
    }


def indicator_swings(
    symbol: str,
    timeframe: Timeframe = "H1",
    left: int = 5,
    right: int = 5,
    count: int = DEFAULT_COUNT,
) -> dict:
    df = _frame(symbol, timeframe, count)
    hi_idx = swing_highs(df, left, right)
    lo_idx = swing_lows(df, left, right)
    return {
        "indicator": "SWINGS",
        "left": left,
        "right": right,
        "swing_highs": [
            {
                "index": int(i),
                "time": df.iloc[i].time.isoformat(),
                "price": float(df.iloc[i].high),
            }
            for i in hi_idx
        ],
        "swing_lows": [
            {
                "index": int(i),
                "time": df.iloc[i].time.isoformat(),
                "price": float(df.iloc[i].low),
            }
            for i in lo_idx
        ],
    }


def indicator_all(
    symbol: str, timeframe: Timeframe = "H1", count: int = DEFAULT_COUNT
) -> dict:
    df = _frame(symbol, timeframe, count)
    close = df["close"]
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "last_close": float(close.iloc[-1]),
        "ema_20": float(ema(close, 20).iloc[-1]),
        "ema_50": float(ema(close, 50).iloc[-1]),
        "ema_200": float(ema(close, 200).iloc[-1]) if len(df) >= 200 else None,
        "sma_50": float(sma(close, 50).iloc[-1]),
        "atr_14": float(atr(df, 14).iloc[-1]),
        "rsi_14": float(rsi(close, 14).iloc[-1]),
        "macd": {k: round(float(v), 6) for k, v in macd(close).iloc[-1].items()},
        "bollinger": {
            "upper": round(float(bollinger(close).iloc[-1].upper), 6),
            "middle": round(float(bollinger(close).iloc[-1].middle), 6),
            "lower": round(float(bollinger(close).iloc[-1].lower), 6),
        },
        "vwap_100": float(vwap(df, rolling=100).iloc[-1]),
    }


def register(mcp) -> None:
    @mcp.tool(name="indicator_ema", description="Exponential moving average series.")
    def tool_ema(
        symbol: str,
        timeframe: Timeframe = "H1",
        period: int = 50,
        count: int = DEFAULT_COUNT,
    ) -> dict:
        return indicator_ema(symbol, timeframe, period, count)

    @mcp.tool(name="indicator_sma", description="Simple moving average series.")
    def tool_sma(
        symbol: str,
        timeframe: Timeframe = "H1",
        period: int = 50,
        count: int = DEFAULT_COUNT,
    ) -> dict:
        return indicator_sma(symbol, timeframe, period, count)

    @mcp.tool(name="indicator_atr", description="Average True Range series.")
    def tool_atr(
        symbol: str,
        timeframe: Timeframe = "H1",
        period: int = 14,
        count: int = DEFAULT_COUNT,
    ) -> dict:
        return indicator_atr(symbol, timeframe, period, count)

    @mcp.tool(
        name="indicator_rsi",
        description="Relative Strength Index with overbought/oversold flags.",
    )
    def tool_rsi(
        symbol: str,
        timeframe: Timeframe = "H1",
        period: int = 14,
        count: int = DEFAULT_COUNT,
    ) -> dict:
        return indicator_rsi(symbol, timeframe, period, count)

    @mcp.tool(
        name="indicator_macd", description="MACD line, signal line and histogram."
    )
    def tool_macd(
        symbol: str,
        timeframe: Timeframe = "H1",
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        count: int = DEFAULT_COUNT,
    ) -> dict:
        return indicator_macd(symbol, timeframe, fast, slow, signal, count)

    @mcp.tool(
        name="indicator_bollinger", description="Bollinger Bands with price position."
    )
    def tool_bollinger(
        symbol: str,
        timeframe: Timeframe = "H1",
        period: int = 20,
        num_std: float = 2.0,
        count: int = DEFAULT_COUNT,
    ) -> dict:
        return indicator_bollinger(symbol, timeframe, period, num_std, count)

    @mcp.tool(
        name="indicator_vwap", description="Volume weighted average price series."
    )
    def tool_vwap(
        symbol: str,
        timeframe: Timeframe = "H1",
        rolling: int = 100,
        count: int = DEFAULT_COUNT,
    ) -> dict:
        return indicator_vwap(symbol, timeframe, rolling, count)

    @mcp.tool(name="indicator_volume", description="Volume vs rolling average.")
    def tool_volume(
        symbol: str,
        timeframe: Timeframe = "H1",
        period: int = 20,
        count: int = DEFAULT_COUNT,
    ) -> dict:
        return indicator_volume(symbol, timeframe, period, count)

    @mcp.tool(
        name="indicator_swings",
        description="Confirmed swing highs and lows (fractal pivots).",
    )
    def tool_swings(
        symbol: str,
        timeframe: Timeframe = "H1",
        left: int = 5,
        right: int = 5,
        count: int = DEFAULT_COUNT,
    ) -> dict:
        return indicator_swings(symbol, timeframe, left, right, count)

    @mcp.tool(
        name="indicator_all",
        description="One-shot snapshot of EMA/SMA/ATR/RSI/MACD/Bollinger/VWAP.",
    )
    def tool_all(
        symbol: str, timeframe: Timeframe = "H1", count: int = DEFAULT_COUNT
    ) -> dict:
        return indicator_all(symbol, timeframe, count)

"""Price action pattern detection.

Detects candle patterns and structural events with confidence scores:

* Bullish/Bearish Engulfing, Pin Bar, Hammer, Shooting Star
* Morning Star, Evening Star, Inside Bar, Outside Bar
* Break of Structure (BOS), Change of Character (CHOCH)
* Liquidity Sweep, Fake Breakout
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from common import PatternResult
from indicators import atr, swing_highs, swing_lows

NEUTRAL = "neutral"


def _candle(frame: pd.DataFrame, index: int) -> pd.Series:
    return frame.iloc[index]


def _body(c: pd.Series) -> float:
    return abs(float(c.close) - float(c.open))


def _bullish(c: pd.Series) -> bool:
    return float(c.close) > float(c.open)


def _range(c: pd.Series) -> float:
    return float(c.high) - float(c.low)


def _upper_wick(c: pd.Series) -> float:
    return float(c.high) - max(float(c.close), float(c.open))


def _lower_wick(c: pd.Series) -> float:
    return min(float(c.close), float(c.open)) - float(c.low)


def _result(
    pattern: str,
    direction: str,
    confidence: float,
    index: int,
    price: float,
    detail: dict,
) -> PatternResult:
    return PatternResult(
        pattern=pattern,
        direction=direction,
        confidence=round(float(np.clip(confidence, 0.0, 1.0)), 3),
        index=index,
        price=float(price),
        detail=detail,
    )


def _engulfing(frame: pd.DataFrame, i: int) -> PatternResult | None:
    cur, prev = _candle(frame, i), _candle(frame, i - 1)
    cur_body, prev_body = _body(cur), _body(prev)
    if prev_body <= 0 or cur_body <= 0:
        return None
    if not (cur_body >= prev_body * 1.1 and cur_body > 0.3 * _range(cur)):
        return None
    if (
        _bullish(cur)
        and not _bullish(prev)
        and float(cur.close) >= float(prev.open)
        and float(cur.open) <= float(prev.close)
    ):
        ratio = min(cur_body / prev_body, 4.0)
        conf = min(0.9, 0.45 + ratio * 0.1)
        return _result(
            "bullish_engulfing",
            "bullish",
            conf,
            i,
            float(cur.close),
            {"body_ratio": round(ratio, 2)},
        )
    if (
        not _bullish(cur)
        and _bullish(prev)
        and float(cur.close) <= float(prev.open)
        and float(cur.open) >= float(prev.close)
    ):
        ratio = min(cur_body / prev_body, 4.0)
        conf = min(0.9, 0.45 + ratio * 0.1)
        return _result(
            "bearish_engulfing",
            "bearish",
            conf,
            i,
            float(cur.close),
            {"body_ratio": round(ratio, 2)},
        )
    return None


def _pin_bar(frame: pd.DataFrame, i: int) -> PatternResult | None:
    cur = _candle(frame, i)
    total = _range(cur)
    body = _body(cur)
    if total <= 0 or body <= 0:
        return None
    upper, lower = _upper_wick(cur), _lower_wick(cur)
    if body > 0.35 * total:
        return None
    wick = max(upper, lower)
    if wick + 1e-12 < 2.0 * body or wick < 0.5 * total:
        return None
    if lower + 1e-12 >= upper * 2.0:
        conf = min(0.9, 0.5 + (lower / body - 2.0) * 0.1)
        return _result(
            "pin_bar",
            "bullish",
            conf,
            i,
            float(cur.close),
            {"wick_ratio": round(lower / max(body, 1e-12), 2)},
        )
    if upper + 1e-12 >= lower * 2.0:
        conf = min(0.9, 0.5 + (upper / body - 2.0) * 0.1)
        return _result(
            "pin_bar",
            "bearish",
            conf,
            i,
            float(cur.close),
            {"wick_ratio": round(upper / max(body, 1e-12), 2)},
        )
    return None


def _hammer(frame: pd.DataFrame, i: int) -> PatternResult | None:
    cur = _candle(frame, i)
    total, body = _range(cur), _body(cur)
    if total <= 0 or body <= 0:
        return None
    lower, upper = _lower_wick(cur), _upper_wick(cur)
    if lower + 1e-12 >= 2.0 * body and upper <= 0.4 * total and _bullish(cur):
        conf = min(0.85, 0.55 + (lower / max(body, 1e-12) - 2.0) * 0.08)
        return _result(
            "hammer",
            "bullish",
            conf,
            i,
            float(cur.close),
            {"wick_ratio": round(lower / max(body, 1e-12), 2)},
        )
    return None


def _shooting_star(frame: pd.DataFrame, i: int) -> PatternResult | None:
    cur = _candle(frame, i)
    total, body = _range(cur), _body(cur)
    if total <= 0 or body <= 0:
        return None
    upper, lower = _upper_wick(cur), _lower_wick(cur)
    if upper + 1e-12 >= 2.0 * body and lower <= 0.4 * total and not _bullish(cur):
        conf = min(0.85, 0.55 + (upper / max(body, 1e-12) - 2.0) * 0.08)
        return _result(
            "shooting_star",
            "bearish",
            conf,
            i,
            float(cur.close),
            {"wick_ratio": round(upper / max(body, 1e-12), 2)},
        )
    return None


def _star(frame: pd.DataFrame, i: int, atr_val: float) -> PatternResult | None:
    if i < 2:
        return None
    c1, c2, c3 = _candle(frame, i - 2), _candle(frame, i - 1), _candle(frame, i)
    b1, b2, b3 = _body(c1), _body(c2), _body(c3)
    if atr_val <= 0 or b2 <= 0:
        return None
    middle_small = b2 <= 0.45 * _range(c2) and b2 <= 0.5 * b1 and b3 >= 0.6 * b1
    if not middle_small:
        return None
    if not _bullish(c1) and _bullish(c3) and float(c3.close) > float(c1.close):
        strength = (b1 + b3) / atr_val
        conf = min(0.85, 0.5 + strength * 0.1)
        return _result(
            "morning_star",
            "bullish",
            conf,
            i,
            float(c3.close),
            {"atr_ratio": round(strength, 2)},
        )
    if _bullish(c1) and not _bullish(c3) and float(c3.close) < float(c1.close):
        strength = (b1 + b3) / atr_val
        conf = min(0.85, 0.5 + strength * 0.1)
        return _result(
            "evening_star",
            "bearish",
            conf,
            i,
            float(c3.close),
            {"atr_ratio": round(strength, 2)},
        )
    return None


def _inside_bar(frame: pd.DataFrame, i: int) -> PatternResult | None:
    cur, prev = _candle(frame, i), _candle(frame, i - 1)
    if float(cur.high) <= float(prev.high) and float(cur.low) >= float(prev.low):
        body_ratio = _body(cur) / max(_body(prev), 1e-12)
        conf = min(0.6, 0.45 + 0.05 * (1.0 - body_ratio))
        direction = (
            "bullish" if _bullish(cur) else "bearish" if not _bullish(cur) else NEUTRAL
        )
        return _result(
            "inside_bar",
            direction,
            conf,
            i,
            float(cur.close),
            {"body_ratio": round(body_ratio, 2)},
        )
    return None


def _outside_bar(frame: pd.DataFrame, i: int) -> PatternResult | None:
    cur, prev = _candle(frame, i), _candle(frame, i - 1)
    if float(cur.high) > float(prev.high) and float(cur.low) < float(prev.low):
        conf = min(0.75, 0.5 + (_range(cur) / max(_range(prev), 1e-12) - 1.0) * 0.15)
        direction = "bullish" if _bullish(cur) else "bearish"
        return _result(
            "outside_bar",
            direction,
            conf,
            i,
            float(cur.close),
            {"range_ratio": round(_range(cur) / max(_range(prev), 1e-12), 2)},
        )
    return None


def _structure_events(
    frame: pd.DataFrame, i: int, highs: list[int], lows: list[int], atr_val: float
) -> list[PatternResult]:
    results: list[PatternResult] = []
    if atr_val <= 0 or i < 1:
        return results
    close = float(frame.iloc[i].close)
    swing_highs_last = [h for h in highs if h < i]
    swing_lows_last = [low_idx for low_idx in lows if low_idx < i]
    level_high = (
        float(frame.iloc[swing_highs_last[-1]].high) if swing_highs_last else None
    )
    level_low = float(frame.iloc[swing_lows_last[-1]].low) if swing_lows_last else None

    if level_high is not None and close > level_high:
        dist = (close - level_high) / atr_val
        conf = min(0.9, 0.55 + dist * 0.12)
        results.append(
            _result(
                "BOS",
                "bullish",
                conf,
                i,
                close,
                {"level": round(level_high, 5), "distance_atr": round(dist, 2)},
            )
        )
    if level_low is not None and close < level_low:
        dist = (level_low - close) / atr_val
        conf = min(0.9, 0.55 + dist * 0.12)
        results.append(
            _result(
                "BOS",
                "bearish",
                conf,
                i,
                close,
                {"level": round(level_low, 5), "distance_atr": round(dist, 2)},
            )
        )

    if level_high is not None and float(frame.iloc[i].high) > level_high > close:
        dist = (float(frame.iloc[i].high) - level_high) / atr_val
        conf = min(0.85, 0.5 + dist * 0.15)
        results.append(
            _result(
                "liquidity_sweep",
                "bearish",
                conf,
                i,
                close,
                {"swept_level": "high", "distance_atr": round(dist, 2)},
            )
        )
    if level_low is not None and float(frame.iloc[i].low) < level_low < close:
        dist = (level_low - float(frame.iloc[i].low)) / atr_val
        conf = min(0.85, 0.5 + dist * 0.15)
        results.append(
            _result(
                "liquidity_sweep",
                "bullish",
                conf,
                i,
                close,
                {"swept_level": "low", "distance_atr": round(dist, 2)},
            )
        )

    if i >= 1 and level_high is not None:
        prev_close = float(frame.iloc[i - 1].close)
        if prev_close > level_high and close < level_high:
            results.append(
                _result(
                    "fake_breakout", "bearish", 0.6, i, close, {"broken_level": "high"}
                )
            )
    if i >= 1 and level_low is not None:
        prev_close = float(frame.iloc[i - 1].close)
        if prev_close < level_low and close > level_low:
            results.append(
                _result(
                    "fake_breakout", "bullish", 0.6, i, close, {"broken_level": "low"}
                )
            )

    choch = _choch(frame, i, highs, lows, atr_val)
    if choch is not None:
        results.append(choch)
    return results


def _choch(
    frame: pd.DataFrame, i: int, highs: list[int], lows: list[int], atr_val: float
) -> PatternResult | None:
    recent_highs = [h for h in highs if h < i]
    recent_lows = [low_idx for low_idx in lows if low_idx < i]
    if len(recent_highs) < 2 or len(recent_lows) < 2 or atr_val <= 0:
        return None
    close = float(frame.iloc[i].close)
    h_last, h_prev = (
        float(frame.iloc[recent_highs[-1]].high),
        float(frame.iloc[recent_highs[-2]].high),
    )
    l_last, l_prev = (
        float(frame.iloc[recent_lows[-1]].low),
        float(frame.iloc[recent_lows[-2]].low),
    )
    in_downtrend = h_last < h_prev and l_last < l_prev
    in_uptrend = h_last > h_prev and l_last > l_prev
    if in_downtrend and close > h_last:
        conf = min(0.85, 0.5 + (close - h_last) / atr_val * 0.15)
        return _result("CHOCH", "bullish", conf, i, close, {"broken": round(h_last, 5)})
    if in_uptrend and close < l_last:
        conf = min(0.85, 0.5 + (l_last - close) / atr_val * 0.15)
        return _result("CHOCH", "bearish", conf, i, close, {"broken": round(l_last, 5)})
    return None


DETECTORS = [
    _engulfing,
    _pin_bar,
    _hammer,
    _shooting_star,
    _inside_bar,
    _outside_bar,
]


def detect_patterns(
    frame: pd.DataFrame,
    lookback: int = 200,
    left: int = 5,
    right: int = 5,
    atr_period: int = 14,
    check_last: int = 5,
) -> list[PatternResult]:
    """Detect all patterns on the most recent candles. Sorted by confidence."""
    if frame is None or len(frame) < left + right + 5:
        return []
    df = frame.tail(int(lookback)).reset_index(drop=True)
    atr_series = atr(df, atr_period)
    atr_now = float(atr_series.iloc[-1]) or 1.0
    highs = swing_highs(df, int(left), int(right))
    lows = swing_lows(df, int(left), int(right))
    results: list[PatternResult] = []
    start = max(2, len(df) - int(check_last))
    for i in range(start, len(df)):
        for detector in DETECTORS:
            result = detector(df, i)
            if result is not None:
                results.append(result)
        star = _star(df, i, atr_now)
        if star is not None:
            results.append(star)
        results.extend(_structure_events(df, i, highs, lows, atr_now))
    results.sort(key=lambda r: -r.confidence)
    return results


def filter_by_direction(
    results: list[PatternResult], direction: str
) -> list[PatternResult]:
    return [r for r in results if r.direction == direction]

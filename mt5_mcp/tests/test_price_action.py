"""Tests for price action pattern detection."""

from __future__ import annotations

from helpers import build_frame

from price_action import detect_patterns, filter_by_direction


def _patterns(closes: list[float], **kwargs):
    frame = build_frame(closes, wick=0.0001)
    return detect_patterns(frame, **kwargs)


def test_bullish_engulfing():
    closes = [1.2] * 40
    closes += [
        1.2050,
        1.2040,
        1.2060,
        1.2030,
        1.2070,
        1.2020,
        1.2080,
        1.2010,
        1.2090,
        1.2000,
    ]
    closes += [1.2025, 1.2015, 1.2045]
    results = _patterns(closes)
    engulfing = [r for r in results if r.pattern == "bullish_engulfing"]
    assert engulfing, f"expected bullish engulfing, got {[r.pattern for r in results]}"
    assert engulfing[0].direction == "bullish"
    assert 0.0 < engulfing[0].confidence <= 1.0


def test_bearish_engulfing():
    closes = [1.2] * 40
    closes += [
        1.1950,
        1.1960,
        1.1940,
        1.1970,
        1.1930,
        1.1980,
        1.1920,
        1.1990,
        1.1910,
        1.2000,
    ]
    closes += [1.1975, 1.1985, 1.1955]
    results = _patterns(closes)
    engulfing = [r for r in results if r.pattern == "bearish_engulfing"]
    assert engulfing, f"expected bearish engulfing, got {[r.pattern for r in results]}"
    assert engulfing[0].direction == "bearish"


def test_hammer_detected():
    closes = [1.2] * 40
    closes += [1.1995, 1.1990, 1.1985, 1.1980, 1.1975, 1.1970, 1.1965, 1.1960, 1.1955]
    closes += [1.1955, 1.1980]  # hammer candle: opens at 1.1955, closes at 1.1980
    frame = build_frame(closes, wick=0.0001)
    frame.loc[frame.index[-1], "low"] = 1.1900  # long lower wick
    results = detect_patterns(frame)
    hammers = [r for r in results if r.pattern == "hammer"]
    assert hammers, f"expected hammer, got {[r.pattern for r in results]}"
    assert hammers[0].direction == "bullish"


def test_shooting_star_detected():
    closes = [1.2] * 40
    closes += [1.2005, 1.2010, 1.2015, 1.2020, 1.2025, 1.2030, 1.2035, 1.2040, 1.2045]
    closes += [1.2025, 1.2050, 1.2025]  # small bullish candle, then shooting star
    frame = build_frame(closes, wick=0.0001)
    frame.loc[frame.index[-1], "high"] = 1.2100  # long upper wick
    results = detect_patterns(frame)
    stars = [r for r in results if r.pattern == "shooting_star"]
    assert stars, f"expected shooting_star, got {[r.pattern for r in results]}"
    assert stars[0].direction == "bearish"


def test_inside_bar():
    closes = [1.2] * 45
    closes += [1.2010, 1.2015, 1.2020, 1.2025]
    closes += [1.2030, 1.2028, 1.2032, 1.2026, 1.2030, 1.2027]
    results = _patterns(closes)
    inside = [r for r in results if r.pattern == "inside_bar"]
    assert inside, f"expected inside_bar, got {[r.pattern for r in results]}"


def test_bos_detected():
    closes = []
    for k in range(10):
        closes.append(1.2 + k * 0.01)
    for k in range(10):
        closes.append(1.29 - k * 0.01)
    for k in range(12):
        closes.append(1.19 + k * 0.01)
    results = _patterns(closes)
    bos = [r for r in results if r.pattern == "BOS"]
    assert bos, f"expected BOS, got {[r.pattern for r in results]}"
    assert bos[0].direction == "bullish"


def test_choch_detected():
    closes = [1.2] * 25
    price = 1.2
    for _ in range(4):
        price += 0.01
        closes.append(price)
        price += 0.01
        closes.append(price)
        price += 0.005
        closes.append(price)
        for _ in range(6):
            price -= 0.006
            closes.append(price)
    for _ in range(8):
        price += 0.008
        closes.append(price)
    results = _patterns(closes)
    choch = [r for r in results if r.pattern == "CHOCH"]
    assert choch, f"expected CHOCH, got {[r.pattern for r in results]}"
    assert choch[0].direction == "bullish"


def test_filter_by_direction():
    closes = [1.2] * 45 + [
        1.1950,
        1.1945,
        1.1955,
        1.1965,
        1.1975,
        1.1985,
        1.1995,
        1.2005,
    ]
    results = _patterns(closes)
    bullish = filter_by_direction(results, "bullish")
    assert all(r.direction == "bullish" for r in bullish)


def test_empty_frame():
    from price_action import detect_patterns

    assert detect_patterns(None) == []

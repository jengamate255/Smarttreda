"""Explainable Autochartist-style chart pattern detection.

The scanner deliberately uses deterministic geometry plus confluence scoring.
It is an AI-style ranking layer, not a claim of a proprietary black-box model:
every score is accompanied by the pattern geometry and market evidence used.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from mt5_mcp.indicators import atr, swing_highs, swing_lows


def _normalise_frame(frame: pd.DataFrame, lookback: int) -> pd.DataFrame:
    required = {"open", "high", "low", "close"}
    if frame is None or not required.issubset(frame.columns):
        return pd.DataFrame()
    result = frame.copy().tail(int(lookback)).reset_index(drop=True)
    if "time" not in result.columns:
        result["time"] = pd.date_range(
            end=datetime.now(timezone.utc), periods=len(result), freq="h"
        )
    result["time"] = pd.to_datetime(result["time"], utc=True)
    if "tick_volume" not in result.columns:
        result["tick_volume"] = result.get("volume", 1.0)
    for column in ("open", "high", "low", "close", "tick_volume"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.dropna(subset=["open", "high", "low", "close"]).reset_index(
        drop=True
    )


def _finite(value: Any, fallback: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return fallback
    return value if np.isfinite(value) else fallback


def _round(value: float | None) -> float | None:
    return None if value is None else round(_finite(value), 8)


def _time(frame: pd.DataFrame, index: int) -> str:
    return pd.Timestamp(frame.iloc[index]["time"]).isoformat()


def _candidate(
    frame: pd.DataFrame,
    pattern: str,
    direction: str,
    status: str,
    quality: float,
    formation_index: int,
    breakout_level: float | None,
    target: float | None,
    invalidation: float | None,
    evidence: list[str],
) -> dict[str, Any]:
    current = _finite(frame.iloc[-1]["close"])
    target_distance = (
        abs(target - current) / max(abs(current - invalidation), 1e-12)
        if target is not None and invalidation is not None
        else 0.0
    )
    return {
        "pattern": pattern,
        "direction": direction,
        "status": status,
        "quality_score": int(round(np.clip(quality, 0.0, 100.0))),
        "confidence": int(round(np.clip(quality, 0.0, 100.0))),
        "formation_index": int(formation_index),
        "bars_ago": int(len(frame) - 1 - formation_index),
        "formed_at": _time(frame, formation_index),
        "breakout_level": _round(breakout_level),
        "target": _round(target),
        "invalidation": _round(invalidation),
        "target_distance_ratio": round(float(target_distance), 2),
        "evidence": evidence,
    }


def _double_patterns(
    frame: pd.DataFrame, highs: list[int], lows: list[int], atr_now: float
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    tolerance = max(0.45 * atr_now, _finite(frame.close.iloc[-1]) * 0.0004)
    if len(highs) >= 2:
        first, second = highs[-2:]
        between = [i for i in lows if first < i < second]
        if second - first >= 5 and between:
            peak_1, peak_2 = _finite(frame.high.iloc[first]), _finite(frame.high.iloc[second])
            neckline = min(_finite(frame.low.iloc[i]) for i in between)
            depth = min(peak_1, peak_2) - neckline
            if abs(peak_1 - peak_2) <= tolerance and depth >= 0.7 * atr_now:
                close = _finite(frame.close.iloc[-1])
                broken = close < neckline - 0.12 * atr_now
                proximity = abs(close - neckline) <= 0.4 * atr_now
                similarity = 1.0 - abs(peak_1 - peak_2) / max(tolerance, 1e-12)
                depth_score = min(depth / max(1.5 * atr_now, 1e-12), 1.0)
                quality = 48 + 20 * similarity + 18 * depth_score + (14 if broken else 5 if proximity else 0)
                results.append(
                    _candidate(
                        frame,
                        "double_top",
                        "bearish",
                        "confirmed" if broken else "emerging",
                        quality,
                        first,
                        neckline,
                        neckline - depth,
                        max(peak_1, peak_2) + 0.25 * atr_now,
                        [
                            f"peak symmetry {similarity:.0%}",
                            f"neckline {neckline:.5f}",
                            f"pattern depth {depth / max(atr_now, 1e-12):.1f} ATR",
                            "neckline broken" if broken else "neckline not broken",
                        ],
                    )
                )
    if len(lows) >= 2:
        first, second = lows[-2:]
        between = [i for i in highs if first < i < second]
        if second - first >= 5 and between:
            low_1, low_2 = _finite(frame.low.iloc[first]), _finite(frame.low.iloc[second])
            neckline = max(_finite(frame.high.iloc[i]) for i in between)
            depth = neckline - max(low_1, low_2)
            if abs(low_1 - low_2) <= tolerance and depth >= 0.7 * atr_now:
                close = _finite(frame.close.iloc[-1])
                broken = close > neckline + 0.12 * atr_now
                proximity = abs(close - neckline) <= 0.4 * atr_now
                similarity = 1.0 - abs(low_1 - low_2) / max(tolerance, 1e-12)
                depth_score = min(depth / max(1.5 * atr_now, 1e-12), 1.0)
                quality = 48 + 20 * similarity + 18 * depth_score + (14 if broken else 5 if proximity else 0)
                results.append(
                    _candidate(
                        frame,
                        "double_bottom",
                        "bullish",
                        "confirmed" if broken else "emerging",
                        quality,
                        first,
                        neckline,
                        neckline + depth,
                        min(low_1, low_2) - 0.25 * atr_now,
                        [
                            f"trough symmetry {similarity:.0%}",
                            f"neckline {neckline:.5f}",
                            f"pattern depth {depth / max(atr_now, 1e-12):.1f} ATR",
                            "neckline broken" if broken else "neckline not broken",
                        ],
                    )
                )
    return results


def _head_shoulders(
    frame: pd.DataFrame, highs: list[int], lows: list[int], atr_now: float
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if len(highs) >= 3:
        h1, h2, h3 = highs[-3:]
        left_lows = [i for i in lows if h1 < i < h2]
        right_lows = [i for i in lows if h2 < i < h3]
        if left_lows and right_lows:
            shoulder_l = _finite(frame.high.iloc[h1])
            head = _finite(frame.high.iloc[h2])
            shoulder_r = _finite(frame.high.iloc[h3])
            n1 = min(_finite(frame.low.iloc[i]) for i in left_lows)
            n2 = min(_finite(frame.low.iloc[i]) for i in right_lows)
            neckline = (n1 + n2) / 2.0
            shoulder_gap = abs(shoulder_l - shoulder_r)
            if head > max(shoulder_l, shoulder_r) + 0.5 * atr_now and shoulder_gap <= 0.9 * atr_now and abs(n1 - n2) <= 1.0 * atr_now:
                close = _finite(frame.close.iloc[-1])
                broken = close < neckline - 0.12 * atr_now
                quality = 55 + min(18, (head - max(shoulder_l, shoulder_r)) / max(atr_now, 1e-12) * 8)
                quality += max(0, 12 - shoulder_gap / max(atr_now, 1e-12) * 10) + (15 if broken else 0)
                results.append(
                    _candidate(
                        frame,
                        "head_and_shoulders",
                        "bearish",
                        "confirmed" if broken else "emerging",
                        quality,
                        h1,
                        neckline,
                        neckline - (head - neckline),
                        head + 0.25 * atr_now,
                        [
                            f"head exceeds shoulders {(head - max(shoulder_l, shoulder_r)) / max(atr_now, 1e-12):.1f} ATR",
                            f"shoulder symmetry {max(0.0, 1.0 - shoulder_gap / max(atr_now, 1e-12)):.0%}",
                            f"neckline {neckline:.5f}",
                            "neckline broken" if broken else "neckline not broken",
                        ],
                    )
                )
    if len(lows) >= 3:
        l1, l2, l3 = lows[-3:]
        left_highs = [i for i in highs if l1 < i < l2]
        right_highs = [i for i in highs if l2 < i < l3]
        if left_highs and right_highs:
            shoulder_l = _finite(frame.low.iloc[l1])
            head = _finite(frame.low.iloc[l2])
            shoulder_r = _finite(frame.low.iloc[l3])
            n1 = max(_finite(frame.high.iloc[i]) for i in left_highs)
            n2 = max(_finite(frame.high.iloc[i]) for i in right_highs)
            neckline = (n1 + n2) / 2.0
            shoulder_gap = abs(shoulder_l - shoulder_r)
            if head < min(shoulder_l, shoulder_r) - 0.5 * atr_now and shoulder_gap <= 0.9 * atr_now and abs(n1 - n2) <= 1.0 * atr_now:
                close = _finite(frame.close.iloc[-1])
                broken = close > neckline + 0.12 * atr_now
                quality = 55 + min(18, (min(shoulder_l, shoulder_r) - head) / max(atr_now, 1e-12) * 8)
                quality += max(0, 12 - shoulder_gap / max(atr_now, 1e-12) * 10) + (15 if broken else 0)
                results.append(
                    _candidate(
                        frame,
                        "inverse_head_and_shoulders",
                        "bullish",
                        "confirmed" if broken else "emerging",
                        quality,
                        l1,
                        neckline,
                        neckline + (neckline - head),
                        head - 0.25 * atr_now,
                        [
                            f"head exceeds shoulders {(min(shoulder_l, shoulder_r) - head) / max(atr_now, 1e-12):.1f} ATR",
                            f"shoulder symmetry {max(0.0, 1.0 - shoulder_gap / max(atr_now, 1e-12)):.0%}",
                            f"neckline {neckline:.5f}",
                            "neckline broken" if broken else "neckline not broken",
                        ],
                    )
                )
    return results


def _triangle(
    frame: pd.DataFrame, highs: list[int], lows: list[int], atr_now: float
) -> list[dict[str, Any]]:
    if len(highs) < 3 or len(lows) < 3:
        return []
    high_points, low_points = highs[-3:], lows[-3:]
    try:
        high_values = np.asarray([_finite(frame.high.iloc[i]) for i in high_points])
        low_values = np.asarray([_finite(frame.low.iloc[i]) for i in low_points])
        high_slope = float(np.polyfit(high_points, high_values, 1)[0]) / max(atr_now, 1e-12)
        low_slope = float(np.polyfit(low_points, low_values, 1)[0]) / max(atr_now, 1e-12)
    except (TypeError, ValueError, np.linalg.LinAlgError):
        return []
    start = min(high_points[0], low_points[0])
    end = len(frame) - 1
    high_line_start = float(np.polyval(np.polyfit(high_points, high_values, 1), start))
    low_line_start = float(np.polyval(np.polyfit(low_points, low_values, 1), start))
    resistance = float(np.polyval(np.polyfit(high_points, high_values, 1), end))
    support = float(np.polyval(np.polyfit(low_points, low_values, 1), end))
    start_gap = high_line_start - low_line_start
    current_gap = resistance - support
    if start_gap <= 0 or current_gap <= 0 or current_gap >= start_gap * 0.85:
        return []

    if abs(high_slope) <= 0.08 and low_slope >= 0.04:
        pattern, expected = "ascending_triangle", "bullish"
    elif high_slope <= -0.04 and abs(low_slope) <= 0.08:
        pattern, expected = "descending_triangle", "bearish"
    elif high_slope <= -0.03 and low_slope >= 0.03:
        pattern, expected = "symmetrical_triangle", "neutral"
    else:
        return []

    close = _finite(frame.close.iloc[-1])
    if close > resistance + 0.12 * atr_now:
        direction, status, level, target, invalidation = "bullish", "confirmed", resistance, close + start_gap, support
    elif close < support - 0.12 * atr_now:
        direction, status, level, target, invalidation = "bearish", "confirmed", support, close - start_gap, resistance
    else:
        direction, status, level = expected, "emerging", resistance if expected != "bearish" else support
        target = resistance + start_gap if expected == "bullish" else support - start_gap if expected == "bearish" else None
        invalidation = support if expected != "bearish" else resistance

    convergence = 1.0 - current_gap / max(start_gap, 1e-12)
    quality = 48 + 25 * convergence + 12 * min(1.0, min(len(high_points), len(low_points)) / 3.0)
    if status == "confirmed":
        quality += 15
    return [
        _candidate(
            frame,
            pattern,
            direction,
            status,
            quality,
            start,
            level,
            target,
            invalidation,
            [
                f"range contraction {convergence:.0%}",
                f"upper slope {high_slope:.2f} ATR/bar",
                f"lower slope {low_slope:.2f} ATR/bar",
                "breakout confirmed" if status == "confirmed" else "awaiting breakout",
            ],
        )
    ]


def _breakout(frame: pd.DataFrame, atr_now: float) -> list[dict[str, Any]]:
    if len(frame) < 25:
        return []
    prior = frame.iloc[-21:-1]
    close = _finite(frame.close.iloc[-1])
    upper, lower = _finite(prior.high.max()), _finite(prior.low.min())
    volume_now = _finite(frame.tick_volume.iloc[-1], 1.0)
    volume_avg = _finite(frame.tick_volume.iloc[-21:-1].mean(), 1.0)
    volume_ratio = volume_now / max(volume_avg, 1e-12)
    height = upper - lower
    if height <= 0:
        return []
    if close > upper + 0.12 * atr_now:
        direction, level, target, invalidation = "bullish", upper, close + height, upper - 0.2 * atr_now
    elif close < lower - 0.12 * atr_now:
        direction, level, target, invalidation = "bearish", lower, close - height, lower + 0.2 * atr_now
    else:
        return []
    impulse = abs(close - level) / max(atr_now, 1e-12)
    quality = 55 + min(20, impulse * 8) + min(20, max(0.0, volume_ratio - 1.0) * 12)
    return [
        _candidate(
            frame,
            "range_breakout",
            direction,
            "confirmed",
            quality,
            len(frame) - 1,
            level,
            target,
            invalidation,
            [
                f"range height {height / max(atr_now, 1e-12):.1f} ATR",
                f"breakout impulse {impulse:.1f} ATR",
                f"volume {volume_ratio:.1f}x average",
            ],
        )
    ]


def _flag_patterns(frame: pd.DataFrame, atr_now: float) -> list[dict[str, Any]]:
    """Find a sharp pole followed by a compact counter-trend channel."""
    if len(frame) < 35:
        return []
    start = max(0, len(frame) - 45)
    pole_end = len(frame) - 16
    pull = frame.iloc[pole_end:]
    pole = _finite(frame.close.iloc[pole_end]) - _finite(frame.close.iloc[start])
    pull_range = _finite(pull.high.max()) - _finite(pull.low.min())
    if abs(pole) < 1.5 * atr_now or pull_range > abs(pole) * 0.9:
        return []
    x = np.arange(len(pull), dtype=float)
    slope = float(np.polyfit(x, pull.close.to_numpy(dtype=float), 1)[0])
    current = _finite(frame.close.iloc[-1])
    upper = _finite(pull.high.max())
    lower = _finite(pull.low.min())
    bull = pole > 0 and slope <= 0.12 * atr_now
    bear = pole < 0 and slope >= -0.12 * atr_now
    if not (bull or bear):
        return []
    direction = "bullish" if bull else "bearish"
    broken = current > upper + 0.12 * atr_now if bull else current < lower - 0.12 * atr_now
    quality = 50 + min(24, abs(pole) / max(atr_now, 1e-12) * 3)
    quality += min(14, max(0.0, 1.0 - pull_range / max(abs(pole), 1e-12)) * 20)
    if broken:
        quality += 12
    return [
        _candidate(
            frame,
            "bull_flag" if bull else "bear_flag",
            direction,
            "confirmed" if broken else "emerging",
            quality,
            start,
            upper if bull else lower,
            (upper + abs(pole)) if bull else (lower - abs(pole)),
            lower - 0.25 * atr_now if bull else upper + 0.25 * atr_now,
            [
                f"pole {abs(pole) / max(atr_now, 1e-12):.1f} ATR",
                f"flag range {pull_range / max(atr_now, 1e-12):.1f} ATR",
                f"pullback slope {slope / max(atr_now, 1e-12):+.2f} ATR/bar",
                "flag breakout confirmed" if broken else "awaiting flag breakout",
            ],
        )
    ]


def _wedge_patterns(frame: pd.DataFrame, atr_now: float) -> list[dict[str, Any]]:
    """Find rising/falling wedges from converging high and low regressions."""
    if len(frame) < 35:
        return []
    segment = frame.iloc[-40:].reset_index(drop=True)
    x = np.arange(len(segment), dtype=float)
    high_fit = np.polyfit(x, segment.high.to_numpy(dtype=float), 1)
    low_fit = np.polyfit(x, segment.low.to_numpy(dtype=float), 1)
    high_slope = float(high_fit[0]) / max(atr_now, 1e-12)
    low_slope = float(low_fit[0]) / max(atr_now, 1e-12)
    start_gap = float(np.polyval(high_fit, 0) - np.polyval(low_fit, 0))
    end_gap = float(np.polyval(high_fit, len(segment) - 1) - np.polyval(low_fit, len(segment) - 1))
    if start_gap <= 0 or end_gap <= 0 or end_gap >= start_gap * 0.85:
        return []
    rising = high_slope > 0.03 and low_slope > 0.03 and low_slope > high_slope
    falling = high_slope < -0.03 and low_slope < -0.03 and high_slope < low_slope
    if not (rising or falling):
        return []
    expected = "bearish" if rising else "bullish"
    current = _finite(frame.close.iloc[-1])
    resistance = float(np.polyval(high_fit, len(segment) - 1))
    support = float(np.polyval(low_fit, len(segment) - 1))
    broken = current < support - 0.12 * atr_now if rising else current > resistance + 0.12 * atr_now
    quality = 50 + min(25, (1.0 - end_gap / max(start_gap, 1e-12)) * 35)
    if broken:
        quality += 14
    return [
        _candidate(
            frame,
            "rising_wedge" if rising else "falling_wedge",
            expected,
            "confirmed" if broken else "emerging",
            quality,
            len(frame) - len(segment),
            support if rising else resistance,
            current - start_gap if rising else current + start_gap,
            resistance + 0.25 * atr_now if rising else support - 0.25 * atr_now,
            [
                f"wedge contraction {(1.0 - end_gap / max(start_gap, 1e-12)):.0%}",
                f"upper slope {high_slope:+.2f} ATR/bar",
                f"lower slope {low_slope:+.2f} ATR/bar",
                "wedge breakout confirmed" if broken else "awaiting wedge breakout",
            ],
        )
    ]


def _channel_patterns(frame: pd.DataFrame, atr_now: float) -> list[dict[str, Any]]:
    """Find a parallel rising or falling price channel."""
    if len(frame) < 30:
        return []
    segment = frame.iloc[-36:].reset_index(drop=True)
    x = np.arange(len(segment), dtype=float)
    high_fit = np.polyfit(x, segment.high.to_numpy(dtype=float), 1)
    low_fit = np.polyfit(x, segment.low.to_numpy(dtype=float), 1)
    high_slope = float(high_fit[0])
    low_slope = float(low_fit[0])
    if abs(high_slope - low_slope) > 0.25 * atr_now or abs(high_slope) < 0.03 * atr_now:
        return []
    direction = "bullish" if (high_slope + low_slope) > 0 else "bearish"
    resistance = float(np.polyval(high_fit, len(segment) - 1))
    support = float(np.polyval(low_fit, len(segment) - 1))
    current = _finite(frame.close.iloc[-1])
    broken = current > resistance + 0.12 * atr_now if direction == "bullish" else current < support - 0.12 * atr_now
    width = max(resistance - support, atr_now)
    quality = 48 + min(25, 15 * (1.0 - abs(high_slope - low_slope) / max(0.25 * atr_now, 1e-12)))
    if broken:
        quality += 14
    return [
        _candidate(
            frame,
            "rising_channel" if direction == "bullish" else "falling_channel",
            direction,
            "confirmed" if broken else "emerging",
            quality,
            len(frame) - len(segment),
            resistance if direction == "bullish" else support,
            current + width if direction == "bullish" else current - width,
            support - 0.25 * atr_now if direction == "bullish" else resistance + 0.25 * atr_now,
            [
                f"channel width {width / max(atr_now, 1e-12):.1f} ATR",
                f"upper slope {high_slope / max(atr_now, 1e-12):+.2f} ATR/bar",
                f"lower slope {low_slope / max(atr_now, 1e-12):+.2f} ATR/bar",
                "channel breakout confirmed" if broken else "channel intact",
            ],
        )
    ]


def _cup_and_handle(frame: pd.DataFrame, atr_now: float) -> list[dict[str, Any]]:
    """Find a bullish cup followed by a shallow handle."""
    if len(frame) < 60:
        return []
    segment = frame.iloc[-60:].reset_index(drop=True)
    rim_left = float(segment.high.iloc[:15].max())
    rim_right = float(segment.high.iloc[35:50].max())
    bowl = float(segment.low.iloc[15:38].min())
    depth = min(rim_left, rim_right) - bowl
    handle = segment.iloc[50:]
    handle_high = float(handle.high.max())
    handle_low = float(handle.low.min())
    current = _finite(segment.close.iloc[-1])
    if depth < 1.5 * atr_now or abs(rim_left - rim_right) > 1.25 * atr_now:
        return []
    if handle_high > min(rim_left, rim_right) + 0.25 * atr_now or handle_low < bowl + depth * 0.45:
        return []
    rim = (rim_left + rim_right) / 2.0
    broken = current > rim + 0.12 * atr_now
    quality = 52 + min(24, depth / max(atr_now, 1e-12) * 4)
    quality += max(0.0, 12.0 - abs(rim_left - rim_right) / max(atr_now, 1e-12) * 8)
    if broken:
        quality += 12
    return [
        _candidate(
            frame,
            "cup_and_handle",
            "bullish",
            "confirmed" if broken else "emerging",
            quality,
            len(frame) - len(segment),
            rim,
            rim + depth,
            handle_low - 0.25 * atr_now,
            [
                f"cup depth {depth / max(atr_now, 1e-12):.1f} ATR",
                f"rim symmetry {max(0.0, 1.0 - abs(rim_left - rim_right) / max(atr_now, 1e-12)):.0%}",
                f"handle range {(handle_high - handle_low) / max(atr_now, 1e-12):.1f} ATR",
                "rim breakout confirmed" if broken else "awaiting rim breakout",
            ],
        )
    ]


def _levels(frame: pd.DataFrame, highs: list[int], lows: list[int], atr_now: float) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    tolerance = max(0.45 * atr_now, _finite(frame.close.iloc[-1]) * 0.00035)
    for kind, indices, column in (
        ("resistance", highs, "high"),
        ("support", lows, "low"),
    ):
        for index in indices:
            price = _finite(frame[column].iloc[index])
            match = next(
                (level for level in levels if level["type"] == kind and abs(level["price"] - price) <= tolerance),
                None,
            )
            if match:
                match["prices"].append(price)
                match["indices"].append(index)
            else:
                levels.append({"type": kind, "price": price, "prices": [price], "indices": [index]})
    current = _finite(frame.close.iloc[-1])
    output = []
    for level in levels:
        price = float(np.mean(level["prices"]))
        touches = len(level["prices"])
        recency = max(0.0, 1.0 - (len(frame) - 1 - max(level["indices"])) / max(len(frame), 1))
        strength = min(100.0, 35.0 + touches * 15.0 + recency * 20.0)
        output.append(
            {
                "type": level["type"],
                "price": round(price, 8),
                "touches": touches,
                "strength": int(round(strength)),
                "distance_atr": round(abs(current - price) / max(atr_now, 1e-12), 2),
                "last_seen": _time(frame, max(level["indices"])),
            }
        )
    return sorted(output, key=lambda item: (item["distance_atr"], -item["strength"]))[:8]


def detect_chart_patterns(frame: pd.DataFrame, lookback: int = 500) -> list[dict[str, Any]]:
    """Return emerging and confirmed chart formations with explainable scores."""
    df = _normalise_frame(frame, lookback)
    if len(df) < 40:
        return []
    atr_series = atr(df, 14)
    atr_now = _finite(atr_series.iloc[-1])
    if atr_now <= 0:
        return []
    highs = swing_highs(df, 3, 3)
    lows = swing_lows(df, 3, 3)
    patterns = []
    patterns.extend(_double_patterns(df, highs, lows, atr_now))
    patterns.extend(_head_shoulders(df, highs, lows, atr_now))
    patterns.extend(_triangle(df, highs, lows, atr_now))
    patterns.extend(_breakout(df, atr_now))
    patterns.extend(_flag_patterns(df, atr_now))
    patterns.extend(_wedge_patterns(df, atr_now))
    patterns.extend(_channel_patterns(df, atr_now))
    patterns.extend(_cup_and_handle(df, atr_now))
    return sorted(patterns, key=lambda item: (-item["quality_score"], item["bars_ago"]))


def analyze_chart(
    frame: pd.DataFrame,
    symbol: str = "",
    timeframe: str = "H1",
    lookback: int = 500,
) -> dict[str, Any]:
    """Build the full Autochartist-style report for one chart."""
    df = _normalise_frame(frame, lookback)
    if len(df) < 40:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "verdict": "unavailable",
            "patterns": [],
            "support_resistance": [],
            "errors": ["not enough candles"],
        }

    atr_series = atr(df, 14)
    atr_now = _finite(atr_series.iloc[-1])
    highs = swing_highs(df, 3, 3)
    lows = swing_lows(df, 3, 3)
    patterns = detect_chart_patterns(df, lookback)
    levels = _levels(df, highs, lows, atr_now)

    from mt5_mcp.market_intel import market_regime
    from mt5_mcp.price_action import detect_patterns
    from mt5_mcp.strategy import analyze_trend
    from mt5_mcp.supply_demand import detect_zones

    trend = analyze_trend(df)
    regime = market_regime(df)
    zones = detect_zones(df, lookback=min(400, len(df)), min_strength=35.0)
    zone_data = [
        {
            "type": zone.type,
            "lower": _round(zone.lower),
            "upper": _round(zone.upper),
            "strength": zone.strength,
            "fresh": zone.fresh,
            "retested": zone.retested,
            "broken": zone.broken,
        }
        for zone in zones[:6]
    ]
    candle_patterns = [
        {
            "pattern": item.pattern,
            "direction": item.direction,
            "confidence": int(round(item.confidence * 100)),
            "price": _round(item.price),
            "detail": item.detail,
        }
        for item in detect_patterns(df, lookback=min(200, len(df)), check_last=8)[:8]
    ]

    trend_direction = trend.direction
    for pattern in patterns:
        score = float(pattern["quality_score"])
        evidence = list(pattern["evidence"])
        direction = pattern["direction"]
        if direction != "neutral" and direction == trend_direction:
            score += 8
            evidence.append(f"aligned with {trend_direction} trend")
        elif direction != "neutral" and trend_direction != "sideways":
            score -= 6
            evidence.append(f"counter-trend against {trend_direction}")
        matching_zone = next(
            (
                zone
                for zone in zones
                if not zone.broken
                and ((direction == "bullish" and zone.type == "demand") or (direction == "bearish" and zone.type == "supply"))
            ),
            None,
        )
        if matching_zone:
            score += 7
            evidence.append(f"{matching_zone.type} zone confluence {matching_zone.strength:.0f}/100")
        pattern["ai_score"] = int(round(np.clip(score, 0.0, 100.0)))
        pattern["evidence"] = evidence

    patterns.sort(key=lambda item: (-item["ai_score"], item["bars_ago"]))
    top = patterns[0] if patterns else None
    if top and top["ai_score"] >= 75 and top["status"] == "confirmed":
        verdict = "high_quality"
    elif top and top["ai_score"] >= 60:
        verdict = "watch"
    else:
        verdict = "no_pattern"

    volume_average = _finite(df.tick_volume.iloc[-21:-1].mean(), 1.0) if len(df) > 21 else 1.0
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "lookback": len(df),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_price": _round(df.close.iloc[-1]),
        "atr": _round(atr_now),
        "verdict": verdict,
        "bias": top["direction"] if top and top["direction"] != "neutral" else trend_direction,
        "top_pattern": top,
        "patterns": patterns,
        "candlestick_patterns": candle_patterns,
        "support_resistance": levels,
        "zones": zone_data,
        "market": {
            "trend": trend.model_dump() if hasattr(trend, "model_dump") else trend.dict(),
            "regime": regime,
            "volume_ratio": round(_finite(df.tick_volume.iloc[-1]) / max(volume_average, 1e-12), 2),
        },
        "explanation": (
            f"{top['pattern']} {top['status']} with AI score {top['ai_score']}/100"
            if top
            else "No qualifying geometric formation detected"
        ),
    }

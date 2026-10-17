"""
Hybrid pattern recognition: classical geometric + ML ranking.
Detects: support/resistance, trendlines, triangles, wedges, double tops/bottoms, head & shoulders.
"""
import numpy as np
import pandas as pd
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


class PatternType(Enum):
    SUPPORT = 'support'
    RESISTANCE = 'resistance'
    TRENDLINE_UP = 'trendline_up'
    TRENDLINE_DOWN = 'trendline_down'
    TRIANGLE_SYMMETRIC = 'triangle_symmetric'
    TRIANGLE_ASCENDING = 'triangle_ascending'
    TRIANGLE_DESCENDING = 'triangle_descending'
    WEDGE_RISING = 'wedge_rising'
    WEDGE_FALLING = 'wedge_falling'
    DOUBLE_TOP = 'double_top'
    DOUBLE_BOTTOM = 'double_bottom'
    HEAD_SHOULDERS = 'head_shoulders'
    INV_HEAD_SHOULDERS = 'inv_head_shoulders'


@dataclass
class Pattern:
    type: PatternType
    confidence: float  # 0-100
    start_bar: int
    end_bar: int
    price_levels: List[float]  # key price levels for drawing
    params: Dict  # pattern-specific parameters
    ml_score: Optional[float] = None  # ML ranking score


def find_swing_points(highs: np.ndarray, lows: np.ndarray, window: int = 5) -> Tuple[List[int], List[int]]:
    """Find swing highs and lows using local extrema detection."""
    swing_highs = []
    swing_lows = []
    n = len(highs)

    for i in range(window, n - window):
        if all(highs[i] >= highs[i-j] for j in range(1, window+1)) and \
           all(highs[i] >= highs[i+j] for j in range(1, window+1)):
            swing_highs.append(i)

        if all(lows[i] <= lows[i-j] for j in range(1, window+1)) and \
           all(lows[i] <= lows[i+j] for j in range(1, window+1)):
            swing_lows.append(i)

    return swing_highs, swing_lows


def detect_support_resistance(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                              swing_highs: List[int], swing_lows: List[int],
                              atr: float, tolerance_pct: float = 0.003) -> List[Pattern]:
    """Detect horizontal support/resistance levels from swing point clusters."""
    patterns = []
    all_levels = []

    for idx in swing_highs:
        all_levels.append(('high', idx, highs[idx]))
    for idx in swing_lows:
        all_levels.append(('low', idx, lows[idx]))

    clusters = []
    used = set()

    for i, (type_i, idx_i, price_i) in enumerate(all_levels):
        if i in used:
            continue

        cluster = [(type_i, idx_i, price_i)]
        tolerance = price_i * tolerance_pct

        for j, (type_j, idx_j, price_j) in enumerate(all_levels):
            if j != i and j not in used and abs(price_j - price_i) <= tolerance:
                cluster.append((type_j, idx_j, price_j))
                used.add(j)

        if len(cluster) >= 2:
            used.add(i)
            avg_price = np.mean([p for _, _, p in cluster])
            touches = len(cluster)
            clusters.append((avg_price, touches, cluster))

    for avg_price, touches, cluster in sorted(clusters, key=lambda x: -x[1])[:5]:
        min_bar = min(idx for _, idx, _ in cluster)
        max_bar = max(idx for _, idx, _ in cluster)

        is_resistance = avg_price > closes[-1]
        pattern_type = PatternType.RESISTANCE if is_resistance else PatternType.SUPPORT

        patterns.append(Pattern(
            type=pattern_type,
            confidence=min(95, 50 + touches * 15),
            start_bar=min_bar,
            end_bar=max_bar,
            price_levels=[avg_price],
            params={'touches': touches, 'cluster': cluster}
        ))

    return patterns


def fit_trendline(bars: List[int], prices: List[float]) -> Optional[Tuple[float, float]]:
    """Fit a line through points, return (slope, intercept)."""
    if len(bars) < 2:
        return None

    x = np.array(bars, dtype=float)
    y = np.array(prices, dtype=float)

    n = len(x)
    x_mean = np.mean(x)
    y_mean = np.mean(y)

    numerator = np.sum((x - x_mean) * (y - y_mean))
    denominator = np.sum((x - x_mean) ** 2)

    if denominator == 0:
        return None

    slope = numerator / denominator
    intercept = y_mean - slope * x_mean

    residuals = np.abs(y - (slope * x + intercept))
    if np.std(y) > 0:
        fit_quality = 1 - np.mean(residuals) / np.std(y)
        if fit_quality < 0.5:
            return None

    return slope, intercept


def detect_trendlines(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                      swing_highs: List[int], swing_lows: List[int]) -> List[Pattern]:
    """Detect ascending/descending trendlines."""
    patterns = []

    if len(swing_lows) >= 2:
        for i in range(len(swing_lows) - 1):
            for j in range(i + 1, min(i + 4, len(swing_lows))):
                bars = [swing_lows[i], swing_lows[j]]
                prices = [lows[bars[0]], lows[bars[1]]]
                result = fit_trendline(bars, prices)

                if result:
                    slope, intercept = result
                    if slope > 0:
                        valid_touches = 0
                        for k in range(bars[1], len(closes)):
                            expected = slope * k + intercept
                            if abs(lows[k] - expected) < (expected * 0.005):
                                valid_touches += 1

                        if valid_touches >= 1:
                            patterns.append(Pattern(
                                type=PatternType.TRENDLINE_UP,
                                confidence=min(90, 60 + valid_touches * 10),
                                start_bar=bars[0],
                                end_bar=len(closes) - 1,
                                price_levels=[intercept, slope * (len(closes) - 1) + intercept],
                                params={'slope': slope, 'intercept': intercept, 'touches': valid_touches + 2}
                            ))

    if len(swing_highs) >= 2:
        for i in range(len(swing_highs) - 1):
            for j in range(i + 1, min(i + 4, len(swing_highs))):
                bars = [swing_highs[i], swing_highs[j]]
                prices = [highs[bars[0]], highs[bars[1]]]
                result = fit_trendline(bars, prices)

                if result:
                    slope, intercept = result
                    if slope < 0:
                        valid_touches = 0
                        for k in range(bars[1], len(closes)):
                            expected = slope * k + intercept
                            if abs(highs[k] - expected) < (expected * 0.005):
                                valid_touches += 1

                        if valid_touches >= 1:
                            patterns.append(Pattern(
                                type=PatternType.TRENDLINE_DOWN,
                                confidence=min(90, 60 + valid_touches * 10),
                                start_bar=bars[0],
                                end_bar=len(closes) - 1,
                                price_levels=[intercept, slope * (len(closes) - 1) + intercept],
                                params={'slope': slope, 'intercept': intercept, 'touches': valid_touches + 2}
                            ))

    return patterns[-6:]


def detect_triangles(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                     swing_highs: List[int], swing_lows: List[int]) -> List[Pattern]:
    """Detect triangle patterns (symmetric, ascending, descending)."""
    patterns = []

    up_lines = detect_trendlines(highs, lows, closes, swing_highs, swing_lows)
    down_lines = [p for p in up_lines if p.type == PatternType.TRENDLINE_DOWN]
    up_lines = [p for p in up_lines if p.type == PatternType.TRENDLINE_UP]

    for up_line in up_lines:
        for down_line in down_lines:
            if abs(up_line.start_bar - down_line.start_bar) > 10:
                continue

            up_slope = up_line.params['slope']
            down_slope = down_line.params['slope']

            if up_slope > 0 and down_slope < 0:
                convergence = abs(up_slope - down_slope)
                if convergence > 0:
                    start_bar = min(up_line.start_bar, down_line.start_bar)
                    end_bar = max(up_line.end_bar, down_line.end_bar)

                    if abs(up_slope) < abs(down_slope) * 0.5:
                        pattern_type = PatternType.TRIANGLE_ASCENDING
                    elif abs(down_slope) < abs(up_slope) * 0.5:
                        pattern_type = PatternType.TRIANGLE_DESCENDING
                    else:
                        pattern_type = PatternType.TRIANGLE_SYMMETRIC

                    patterns.append(Pattern(
                        type=pattern_type,
                        confidence=min(85, 55 + convergence * 100),
                        start_bar=start_bar,
                        end_bar=end_bar,
                        price_levels=up_line.price_levels + down_line.price_levels,
                        params={
                            'upper_slope': down_slope,
                            'lower_slope': up_slope,
                            'convergence': convergence
                        }
                    ))

    return patterns[:3]


def detect_double_patterns(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                           swing_highs: List[int], swing_lows: List[int],
                           atr: float) -> List[Pattern]:
    """Detect double tops and double bottoms."""
    patterns = []

    for i in range(len(swing_highs) - 1):
        for j in range(i + 1, min(i + 6, len(swing_highs))):
            idx1, idx2 = swing_highs[i], swing_highs[j]
            price1, price2 = highs[idx1], highs[idx2]

            if abs(price1 - price2) < atr * 0.5:
                min_between = min(lows[idx1:idx2+1]) if idx2 > idx1 else price1
                depth = price1 - min_between

                if depth > atr * 0.8:
                    patterns.append(Pattern(
                        type=PatternType.DOUBLE_TOP,
                        confidence=min(85, 60 + (1 - abs(price1-price2)/atr) * 30),
                        start_bar=idx1,
                        end_bar=idx2,
                        price_levels=[price1, price2, min_between],
                        params={'neckline': min_between, 'depth': depth}
                    ))

    for i in range(len(swing_lows) - 1):
        for j in range(i + 1, min(i + 6, len(swing_lows))):
            idx1, idx2 = swing_lows[i], swing_lows[j]
            price1, price2 = lows[idx1], lows[idx2]

            if abs(price1 - price2) < atr * 0.5:
                max_between = max(highs[idx1:idx2+1]) if idx2 > idx1 else price1
                height = max_between - price1

                if height > atr * 0.8:
                    patterns.append(Pattern(
                        type=PatternType.DOUBLE_BOTTOM,
                        confidence=min(85, 60 + (1 - abs(price1-price2)/atr) * 30),
                        start_bar=idx1,
                        end_bar=idx2,
                        price_levels=[price1, price2, max_between],
                        params={'neckline': max_between, 'height': height}
                    ))

    return patterns[:4]


def detect_head_shoulders(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                          swing_highs: List[int], swing_lows: List[int],
                          atr: float) -> List[Pattern]:
    """Detect head and shoulders (regular and inverse)."""
    patterns = []

    for i in range(len(swing_highs) - 2):
        if i + 2 >= len(swing_highs):
            break

        idx1, idx2, idx3 = swing_highs[i], swing_highs[i+1], swing_highs[i+2]
        p1, p2, p3 = highs[idx1], highs[idx2], highs[idx3]

        if p2 > p1 and p2 > p3 and abs(p1 - p3) < atr * 0.6:
            if idx3 - idx1 < 50:
                shoulders_avg = (p1 + p3) / 2
                head_height = p2 - shoulders_avg

                if head_height > atr * 0.5:
                    valleys = []
                    for j in range(idx1, idx3):
                        if j in swing_lows:
                            valleys.append((j, lows[j]))

                    if len(valleys) >= 2:
                        neckline = np.mean([p for _, p in valleys[:2]])

                        patterns.append(Pattern(
                            type=PatternType.HEAD_SHOULDERS,
                            confidence=min(90, 65 + head_height/atr * 20),
                            start_bar=idx1,
                            end_bar=idx3,
                            price_levels=[p1, p2, p3, neckline],
                            params={'neckline': neckline, 'head_height': head_height}
                        ))

    for i in range(len(swing_lows) - 2):
        if i + 2 >= len(swing_lows):
            break

        idx1, idx2, idx3 = swing_lows[i], swing_lows[i+1], swing_lows[i+2]
        p1, p2, p3 = lows[idx1], lows[idx2], lows[idx3]

        if p2 < p1 and p2 < p3 and abs(p1 - p3) < atr * 0.6:
            if idx3 - idx1 < 50:
                shoulders_avg = (p1 + p3) / 2
                head_depth = shoulders_avg - p2

                if head_depth > atr * 0.5:
                    peaks = []
                    for j in range(idx1, idx3):
                        if j in swing_highs:
                            peaks.append((j, highs[j]))

                    if len(peaks) >= 2:
                        neckline = np.mean([p for _, p in peaks[:2]])

                        patterns.append(Pattern(
                            type=PatternType.INV_HEAD_SHOULDERS,
                            confidence=min(90, 65 + head_depth/atr * 20),
                            start_bar=idx1,
                            end_bar=idx3,
                            price_levels=[p1, p2, p3, neckline],
                            params={'neckline': neckline, 'head_depth': head_depth}
                        ))

    return patterns[:2]


def rank_pattern_ml(pattern: Pattern, atr: float, volume_ratio: float = 1.0) -> float:
    """ML-inspired pattern quality ranking (0-100)."""
    score = pattern.confidence

    if pattern.type in [PatternType.TRIANGLE_SYMMETRIC, PatternType.TRIANGLE_ASCENDING,
                        PatternType.TRIANGLE_DESCENDING]:
        convergence = pattern.params.get('convergence', 0)
        score += min(15, convergence * 200)

    if pattern.type in [PatternType.DOUBLE_TOP, PatternType.DOUBLE_BOTTOM]:
        symmetry = 1 - abs(pattern.price_levels[0] - pattern.price_levels[1]) / atr
        score += symmetry * 10

    if pattern.type in [PatternType.HEAD_SHOULDERS, PatternType.INV_HEAD_SHOULDERS]:
        symmetry = pattern.params.get('head_height', 0) / atr
        score += min(15, symmetry * 20)

    score += min(10, (volume_ratio - 1) * 20)

    return min(100, max(0, score))


def detect_all_patterns(df: pd.DataFrame, lookback: int = 150) -> List[Pattern]:
    """Run all pattern detectors and return ranked list."""
    df = df.tail(lookback).reset_index(drop=True)
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    opens = df['open'].values
    n = len(df)

    atr_period = min(14, n - 1)
    if atr_period >= 2:
        tr = np.maximum(highs[1:] - lows[1:],
                       np.maximum(abs(highs[1:] - closes[:-1]),
                                 abs(lows[1:] - closes[:-1])))
        tr = np.insert(tr, 0, highs[0] - lows[0])
        atr = pd.Series(tr).ewm(span=atr_period, adjust=False).mean().values
    else:
        atr = np.ones(n)

    current_atr = float(atr[-1]) if atr[-1] > 0 else 1.0

    swing_highs, swing_lows = find_swing_points(highs, lows, window=5)

    all_patterns = []
    all_patterns.extend(detect_support_resistance(highs, lows, closes, swing_highs, swing_lows, current_atr))
    all_patterns.extend(detect_triangles(highs, lows, closes, swing_highs, swing_lows))
    all_patterns.extend(detect_double_patterns(highs, lows, closes, swing_highs, swing_lows, current_atr))
    all_patterns.extend(detect_head_shoulders(highs, lows, closes, swing_highs, swing_lows, current_atr))

    for pattern in all_patterns:
        pattern.ml_score = rank_pattern_ml(pattern, current_atr)

    all_patterns.sort(key=lambda p: -p.ml_score)

    return all_patterns[:10]

"""Tests for supply/demand zone detection and scoring."""

from __future__ import annotations

import pandas as pd
from helpers import downtrend_then_base_drop, uptrend_then_base_rally

from supply_demand import detect_zones, nearest_zone


def test_demand_zone_detected_in_base_rally():
    frame = uptrend_then_base_rally()
    zones = detect_zones(frame, lookback=400)
    demands = [z for z in zones if z.type == "demand"]
    assert demands, "expected at least one demand zone"
    best = max(demands, key=lambda z: z.strength)
    assert 0.0 < best.strength <= 100.0
    assert best.upper > best.lower
    assert best.confidence == best.strength
    assert best.formation_time is not None


def test_supply_zone_detected_in_base_drop():
    frame = downtrend_then_base_drop()
    zones = detect_zones(frame, lookback=400)
    supplies = [z for z in zones if z.type == "supply"]
    assert supplies, "expected at least one supply zone"
    best = max(supplies, key=lambda z: z.strength)
    assert best.upper > best.lower


def test_zone_strength_fields_are_consistent():
    frame = uptrend_then_base_rally(seed=3)
    zones = detect_zones(frame, lookback=400)
    for zone in zones:
        assert 1.0 <= zone.strength <= 100.0
        assert zone.touches >= 0
        assert zone.age_bars >= 0
        if zone.broken:
            assert zone.strength <= 40.0


def test_nearest_zone_picks_closest():
    frame = uptrend_then_base_rally()
    zones = detect_zones(frame, lookback=400)
    current = float(frame["close"].iloc[-1])
    nearest = nearest_zone(zones, current)
    assert nearest is not None
    for zone in zones:
        near_dist = min(abs(current - zone.lower), abs(current - zone.upper))
        best_dist = min(abs(current - nearest.lower), abs(current - nearest.upper))
        assert near_dist >= best_dist - 1e-9


def test_empty_frame_returns_empty():
    assert detect_zones(pd.DataFrame(), lookback=400) == []

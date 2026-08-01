"""Tests for the Monte Carlo month simulator."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from monte_carlo import (
    build_day_pool,
    load_trades,
    simulate_month,
)


def _weekdays_in_july_2026() -> list[int]:
    days = []
    day = date(2026, 7, 1)
    while day.month == 7:
        if day.weekday() < 5:
            days.append(day.day)
        day = day + timedelta(days=1)
    return days


def _trade(
    entry_time: str,
    pnl: float,
    risk_amount: float = 50.0,
    symbol: str = "USDJPY",
    rr: float = 3.0,
) -> dict:
    return {
        "symbol": symbol,
        "entry_time": entry_time,
        "pnl": pnl,
        "risk_amount": risk_amount,
        "rr": rr,
    }


def test_r_multiple_from_risk_amount(tmp_path: Path):
    path = tmp_path / "x.json"
    path.write_text(
        '{"params": {"symbol": "USDJPY"}, "trades": ['
        '{"entry_time": "2026-07-03T11:00", "pnl": 150.0, "risk_amount": 50.0}]}',
        encoding="utf-8",
    )
    trades = load_trades(tmp_path)
    assert trades[0]["r"] == 3.0


def test_r_multiple_fallback(tmp_path: Path):
    path = tmp_path / "x.json"
    path.write_text(
        '{"params": {"symbol": "USDJPY"}, "trades": ['
        '{"entry_time": "2026-07-03T11:00", "pnl": 9.0, "rr": 3.0},'
        '{"entry_time": "2026-07-04T11:00", "pnl": -30.0, "rr": 3.0}]}',
        encoding="utf-8",
    )
    trades = load_trades(tmp_path)
    assert trades[0]["r"] == 3.0
    assert trades[1]["r"] == -1.0


def test_since_filter(tmp_path: Path):
    path = tmp_path / "x.json"
    path.write_text(
        '{"params": {"symbol": "USDJPY"}, "trades": ['
        '{"entry_time": "2026-06-18T00:00", "pnl": 100.0, "risk_amount": 50.0},'
        '{"entry_time": "2026-07-14T16:00", "pnl": 150.0, "risk_amount": 50.0}]}',
        encoding="utf-8",
    )
    trades = load_trades(tmp_path, since="2026-07-01")
    assert len(trades) == 1
    assert trades[0]["entry_time"].startswith("2026-07")


def test_day_pool_groups_and_caps_month_length():
    trades = [
        _trade("2026-07-03T11:00", 150.0),
        _trade("2026-07-03T15:00", -50.0),
        _trade("2026-07-14T16:00", 150.0),
        _trade("2026-07-30T02:00", -50.0),
    ]
    pool, weekdays = build_day_pool(trades)
    assert len(pool) == 20  # weekdays Jul 3-30
    assert sum(len(day) for day in pool) == 4
    assert weekdays == 20


def test_deterministic_with_seed():
    trades = [_trade(f"2026-07-{d:02d}T10:00", 150.0) for d in (2, 3, 6)]
    pool, weekdays = build_day_pool(trades)
    a = simulate_month(pool, weekdays, sims=200, seed=7)
    b = simulate_month(pool, weekdays, sims=200, seed=7)
    assert a == b


def test_all_win_pool_always_positive():
    trades = [_trade(f"2026-07-{d:02d}T10:00", 150.0) for d in (2, 3, 6, 8, 10)]
    pool, weekdays = build_day_pool(trades)
    stats = simulate_month(pool, weekdays, sims=500, seed=1)
    assert stats["prob_positive_month"] == 1.0
    assert stats["prob_blown_account"] == 0.0


def test_blown_account_detected():
    trades = [
        _trade(f"2026-07-{d:02d}T10:00", -50.0) for d in _weekdays_in_july_2026()
    ]
    pool, weekdays = build_day_pool(trades)
    stats = simulate_month(pool, weekdays, sims=500, seed=1)
    assert stats["prob_blown_account"] > 0.5


def test_daily_limit_hit_detected():
    trades = []
    for day in (2, 3, 6, 7, 8, 9, 10, 13):
        for hour in range(10, 24, 2):
            trades.append(_trade(f"2026-07-{day:02d}T{hour:02d}:00", -50.0))
    pool, weekdays = build_day_pool(trades)
    stats = simulate_month(pool, weekdays, sims=500, seed=1)
    assert stats["prob_daily_limit_hit"] > 0.5

"""Tests for the bar-by-bar backtest engine."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import Backtester, BacktestParams, default_variants
from strategy import DEMAND, SUPPLY
from supply_demand import Zone


def _info() -> dict:
    return {
        "symbol": "EURUSD",
        "point": 1e-05,
        "digits": 5,
        "volume_min": 0.01,
        "volume_step": 0.01,
        "volume_max": 100.0,
        "trade_contract_size": 100000,
        "tick_value": 1.0,
        "tick_size": 1e-05,
    }


def _frame(n: int, start: str = "2026-01-01") -> pd.DataFrame:
    times = pd.date_range(start=start, periods=n, freq="1h")
    return pd.DataFrame(
        {
            "time": times,
            "open": np.linspace(1.2, 1.2, n),
            "high": np.linspace(1.2, 1.2, n),
            "low": np.linspace(1.2, 1.2, n),
            "close": np.linspace(1.2, 1.2, n),
            "tick_volume": np.ones(n, dtype=int),
            "spread": np.zeros(n, dtype=int),
            "real_volume": np.zeros(n, dtype=int),
        }
    )


def _bullish_frame(n: int, start: str = "2026-01-01") -> pd.DataFrame:
    """Rising price series (EMA50 > EMA200 eventually) with an early base."""
    closes = np.linspace(1.0, 1.3, n)
    df = _frame(n, start)
    df["close"] = closes
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    df["open"] = opens
    df["high"] = np.maximum(opens, closes) + 0.001
    df["low"] = np.minimum(opens, closes) - 0.001
    return df


def _frame_for_tfs(
    base: pd.DataFrame, freq: str
) -> pd.DataFrame:
    out = base.set_index("time").resample(freq).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )
    out = out.dropna().reset_index()
    out["tick_volume"] = 1
    out["spread"] = 0
    out["real_volume"] = 0
    return out


class TestBacktester:
    def test_empty_data_returns_error_result(self) -> None:
        params = BacktestParams(symbol="EURUSD", bars=200)
        bt = Backtester(params, {}, _info())
        result = bt.run()
        assert result.n_trades == 0
        assert result.errors

    def test_too_short_frames_no_crash(self) -> None:
        df = _frame(300)
        frames = {"H1": df, "H4": _frame_for_tfs(df, "4h")}
        params = BacktestParams(symbol="EURUSD", bars=200)
        bt = Backtester(params, frames, _info())
        result = bt.run()
        assert result.n_trades == 0
        assert result.errors == []

    def test_frames_are_never_lookahead(self) -> None:
        params = BacktestParams(symbol="EURUSD", bars=500)
        df = _bullish_frame(1200)
        frames = {
            "H1": df,
            "H4": _frame_for_tfs(df, "4h"),
        }
        bt = Backtester(params, frames, _info())
        base_times = df["time"].values.astype("datetime64[s]")
        for i in (300, 600, 900):
            for tf, frame in frames.items():
                sl = bt._frame_slice(tf, base_times[i])
                assert len(sl) > 0
                assert sl["time"].iloc[-1] <= base_times[i]
                assert frame["time"].iloc[-1] > base_times[i]

    def test_forced_sl_pnl_sign(self) -> None:
        """A trade that exits at SL must have negative PnL for a BUY."""
        params = BacktestParams(symbol="EURUSD", bars=300, spread_points=1.0)
        df = _bullish_frame(1200)
        frames = {
            "H1": df,
            "H4": _frame_for_tfs(df, "4h"),
        }
        bt = Backtester(params, frames, _info())
        pnl = bt._pnl("BUY", entry=1.25, exit_price=1.24, lots=0.1)
        assert pnl < 0
        pnl_sell = bt._pnl("SELL", entry=1.25, exit_price=1.26, lots=0.1)
        assert pnl_sell < 0

    def test_forced_timeout_exit_uses_close(self) -> None:
        df = _bullish_frame(1200)
        params = BacktestParams(symbol="EURUSD", bars=200, timeout_bars=5)
        frames = {"H1": df, "H4": _frame_for_tfs(df, "4h")}
        bt = Backtester(params, frames, _info())
        base_times = df["time"].values.astype("datetime64[s]")
        price, (kind, _ts) = bt._simulate_exit(df, base_times, 100, "BUY", 1.1, 0.5, 5.0)
        assert kind == "timeout"
        assert price == float(df.iloc[104].close)


class TestVariants:
    def test_default_variants_grid(self) -> None:
        variants = default_variants(["EURUSD"])
        names = {
            (v.trend_timeframe, tuple(v.zone_timeframes), v.min_zone_score)
            for v in variants
        }
        assert len(names) == 12
        assert ("H4", ("H1", "H4"), 70.0) in names
        assert ("H4", ("H4", "D1"), 40.0) in names
        assert ("H1", ("D1",), 70.0) in names

    def test_result_determinism(self) -> None:
        df = _bullish_frame(900)
        frames = {"H1": df, "H4": _frame_for_tfs(df, "4h")}
        params = BacktestParams(symbol="EURUSD", bars=300)
        r1 = Backtester(params, frames, _info()).run()
        r2 = Backtester(params, frames, _info()).run()
        assert r1.n_trades == r2.n_trades
        assert r1.net_pnl == r2.net_pnl

    def test_zone_type_filtering(self) -> None:
        """A demand zone must be selected for a BUY, supply for a SELL."""
        demand = Zone(
            type=DEMAND,
            lower=1.10,
            upper=1.11,
            strength=90.0,
            touches=3,
            broken=False,
        )
        supply = Zone(
            type=SUPPLY,
            lower=1.12,
            upper=1.13,
            strength=90.0,
            touches=3,
            broken=False,
        )
        from backtest import _trade_score_formulas

        bt = Backtester(BacktestParams(symbol="EURUSD"), {}, _info())
        chosen, _prox = bt._select_best_zone([demand, supply], DEMAND, 1.105, 0.002)
        assert chosen is demand
        chosen, _prox = bt._select_best_zone([demand, supply], SUPPLY, 1.125, 0.002)
        assert chosen is supply

        score, confidence = _trade_score_formulas(0.8, 90.0, 0.8, 3.0, 3.0)
        assert 70.0 < score < 100.0
        assert confidence > 60.0

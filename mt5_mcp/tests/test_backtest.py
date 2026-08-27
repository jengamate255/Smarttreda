"""Tests for the bar-by-bar backtest engine."""

from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd

from backtest import (
    Backtester,
    BacktestParams,
    _cache_write,
    _run_variant_job,
    default_variants,
)
from indicators import atr
from price_action import detect_patterns
from strategy import DEMAND, SUPPLY
from supply_demand import Zone, detect_zones


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
        params = BacktestParams(symbol="EURUSD", bars=200, timeout_bars=5, use_adaptive_timeout=False)
        frames = {"H1": df, "H4": _frame_for_tfs(df, "4h")}
        bt = Backtester(params, frames, _info())
        base_times = df["time"].values.astype("datetime64[s]")
        price, kind, _ts, _bars, _partial, _trail = bt._simulate_exit(df, base_times, 100, "BUY", 1.1, 0.5, 5.0, zone_atr=0.01)
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


class TestDefaultVariants:
    def test_carries_bars(self) -> None:
        variants = default_variants(["EURUSD"], bars=1234)
        assert len(variants) == 12
        assert all(v.bars == 1234 for v in variants)

    def test_carries_default_bars(self) -> None:
        variants = default_variants(["EURUSD"])
        assert all(v.bars == 5000 for v in variants)


class TestPrecomputedInputsExact:
    """Passing precomputed ATR must produce byte-identical results."""

    def _frame(self, n: int = 300) -> pd.DataFrame:
        rng = np.random.default_rng(3)
        close = 1.1 + np.cumsum(rng.normal(0, 0.004, n))
        df = _frame(n)
        df = df.copy()
        df["close"] = close
        opens = np.roll(close, 1)
        opens[0] = close[0]
        df["open"] = opens
        df["high"] = np.maximum(df["open"], close) + 0.002
        df["low"] = np.minimum(df["open"], close) - 0.002
        return df

    def test_detect_zones_with_atr_series(self) -> None:
        df = self._frame()
        expected = detect_zones(df, lookback=200, min_strength=40.0)
        got = detect_zones(
            df,
            lookback=200,
            min_strength=40.0,
            atr_series=atr(df.tail(200).reset_index(drop=True)),
        )
        assert [asdict(z) for z in expected] == [asdict(z) for z in got]

    def test_detect_patterns_with_atr_now(self) -> None:
        df = self._frame()
        expected = detect_patterns(df, check_last=1)
        atr_now = float(atr(df.tail(200).reset_index(drop=True)).iloc[-1]) or 1.0
        got = detect_patterns(df, check_last=1, atr_now=atr_now)
        assert [asdict(p) for p in expected] == [asdict(p) for p in got]


class TestFrameCache:
    def test_roundtrip_identical(self, tmp_path, monkeypatch) -> None:
        import backtest as bt

        monkeypatch.setattr(bt, "FRAME_CACHE_DIR", tmp_path)
        df = _bullish_frame(500)
        path, meta_path = bt._cache_paths("EURUSD", "H1", 500)
        _cache_write(path, meta_path, df)
        frames = bt.load_frames(
            "EURUSD", ["H1"], 500, use_cache=True, allow_mt5=False, ttl=3600
        )
        assert list(frames.keys()) == ["H1"]
        pd.testing.assert_frame_equal(frames["H1"], df)

    def test_missing_cache_returns_empty_without_mt5(self, tmp_path, monkeypatch) -> None:
        import backtest as bt

        monkeypatch.setattr(bt, "FRAME_CACHE_DIR", tmp_path)
        frames = bt.load_frames(
            "EURUSD", ["H1", "H4"], 500, use_cache=True, allow_mt5=False, ttl=3600
        )
        assert frames == {}

    def test_stale_cache_reused_without_mt5(self, tmp_path, monkeypatch) -> None:
        import backtest as bt

        monkeypatch.setattr(bt, "FRAME_CACHE_DIR", tmp_path)
        df = _bullish_frame(500)
        path, meta_path = bt._cache_paths("EURUSD", "H1", 500)
        _cache_write(path, meta_path, df)
        frames = bt.load_frames(
            "EURUSD", ["H1"], 500, use_cache=True, allow_mt5=False, ttl=0.0
        )
        # stale cache is reused (with a warning) when MT5 is unavailable
        assert list(frames.keys()) == ["H1"]
        pd.testing.assert_frame_equal(frames["H1"], df)


class TestVariantJob:
    def test_runs_from_cache(self, tmp_path, monkeypatch) -> None:
        import backtest as bt

        monkeypatch.setattr(bt, "FRAME_CACHE_DIR", tmp_path)
        df = _bullish_frame(1200)
        frames = {"H1": df, "H4": _frame_for_tfs(df, "4h")}
        for tf, frame in frames.items():
            path, meta_path = bt._cache_paths("EURUSD", tf, 500)
            _cache_write(path, meta_path, frame)
        p = BacktestParams(symbol="EURUSD", bars=500)
        job = {
            "params": asdict(p),
            "info": _info(),
            "out_dir": str(tmp_path / "results"),
            "use_cache": True,
            "ttl": 3600,
        }
        done = _run_variant_job(job)
        assert done["result"].errors == []
        assert (tmp_path / "results" / f"{done['name']}.json").exists()

    def test_empty_frames_report_error(self, tmp_path, monkeypatch) -> None:
        import backtest as bt

        monkeypatch.setattr(bt, "FRAME_CACHE_DIR", tmp_path)
        p = BacktestParams(symbol="EURUSD", bars=500)
        job = {
            "params": asdict(p),
            "info": _info(),
            "out_dir": str(tmp_path / "results"),
            "use_cache": True,
            "ttl": 3600,
        }
        done = _run_variant_job(job)
        assert done["result"].errors

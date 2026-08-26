"""Storage, cleaner, collector, and integrity tests."""
from __future__ import annotations

import sys

import pandas as pd
import pytest

from src.config import Config
from src.data import storage
from src.data.cleaner import clean, detect_gaps
from src.data.mt5_collector import RATE_COLUMNS, MT5UnavailableError, chunk_days_for_max_bars
from tests.conftest import BASE_CONFIG, make_ohlcv


def test_write_read_partition_roundtrip(cfg):
    df = make_ohlcv(n=500, start="2023-06-01")
    storage.write_partition(df, cfg, "raw", "EURUSD", "M15")
    read = storage.read_partition(cfg, "raw", "EURUSD", "M15")
    assert len(read) == len(df)
    assert (read["time"] == df["time"]).all()
    assert read["close"].isna().sum() == 0
    meta = storage.read_metadata(cfg, "raw", "EURUSD", "M15")
    assert meta["rows"] == len(df)
    assert meta["symbol"] == "EURUSD"


def test_partition_by_year(cfg):
    df = make_ohlcv(n=5000, start="2022-01-01", timeframe="H1")
    storage.write_partition(df, cfg, "raw", "GBPUSD", "H1")
    years_only = storage.read_partition(cfg, "raw", "GBPUSD", "H1", years=[2022])
    assert set(years_only["time"].dt.year) == {2022}


def test_iter_partition_streams(cfg):
    df = make_ohlcv(n=1000, start="2023-01-01")
    storage.write_partition(df, cfg, "raw", "EURUSD", "M15")
    total = 0
    for chunk in storage.iter_partition(cfg, "raw", "EURUSD", "M15"):
        total += len(chunk)
    assert total == len(df)


def test_zip_unzip_roundtrip(cfg, tmp_path):
    df = make_ohlcv(n=300, start="2023-01-01")
    storage.write_partition(df, cfg, "raw", "USDJPY", "M15")
    zip_path = storage.zip_dataset(cfg, "raw", "USDJPY", "M15", out_dir=tmp_path / "exp")
    assert zip_path.exists()
    restored = storage.unzip_dataset(zip_path, cfg, "raw", "USDJPY", "M15")
    read = storage.read_partition(cfg, "raw", "USDJPY", "M15")
    assert len(read) == len(df)
    assert restored.exists()


def test_cleaner_deduplicates_and_sorts(cfg):
    df = make_ohlcv(n=100, start="2023-01-01")
    df = pd.concat([df, df.iloc[[5]]], ignore_index=True)
    df = df.sample(frac=1.0, random_state=1)
    cleaned, report = clean(df)
    assert cleaned["time"].is_monotonic_increasing
    assert cleaned["time"].duplicated().sum() == 0
    assert report["rows_after"] == len(df) - 1


def test_cleaner_drops_nan_ohlc(cfg):
    df = make_ohlcv(n=50, start="2023-01-01")
    df.loc[10, "close"] = float("nan")
    cleaned, report = clean(df)
    assert len(cleaned) == len(df) - 1


def test_gap_detection(cfg):
    df = make_ohlcv(n=50, start="2023-01-01")
    gap_df = pd.concat([df, df.copy()], ignore_index=True)
    gap_df.loc[len(df):, "time"] = gap_df.loc[len(df):, "time"] + pd.Timedelta(days=3)
    gap_df = gap_df.sort_values("time")
    gaps = detect_gaps(gap_df, "M15")
    assert len(gaps) >= 1


def test_gap_detection_tolerance_multiplier(cfg):
    """A larger tolerance_mult must report fewer gaps (data.gap_tolerance_multiplier)."""
    df = make_ohlcv(n=50, start="2023-01-01")
    gap_df = pd.concat([df, df.copy()], ignore_index=True)
    gap_df.loc[len(df):, "time"] = gap_df.loc[len(df):, "time"] + pd.Timedelta(days=3)
    gap_df = gap_df.sort_values("time")
    assert len(detect_gaps(gap_df, "M15", tolerance_mult=2.0)) == 1
    assert len(detect_gaps(gap_df, "M15", tolerance_mult=300.0)) == 0


def test_integrity_report_flags_duplicates(cfg):
    df = make_ohlcv(n=50, start="2023-01-01")
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    report = storage.integrity_report(df, ["time", "open", "high", "low", "close"])
    assert report["duplicate_timestamps"] == 1
    assert report["fatal"] is True


def test_collector_unavailable_raises_clear_error(cfg, monkeypatch):
    import types

    fake = types.ModuleType("MetaTrader5")
    fake.initialize = lambda *a, **k: False
    fake.last_error = lambda: (1, "terminal not running")
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    from src.data.mt5_collector import MT5Collector

    collector = MT5Collector(cfg)
    with pytest.raises(MT5UnavailableError):
        collector.collect("EURUSD", "M15", pd.Timestamp("2023-01-01", tz="UTC"),
                          pd.Timestamp("2023-01-02", tz="UTC"))


def test_rates_columns_match_spec(cfg):
    assert set(RATE_COLUMNS) == {"time", "open", "high", "low", "close",
                                 "tick_volume", "real_volume", "spread"}


def test_chunk_days_respects_max_bars(cfg):
    assert chunk_days_for_max_bars(cfg, "M15", default=400) == 400
    cfg2 = Config(data=dict(BASE_CONFIG))
    cfg2.data["mt5"] = {"max_bars_per_request": 100_000}
    # 100k M15 bars * 900 s/bar = 90M s = ~1041 days.
    assert chunk_days_for_max_bars(cfg2, "M15", default=400) > 400
    assert chunk_days_for_max_bars(cfg2, "M5", default=400) * 86_400 // 300 <= 100_000

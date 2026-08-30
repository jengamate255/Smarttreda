"""Load OHLCV data from MT5 or Parquet files, with synthetic fallback."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TIMEFRAME_MAP: dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440, "W1": 10080, "MN1": 43200,
}

SYNTHETIC_PARAMS: dict[str, dict] = {
    "EURUSD": {"drift": 0.0, "vol": 0.0005, "mean_price": 1.10, "reversion": 0.001},
    "GBPUSD": {"drift": 0.0, "vol": 0.0006, "mean_price": 1.27, "reversion": 0.001},
    "USDJPY": {"drift": 0.0, "vol": 0.0004, "mean_price": 110.0, "reversion": 0.001},
    "AUDUSD": {"drift": 0.0, "vol": 0.0005, "mean_price": 0.72, "reversion": 0.001},
    "USDCHF": {"drift": 0.0, "vol": 0.0004, "mean_price": 0.92, "reversion": 0.001},
    "USDCAD": {"drift": 0.0, "vol": 0.0004, "mean_price": 1.36, "reversion": 0.001},
    "NZDUSD": {"drift": 0.0, "vol": 0.0005, "mean_price": 0.66, "reversion": 0.001},
    "XAUUSD": {"drift": 0.0, "vol": 0.0008, "mean_price": 1800.0, "reversion": 0.0005},
}


class DataLoader:
    """Unified interface for loading OHLCV data."""

    def __init__(self, mt5_client: Optional[object] = None) -> None:
        self._client = mt5_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_from_mt5(
        self,
        symbol: str,
        timeframe: str,
        start_date: str | datetime,
        end_date: str | datetime,
    ) -> pd.DataFrame:
        """Fetch historical bars from MT5 and return a standardised DataFrame.

        Falls back to synthetic data generation when MT5 is unavailable.
        """
        start_dt = self._to_utc(start_date)
        end_dt = self._to_utc(end_date)

        if self._client is not None:
            try:
                return self._fetch_from_mt5(symbol, timeframe, start_dt, end_dt)
            except Exception as exc:
                logger.warning(
                    "MT5 fetch failed for %s %s: %s – falling back to synthetic data",
                    symbol, timeframe, exc,
                )

        return self._generate_synthetic(symbol, timeframe, start_dt, end_dt)

    def load_from_parquet(self, filepath: str | Path) -> pd.DataFrame:
        """Load an OHLCV DataFrame saved as Parquet."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Parquet file not found: {path}")
        df = pd.read_parquet(path)
        df = self._normalise_columns(df)
        if not isinstance(df.index, pd.DatetimeIndex):
            if "time" in df.columns:
                df = df.set_index("time")
            else:
                raise ValueError("No 'time' column or DatetimeIndex in parquet file")
        df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index
        df.index = df.index.tz_convert("UTC").tz_localize(None)
        df.sort_index(inplace=True)
        logger.info("Loaded %d rows from %s", len(df), path)
        return df

    def save_to_parquet(self, df: pd.DataFrame, filepath: str | Path) -> None:
        """Persist an OHLCV DataFrame to Parquet."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, engine="pyarrow")
        logger.info("Saved %d rows to %s", len(df), path)

    def load_synthetic(
        self,
        symbol: str = "EURUSD",
        timeframe: str = "H1",
        n_bars: int = 2000,
        seed: int = 42,
    ) -> pd.DataFrame:
        """Generate synthetic OHLCV data directly by bar count."""
        from datetime import timedelta
        tf_minutes = TIMEFRAME_MAP.get(timeframe.upper(), 60)
        end_dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        start_dt = end_dt - timedelta(minutes=tf_minutes * n_bars)
        return self._generate_synthetic(symbol, timeframe, start_dt, end_dt)

    # ------------------------------------------------------------------
    # MT5 helper
    # ------------------------------------------------------------------

    def _fetch_from_mt5(
        self,
        symbol: str,
        timeframe: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pd.DataFrame:
        tf_minutes = TIMEFRAME_MAP.get(timeframe.upper())
        if tf_minutes is None:
            raise ValueError(f"Unsupported timeframe '{timeframe}'")

        tf_seconds = tf_minutes * 60
        total_seconds = max(0, (end_dt - start_dt).total_seconds())
        bar_count = min(int(total_seconds / tf_seconds) + 1, 500_000)

        client = self._client  # MT5Client instance
        client.ensure_connected()

        # Use positional fetch in a loop to cover large date ranges
        frames: list[pd.DataFrame] = []
        remaining = bar_count
        pos = 0
        batch = 100_000
        while remaining > 0:
            n = min(batch, remaining)
            raw = client.rates(symbol, timeframe, n, from_pos=pos)
            if raw is None or raw.empty:
                break
            frames.append(raw)
            remaining -= n
            pos += n

        if not frames:
            raise RuntimeError(f"MT5 returned no data for {symbol} {timeframe}")

        df = pd.concat(frames, ignore_index=True)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.rename(columns={"tick_volume": "volume"})
        df = df.set_index("time")
        df = df.loc[start_dt:end_dt]
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df.sort_index(inplace=True)
        logger.info(
            "Loaded %d bars from MT5: %s %s [%s – %s]",
            len(df), symbol, timeframe, start_dt, end_dt,
        )
        return df

    # ------------------------------------------------------------------
    # Synthetic data
    # ------------------------------------------------------------------

    def _generate_synthetic(
        self,
        symbol: str,
        timeframe: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pd.DataFrame:
        """Generate realistic OHLCV via geometric Brownian motion with mean-reversion."""
        tf_minutes = TIMEFRAME_MAP.get(timeframe.upper(), 60)
        tf_seconds = tf_minutes * 60
        total_seconds = (end_dt - start_dt).total_seconds()
        n_bars = max(1, int(total_seconds / tf_seconds))

        params = SYNTHETIC_PARAMS.get(symbol, {
            "drift": 0.0, "vol": 0.0005, "mean_price": 1.0, "reversion": 0.001,
        })

        rng = np.random.default_rng(hash((symbol, timeframe, str(start_dt))) % (2**31))
        dt = tf_seconds / 86400.0  # fraction of a day

        drift = params["drift"]
        vol = params["vol"]
        mean_price = params["mean_price"]
        reversion = params["reversion"]

        # Generate close prices with mean-reverting GBM
        closes = np.empty(n_bars, dtype=np.float64)
        closes[0] = mean_price
        for i in range(1, n_bars):
            log_return = (
                drift * dt
                - reversion * (closes[i - 1] / mean_price - 1.0) * dt
                + vol * np.sqrt(dt) * rng.standard_normal()
            )
            closes[i] = closes[i - 1] * np.exp(log_return)

        # Generate OHLV from close
        intra_vol = vol * np.sqrt(dt) * 0.5
        highs = closes * (1.0 + np.abs(rng.standard_normal(n_bars)) * intra_vol)
        lows = closes * (1.0 - np.abs(rng.standard_normal(n_bars)) * intra_vol)
        opens = np.empty(n_bars, dtype=np.float64)
        opens[0] = closes[0]
        opens[1:] = closes[:-1] * (1.0 + rng.standard_normal(n_bars - 1) * intra_vol * 0.3)

        # Enforce OHLC consistency
        highs = np.maximum(highs, np.maximum(opens, closes))
        lows = np.minimum(lows, np.minimum(opens, closes))

        volumes = rng.integers(100, 5000, size=n_bars).astype(np.float64)

        timestamps = pd.date_range(start=start_dt, periods=n_bars, freq=f"{tf_minutes}min", tz="UTC")
        timestamps = timestamps.tz_localize(None)

        df = pd.DataFrame({
            "open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes,
        }, index=timestamps)
        df.index.name = "time"

        logger.info(
            "Generated %d synthetic bars for %s %s [%s – %s]",
            len(df), symbol, timeframe, start_dt, end_dt,
        )
        return df

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_utc(date_val: str | datetime) -> datetime:
        if isinstance(date_val, str):
            dt = datetime.strptime(date_val, "%Y-%m-%d")
        else:
            dt = date_val
        return dt.replace(tzinfo=timezone.utc)

    @staticmethod
    def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
        col_map = {"tick_volume": "volume", "real_volume": "volume"}
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        wanted = ["open", "high", "low", "close", "volume"]
        missing = [c for c in wanted if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
        return df[wanted]

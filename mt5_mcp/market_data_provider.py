"""Market data provider abstraction for multi-asset support."""

from __future__ import annotations

import abc
import base64
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .asset import AssetClass, asset_class_from_symbol, is_forex, get_phase1_symbols
from .data_schema import NormalizedCandle, NormalizedCandle as Candle, SymbolInfo, DataQualityReport, ALL_TIMEFRAMES

# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

_Timestamp = datetime
_Timeframe = str
_Symbol = str

# ---------------------------------------------------------------------------
# Provider result types
# ---------------------------------------------------------------------------

class ProviderResult(abc.ABC):
    """Base class for provider operation results."""

    @property
    @abc.abstractmethod
    def success(self) -> bool:
        ...


class ProviderSuccess(ProviderResult):
    """Successful provider result."""

    def __init__(self, data: Any) -> None:
        self._data = data

    @property
    def success(self) -> bool:
        return True

    @property
    def data(self) -> Any:
        return self._data


class ProviderError(ProviderResult):
    """Provider error result."""

    def __init__(self, message: str) -> None:
        self._message = message

    @property
    def success(self) -> bool:
        return False

    @property
    def message(self) -> str:
        return self._message


# ---------------------------------------------------------------------------
# MarketDataProvider abstract base
# ---------------------------------------------------------------------------


class MarketDataProvider(abc.ABC):
    """Abstract base for market data providers.

    All providers must normalize data to the common schema:
    - timestamp (UTC datetime)
    - symbol (str)
    - timeframe (str: M1, M5, M15, M30, H1, H4, D1, W1)
    - open, high, low, close (float)
    - volume (float: tick_volume or real_volume)
    - spread (float)

    Subclasses should implement specific data sources (CSV/Parquet, MT5,
    broker APIs, etc.) while adhering to this contract.
    """

    @abc.abstractmethod
    def get_symbols(self) -> list[_Symbol]:
        """Return list of symbols available from this provider."""

    @abc.abstractmethod
    def get_candles(
        self,
        symbol: _Symbol,
        timeframe: _Timeframe,
        start: _Timestamp | None = None,
        end: _Timestamp | None = None,
        count: int | None = None,
    ) -> list[Candle]:
        """Return candle data for symbol/timeframe in normalized format."""

    @abc.abstractmethod
    def get_ticks(self, symbol: _Symbol) -> list[dict]:
        """Return raw tick data for symbol."""

    @abc.abstractmethod
    def get_spread(self, symbol: _Symbol, timeframe: _Timeframe | None = None) -> float:
        """Return current or average spread for symbol."""

    @abc.abstractmethod
    def get_symbol_info(self, symbol: _Symbol) -> SymbolInfo:
        """Return normalized symbol metadata."""

    @abc.abstractmethod
    def get_sessions(self, symbol: _Symbol) -> dict[str, Any]:
        """Return trading session information for symbol."""

    @abc.abstractmethod
    def health_check(self) -> bool:
        """Return True if provider is healthy/connected."""


# ---------------------------------------------------------------------------
# CSV / Parquet provider (Phase 1 implementation)
# ---------------------------------------------------------------------------


class CsvParquetProvider(MarketDataProvider):
    """Provider that reads normalized CSV/Parquet data files.

    Expected file format (Parquet preferred):
    - Parquet schema: timestamp, symbol, timeframe, open, high, low, close, volume, spread
    - CSV columns: timestamp, symbol, timeframe, open, high, low, close, volume, spread

    Timestamps should be ISO format (UTC). Volume represents tick_volume by default;
    real_volume should be stored in a separate field if available.
    """

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir else Path("data")
        self._cache: dict[tuple[str, str], list[Candle]] = {}
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        """Ensure data directory exists."""
        self._data_dir.mkdir(parents=True, exist_ok=True)

    # -- Abstract interface --

    def get_symbols(self) -> list[str]:
        """Return symbols found in available data files."""
        symbols: set[str] = set()
        if not self._data_dir.exists():
            return []
        for f in self._data_dir.rglob("*.parquet"):
            try:
                df = pd.read_parquet(f)
                for sym in df["symbol"].unique():
                    symbols.add(str(sym))
            except Exception:
                continue
        for f in self._data_dir.rglob("*.csv"):
            try:
                df = pd.read_csv(f)
                for sym in df["symbol"].unique():
                    symbols.add(str(sym))
            except Exception:
                continue
        return sorted(symbols)

    def get_candles(
        self,
        symbol: _Symbol,
        timeframe: _Timeframe,
        start: _Timestamp | None = None,
        end: _Timestamp | None = None,
        count: int | None = None,
    ) -> list[Candle]:
        """Load candles from CSV/Parquet files.

        Files are expected in ``data_dir / symbol / timeframe.parquet``
        or ``data_dir / symbol / timeframe.csv``.
        """
        cache_key = (symbol, timeframe)
        if cache_key in self._cache:
            candles = self._cache[cache_key]
        else:
            candles = self._load_from_disk(symbol, timeframe)
            self._cache[cache_key] = candles

        # Apply start/end/filter
        result = candles
        if start or end or count:
            result = self._filter_candles(
                candles, symbol, timeframe, start=start, end=end, count=count
            )
        return result

    def _load_from_disk(self, symbol: _Symbol, timeframe: _Timeframe) -> list[Candle]:
        """Load candles from parquet or csv file on disk."""
        symbol_dir = self._data_dir / symbol
        parquet_file = symbol_dir / f"{timeframe}.parquet"
        csv_file = symbol_dir / f"{timeframe}.csv"

        if parquet_file.exists():
            return self._load_parquet(str(parquet_file), symbol, timeframe)
        if csv_file.exists():
            return self._load_csv(str(csv_file), symbol, timeframe)

        # Return empty list if no data file found
        return []

    def _load_parquet(self, path: str, symbol: _Symbol, timeframe: _Timeframe) -> list[Candle]:
        """Load candles from a Parquet file."""
        df = pd.read_parquet(path)
        return self._normalize_dataframe(df, symbol, timeframe)

    def _load_csv(self, path: str, symbol: _Symbol, timeframe: _Timeframe) -> list[Candle]:
        """Load candles from a CSV file."""
        df = pd.read_csv(path)
        return self._normalize_dataframe(df, symbol, timeframe)

    def _normalize_dataframe(
        self, df: pd.DataFrame, symbol: _Symbol, timeframe: _Timeframe
    ) -> list[Candle]:
        """Normalize a pandas DataFrame to list of NormalizedCandle."""
        # Ensure required columns exist
        required = {"timestamp", "open", "high", "low", "close", "volume", "spread"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        # Parse timestamps to UTC datetime
        timestamps = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        # Coerce numeric columns
        for col in ["open", "high", "low", "close", "volume", "spread"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        candles: list[Candle] = []
        for i in range(len(df)):
            ts = timestamps.iloc[i]
            if ts is pd.NaT:
                continue  # skip bad timestamps
            # Ensure timezone-aware UTC
            if ts.tz is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")

            real_vol = df["real_volume"].iloc[i] if "real_volume" in df.columns else None
            # Build NormalizedCandle - ensure asset_class is set
            from .asset import asset_class_from_symbol
            ac = asset_class_from_symbol(symbol)

            candle = Candle(
                timestamp=ts,
                symbol=symbol,
                timeframe=timeframe,
                open=float(df["open"].iloc[i]),
                high=float(df["high"].iloc[i]),
                low=float(df["low"].iloc[i]),
                close=float(df["close"].iloc[i]),
                volume=float(df["volume"].iloc[i]),
                spread=float(df["spread"].iloc[i]),
                real_volume=real_vol,
                asset_class=ac,
            )
            candles.append(candle)

        # Sort by timestamp ascending
        candles.sort(key=lambda c: c.timestamp)
        return candles

    def _filter_candles(
        self,
        candles: list[Candle],
        symbol: _Symbol,
        timeframe: _Timeframe,
        start: _Timestamp | None = None,
        end: _Timestamp | None = None,
        count: int | None = None,
    ) -> list[Candle]:
        """Filter candles by date range and/or count."""
        result = candles

        if start:
            start = start.astimezone(timezone.utc) if start.tz else start.replace(tzinfo=timezone.utc)
            result = [c for c in result if c.timestamp >= start]

        if end:
            end = end.astimezone(timezone.utc) if end.tz else end.replace(tzinfo=timezone.utc)
            result = [c for c in result if c.timestamp <= end]

        if count is not None:
            result = result[-count:]  # most recent count candles

        return result

    def get_ticks(self, symbol: _Symbol) -> list[dict]:
        """Tick data not typically available from CSV/Parquet; return empty list."""
        return []

    def get_spread(self, symbol: _Symbol, timeframe: _Timeframe | None = None) -> float:
        """Spread not always stored in data files; return 0.0 as placeholder."""
        return 0.0

    def get_symbol_info(self, symbol: _Symbol) -> SymbolInfo:
        """Basic symbol info from asset class mapping."""
        from .asset import is_forex, asset_class_from_symbol

        ac = asset_class_from_symbol(symbol)
        return SymbolInfo(
            symbol=symbol,
            asset_class=ac,
            point=0.00001,
            digits=5,
            trade_mode=0,
            filling_mode=2,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            tick_size=0.00001,
            tick_value=1.0,
            contract_size=100000,
        )

    def get_sessions(self, symbol: _Symbol) -> dict[str, Any]:
        """Session data not typically stored; return empty structure."""
        return {
            "asian": {"active": False, "hours": "21:00-08:00 UTC"},
            "london": {"active": False, "hours": "08:00-17:00 UTC"},
            "new_york": {"active": False, "hours": "13:00-22:00 UTC"},
            "overlap_london_ny": {"active": False, "hours": "13:00-17:00 UTC"},
        }

    def health_check(self) -> bool:
        """Data directory exists and is accessible."""
        return self._data_dir.exists()


# ---------------------------------------------------------------------------
# MT5 adapter (Phase 1B - live MT5 connection)
# ---------------------------------------------------------------------------


class Mt5Provider(MarketDataProvider):
    """MT5 market data provider.

    Connects to a running MetaTrader 5 terminal and fetches live candle data,
    normalizing it to the common NormalizedCandle schema.

    Args:
        mt5: Optional pre-configured MetaTrader5 module (for testing).
             If None, will try to import and initialize MetaTrader5.
        terminal_path: Optional path to MT5 terminal executable.
        login: Optional MT5 account login.
        password: Optional MT5 account password.
        server: Optional MT5 server name.
    """

    def __init__(
        self,
        mt5: Any = None,
        terminal_path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
    ) -> None:
        self._mt5 = mt5
        self._connected = False
        self._terminal_path = terminal_path
        self._login = login
        self._password = password
        self._server = server
        self._symbol_cache: dict[str, Any] = {}

    def connect(self) -> bool:
        """Connect to MT5 terminal."""
        if self._mt5 is not None:
            self._connected = True
            return True

        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5
        except ImportError:
            raise RuntimeError(
                "MetaTrader5 package is not installed. "
                "Install with: pip install MetaTrader5"
            )

        init_kwargs: dict[str, Any] = {}
        if self._terminal_path:
            init_kwargs["path"] = self._terminal_path
        if self._login:
            init_kwargs["login"] = self._login
        if self._password:
            init_kwargs["password"] = self._password
        if self._server:
            init_kwargs["server"] = self._server

        if not self._mt5.initialize(**init_kwargs):
            error = self._mt5.last_error()
            raise RuntimeError(f"MT5 initialize failed: {error}")

        self._connected = True
        return True

    def disconnect(self) -> None:
        """Disconnect from MT5 terminal."""
        if self._mt5 is not None and self._connected:
            try:
                self._mt5.shutdown()
            except Exception:
                pass
            self._connected = False

    def _ensure_connected(self) -> None:
        """Ensure MT5 is connected."""
        if not self._connected:
            self.connect()

    def _mt5_timeframe(self, tf: str) -> int:
        """Convert our timeframe string to MT5 timeframe constant."""
        from .common import TIMEFRAME_MT5
        if tf not in TIMEFRAME_MT5:
            raise ValueError(f"Unsupported timeframe: {tf}. Supported: {list(TIMEFRAME_MT5.keys())}")
        return TIMEFRAME_MT5[tf]

    def _normalize_candles(self, data: Any, symbol: str, timeframe: str) -> list[Candle]:
        """Convert MT5 rate data to NormalizedCandle list."""
        from .asset import asset_class_from_symbol

        if data is None or len(data) == 0:
            return []

        ac = asset_class_from_symbol(symbol)
        candles: list[Candle] = []

        for bar in data:
            try:
                ts = datetime.fromtimestamp(int(bar["time"]), tz=timezone.utc)
                tick_vol = float(bar["tick_volume"]) if "tick_volume" in data.dtype.names else 0.0
                real_vol_val = bar["real_volume"] if "real_volume" in data.dtype.names else 0
                real_vol = float(real_vol_val) if real_vol_val and real_vol_val > 0 else None
                candle = Candle(
                    timestamp=ts,
                    symbol=symbol,
                    timeframe=timeframe,
                    open=float(bar["open"]),
                    high=float(bar["high"]),
                    low=float(bar["low"]),
                    close=float(bar["close"]),
                    volume=tick_vol,
                    spread=float(bar["spread"]) if "spread" in data.dtype.names else 0.0,
                    real_volume=real_vol,
                    asset_class=ac,
                )
                candles.append(candle)
            except (ValueError, KeyError):
                continue

        return candles

    # -- Abstract interface --

    def get_symbols(self) -> list[str]:
        """Return list of symbols available in MT5."""
        self._ensure_connected()
        symbols = self._mt5.symbols_get()
        if symbols is None:
            return []
        return [s.name for s in symbols]

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        count: int | None = None,
    ) -> list[Candle]:
        """Fetch candle data from MT5."""
        self._ensure_connected()
        tf = self._mt5_timeframe(timeframe)

        if start is not None:
            end_ts = end or datetime.now(timezone.utc)
            start_ts = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
            data = self._mt5.copy_rates_range(symbol, tf, start_ts, end_ts)
        else:
            n = count or 100
            data = self._mt5.copy_rates_from_pos(symbol, tf, 0, n)

        return self._normalize_candles(data, symbol, timeframe)

    def get_ticks(self, symbol: str) -> list[dict]:
        """Fetch recent ticks from MT5."""
        self._ensure_connected()
        ticks = self._mt5.copy_ticks_from_pos(symbol, 0, 100)
        if ticks is None:
            return []
        return [
            {
                "time": int(t["time"]),
                "bid": float(t["bid"]),
                "ask": float(t["ask"]),
                "last": float(t["last"]) if "last" in ticks.dtype.names else 0.0,
                "volume": float(t["volume"]) if "volume" in ticks.dtype.names else 0.0,
            }
            for t in ticks
        ]

    def get_spread(self, symbol: str, timeframe: str | None = None) -> float:
        """Return current spread for symbol."""
        self._ensure_connected()
        tick = self._mt5.symbol_info_tick(symbol)
        if tick is None:
            return 0.0
        info = self._mt5.symbol_info(symbol)
        if info is None:
            return 0.0
        return (tick.ask - tick.bid) / info.point

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        """Return normalized symbol metadata from MT5."""
        self._ensure_connected()
        info = self._mt5.symbol_info(symbol)
        if info is None:
            return SymbolInfo(
                symbol=symbol,
                asset_class=AssetClass.FOREX,
                point=0.00001,
                digits=5,
                trade_mode=0,
                filling_mode=2,
                volume_min=0.01,
                volume_max=100.0,
                volume_step=0.01,
                tick_size=0.00001,
                tick_value=1.0,
                contract_size=100000,
            )

        from .asset import asset_class_from_symbol
        ac = asset_class_from_symbol(symbol)

        return SymbolInfo(
            symbol=symbol,
            asset_class=ac,
            point=float(info.point),
            digits=int(info.digits),
            trade_mode=int(info.trade_mode),
            filling_mode=int(getattr(info, "filling_mode", 2)),
            volume_min=float(info.volume_min),
            volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            tick_size=float(getattr(info, "point", 0.00001)),
            tick_value=float(getattr(info, "trade_contract_size", 100000)),
            contract_size=float(getattr(info, "trade_contract_size", 100000)),
        )

    def get_sessions(self, symbol: str) -> dict[str, Any]:
        """Session info not directly available from MT5; return standard sessions."""
        return {
            "asian": {"active": False, "hours": "21:00-08:00 UTC"},
            "london": {"active": False, "hours": "08:00-17:00 UTC"},
            "new_york": {"active": False, "hours": "13:00-22:00 UTC"},
            "overlap_london_ny": {"active": False, "hours": "13:00-17:00 UTC"},
        }

    def health_check(self) -> bool:
        """Return True if MT5 is connected and responsive."""
        if not self._connected or self._mt5 is None:
            return False
        try:
            info = self._mt5.account_info()
            return info is not None
        except Exception:
            return False
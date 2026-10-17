from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio

from trading_ai.strategies.genome.schema import StrategyGenome


# ---------------------------------------------------------------------------
# Async DB session (in-memory SQLite)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def async_session():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            __import__("trading_ai.memory.journal", fromlist=["Base"])
            .Base.metadata.create_all
        )
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Sample OHLCV data generator
# ---------------------------------------------------------------------------

def _generate_ohlcv(
    start: datetime,
    periods: int,
    timeframe_seconds: int,
    base_price: float = 1.1000,
    volatility: float = 0.0005,
    trend: float = 0.0,
    include_spread: bool = True,
) -> pd.DataFrame:
    """Generate synthetic but realistic OHLCV data."""
    rng = np.random.default_rng()
    dates = pd.date_range(start=start, periods=periods, freq=f"{timeframe_seconds}s", tz=timezone.utc)

    closes = np.empty(periods)
    opens = np.empty(periods)
    highs = np.empty(periods)
    lows = np.empty(periods)
    volumes = np.empty(periods)
    spreads = np.empty(periods)

    price = base_price
    for i in range(periods):
        drift = trend + rng.normal(0, volatility * 0.3)
        change = rng.normal(drift, volatility)
        open_p = price
        close_p = price + change
        intra_high = abs(change) * rng.uniform(0.2, 1.0)
        intra_low = abs(change) * rng.uniform(0.2, 1.0)
        high_p = max(open_p, close_p) + intra_high
        low_p = min(open_p, close_p) - intra_low
        vol = rng.uniform(50, 500)
        spread = rng.uniform(0.5, 3.0)

        opens[i] = round(open_p, 5)
        closes[i] = round(close_p, 5)
        highs[i] = round(high_p, 5)
        lows[i] = round(low_p, 5)
        volumes[i] = round(vol, 2)
        spreads[i] = round(spread, 2)
        price = close_p

    data = {
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }
    if include_spread:
        data["spread"] = spreads

    df = pd.DataFrame(data, index=dates)
    df.index.name = "time"
    return df


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """500 H1 candles starting 2024-01-01."""
    return _generate_ohlcv(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=500,
        timeframe_seconds=3600,
        base_price=1.1000,
        volatility=0.0008,
    )


@pytest.fixture
def sample_ohlcv_with_gaps() -> pd.DataFrame:
    """OHLCV with intentionally injected gaps."""
    df = _generate_ohlcv(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=200,
        timeframe_seconds=3600,
    )
    # Remove a block of candles to create a gap
    gap_start = 50
    gap_end = 55
    df = pd.concat([df.iloc[:gap_start], df.iloc[gap_end:]])
    return df


@pytest.fixture
def sample_ohlcv_invalid() -> pd.DataFrame:
    """OHLCV with intentional invalid data (high < low, NaN, negative)."""
    df = _generate_ohlcv(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=100,
        timeframe_seconds=3600,
    )
    # Inject high < low
    df.iloc[10, df.columns.get_loc("high")] = 0.9000
    df.iloc[10, df.columns.get_loc("low")] = 1.2000
    # Inject NaN
    df.iloc[20, df.columns.get_loc("close")] = np.nan
    # Inject negative price
    df.iloc[30, df.columns.get_loc("open")] = -0.5
    return df


@pytest.fixture
def sample_ohlcv_duplicate_timestamps() -> pd.DataFrame:
    """OHLCV with duplicate timestamps."""
    df = _generate_ohlcv(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=100,
        timeframe_seconds=3600,
    )
    dup_row = df.iloc[50:51].copy()
    df = pd.concat([df, dup_row])
    return df


@pytest.fixture
def sample_ohlcv_wide_spread() -> pd.DataFrame:
    """OHLCV with abnormally wide spreads."""
    df = _generate_ohlcv(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=100,
        timeframe_seconds=3600,
    )
    df.iloc[40, df.columns.get_loc("spread")] = 150.0
    df.iloc[60, df.columns.get_loc("spread")] = 200.0
    return df


# ---------------------------------------------------------------------------
# Sample strategy genome
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_genome() -> StrategyGenome:
    return StrategyGenome(
        strategy_id="test-strategy-001",
        version=1,
        name="Test Supply Demand",
        description="Basic supply/demand strategy for testing",
        market=["EURUSD", "GBPUSD"],
        timeframes=["H4", "H1", "M15"],
        entry_rules=[
            {
                "type": "supply_demand",
                "params": {
                    "zone_lookback": 50,
                    "min_zone_strength": 0.6,
                    "require_ob_level": True,
                },
                "description": "Supply/demand zone entry",
            }
        ],
        exit_rules=[
            {
                "type": "risk_reward",
                "params": {"min_rr": 3.0, "target_rr": 5.0},
                "description": "Risk-reward exit",
            }
        ],
        risk_rules=[
            {
                "type": "fixed_fraction",
                "params": {"risk_per_trade": 0.01, "max_positions": 3},
                "description": "Fixed fraction sizing",
            }
        ],
        filters=[],
    )


@pytest.fixture
def sample_genome_with_volatility_filter(sample_genome: StrategyGenome) -> StrategyGenome:
    child = sample_genome.mutate({
        "filters": [
            {
                "type": "volatility",
                "params": {"min_atr": 0.0005, "max_atr": 0.005},
                "description": "Volatility regime filter",
            }
        ],
    })
    return child


# ---------------------------------------------------------------------------
# Paper broker instance
# ---------------------------------------------------------------------------

class PaperBroker:
    """Minimal paper trading broker for testing."""

    def __init__(self, initial_balance: float = 100_000.0):
        self.balance = initial_balance
        self.equity = initial_balance
        self.positions: list[dict] = []
        self.closed_trades: list[dict] = []
        self._tick_data: dict[str, dict] = {}

    def update_tick(self, symbol: str, bid: float, ask: float) -> None:
        self._tick_data[symbol] = {"bid": bid, "ask": ask, "time": datetime.now(timezone.utc)}
        self._check_stops(symbol)

    def open_position(
        self, symbol: str, direction: str, lot_size: float,
        entry_price: float, stop_loss: float, take_profit: float,
    ) -> dict:
        pos = {
            "id": str(uuid.uuid4()),
            "symbol": symbol,
            "direction": direction,
            "lot_size": lot_size,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "open_time": datetime.now(timezone.utc),
            "unrealized_pnl": 0.0,
        }
        self.positions.append(pos)
        return pos

    def close_position(self, position_id: str, exit_price: float) -> dict | None:
        for i, pos in enumerate(self.positions):
            if pos["id"] == position_id:
                removed = self.positions.pop(i)
                pnl = self._calc_pnl(removed, exit_price)
                removed["exit_price"] = exit_price
                removed["pnl"] = pnl
                removed["close_time"] = datetime.now(timezone.utc)
                self.balance += pnl
                self.closed_trades.append(removed)
                return removed
        return None

    def _calc_pnl(self, pos: dict, exit_price: float) -> float:
        if pos["direction"] == "BUY":
            return (exit_price - pos["entry_price"]) * pos["lot_size"] * 100_000
        return (pos["entry_price"] - exit_price) * pos["lot_size"] * 100_000

    def _check_stops(self, symbol: str) -> None:
        tick = self._tick_data.get(symbol)
        if not tick:
            return
        to_close = []
        for pos in self.positions:
            if pos["symbol"] != symbol:
                continue
            if pos["direction"] == "BUY":
                if tick["bid"] <= pos["stop_loss"]:
                    to_close.append((pos["id"], pos["stop_loss"]))
                elif tick["bid"] >= pos["take_profit"]:
                    to_close.append((pos["id"], pos["take_profit"]))
            else:
                if tick["ask"] >= pos["stop_loss"]:
                    to_close.append((pos["id"], pos["stop_loss"]))
                elif tick["ask"] <= pos["take_profit"]:
                    to_close.append((pos["id"], pos["take_profit"]))
        for pid, price in to_close:
            self.close_position(pid, price)


@pytest.fixture
def paper_broker() -> PaperBroker:
    return PaperBroker(initial_balance=100_000.0)


# ---------------------------------------------------------------------------
# Market state fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_market_state() -> dict:
    return {
        "regime": "ranging",
        "volatility": 0.0008,
        "trend_strength": 0.2,
        "session": "london",
        "swings": {"highs": [1.1050, 1.1080], "lows": [1.0950, 1.0920]},
        "bos": None,
        "atr_14": 0.00075,
    }

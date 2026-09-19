"""Tests for Mt5Provider - MT5 market data adapter."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from mt5_mcp.market_data_provider import Mt5Provider


class FakeMT5:
    def __init__(self) -> None:
        self._initialized = False
        self._symbols = [
            SimpleNamespace(name="EURUSD"),
            SimpleNamespace(name="GBPUSD"),
            SimpleNamespace(name="USDJPY"),
        ]

    def initialize(self, **kwargs: Any) -> bool:
        self._initialized = True
        return True

    def shutdown(self) -> None:
        self._initialized = False

    def last_error(self):
        return (0, "ok")

    def symbols_get(self, group: str = ""):
        return tuple(self._symbols)

    def copy_rates_from_pos(self, symbol, timeframe, from_pos, count):
        n = min(int(count), 100)
        base = int(time.time()) - n * 3600
        dtype = [
            ("time", "<i8"), ("open", "<f8"), ("high", "<f8"),
            ("low", "<f8"), ("close", "<f8"), ("tick_volume", "<i8"),
            ("spread", "<i4"), ("real_volume", "<i8"),
        ]
        data = np.zeros(n, dtype=dtype)
        price = 1.1000
        for i in range(n):
            data[i]["time"] = base + i * 3600
            data[i]["open"] = price
            data[i]["high"] = price + 0.0005
            data[i]["low"] = price - 0.0005
            price += 0.0003
            data[i]["close"] = price
            data[i]["tick_volume"] = 100
            data[i]["spread"] = 2
            data[i]["real_volume"] = 0
        return data

    def copy_rates_range(self, symbol, timeframe, start, end):
        return self.copy_rates_from_pos(symbol, timeframe, 0, 100)

    def copy_ticks_from_pos(self, symbol, from_pos, count):
        n = min(int(count), 100)
        dtype = [("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"), ("volume", "<f8")]
        data = np.zeros(n, dtype=dtype)
        for i in range(n):
            data[i]["time"] = int(time.time()) - i
            data[i]["bid"] = 1.1000
            data[i]["ask"] = 1.10005
            data[i]["last"] = 1.1000
            data[i]["volume"] = 10
        return data

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=1.1000, ask=1.10005)

    def symbol_info(self, symbol):
        return SimpleNamespace(
            name=symbol, point=0.00001, digits=5, trade_mode=0,
            filling_mode=2, volume_min=0.01, volume_max=100.0,
            volume_step=0.01, trade_contract_size=100000,
        )

    def account_info(self):
        return SimpleNamespace(login=12345, balance=10000.0, equity=10000.0)


@pytest.fixture
def fake_mt5():
    return FakeMT5()


@pytest.fixture
def provider(fake_mt5):
    return Mt5Provider(mt5=fake_mt5)


def test_connect_with_preconfigured_mt5(fake_mt5):
    p = Mt5Provider(mt5=fake_mt5)
    assert p.connect() is True
    assert p._connected is True


def test_health_check(provider):
    provider.connect()
    assert provider.health_check() is True


def test_health_check_not_connected():
    p = Mt5Provider()
    assert p.health_check() is False


def test_get_symbols(provider):
    provider.connect()
    symbols = provider.get_symbols()
    assert "EURUSD" in symbols
    assert "GBPUSD" in symbols
    assert "USDJPY" in symbols


def test_get_candles_count(provider):
    provider.connect()
    candles = provider.get_candles("EURUSD", "H1", count=50)
    assert len(candles) == 50
    assert candles[0].symbol == "EURUSD"
    assert candles[0].timeframe == "H1"
    assert candles[0].open > 0


def test_get_candles_sorted_ascending(provider):
    provider.connect()
    candles = provider.get_candles("EURUSD", "H1", count=20)
    for i in range(1, len(candles)):
        assert candles[i].timestamp >= candles[i - 1].timestamp


def test_get_candles_time_range(provider):
    provider.connect()
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 2, tzinfo=timezone.utc)
    candles = provider.get_candles("EURUSD", "H1", start=start, end=end)
    assert len(candles) > 0


def test_get_candles_asset_class(provider):
    provider.connect()
    candles = provider.get_candles("EURUSD", "H1", count=5)
    for c in candles:
        assert c.asset_class.value == "forex"


def test_get_ticks(provider):
    provider.connect()
    ticks = provider.get_ticks("EURUSD")
    assert len(ticks) > 0
    assert "bid" in ticks[0]
    assert "ask" in ticks[0]


def test_get_spread(provider):
    provider.connect()
    spread = provider.get_spread("EURUSD")
    assert spread >= 0


def test_get_symbol_info(provider):
    provider.connect()
    info = provider.get_symbol_info("EURUSD")
    assert info.symbol == "EURUSD"
    assert info.point == 0.00001
    assert info.digits == 5
    assert info.volume_min == 0.01
    assert info.volume_max == 100.0


def test_get_sessions(provider):
    provider.connect()
    sessions = provider.get_sessions("EURUSD")
    assert "london" in sessions
    assert "new_york" in sessions


def test_disconnect(provider):
    provider.connect()
    provider.disconnect()
    assert provider._connected is False


def test_unsupported_timeframe(provider):
    provider.connect()
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        provider.get_candles("EURUSD", "X1", count=10)


def test_empty_data():
    class EmptyMT5(FakeMT5):
        def copy_rates_from_pos(self, symbol, timeframe, from_pos, count):
            return np.array([], dtype=[("time", "<i8"), ("open", "<f8")])
    p = Mt5Provider(mt5=EmptyMT5())
    p.connect()
    candles = p.get_candles("EURUSD", "H1", count=10)
    assert candles == []

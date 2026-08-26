"""Shared test fixtures: fake MT5 module, fake client."""

from __future__ import annotations

import sys
import time
import importlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _alias_test_modules() -> None:
    """Keep legacy top-level test imports on the package module instances."""
    names = (
        "client",
        "common",
        "config",
        "mt5_client",
        "indicators",
        "price_action",
        "supply_demand",
        "strategy",
        "market_intel",
        "chart_patterns",
        "coach",
        "research",
        "ml_ranker",
        "monte_carlo",
        "backtest",
        "dashboard",
    )
    for name in names:
        sys.modules[name] = importlib.import_module(f"mt5_mcp.{name}")

    sys.modules["memory"] = importlib.import_module("mt5_mcp.memory")
    for name in ("trade_journal", "learning", "statistics"):
        sys.modules[f"memory.{name}"] = importlib.import_module(
            f"mt5_mcp.memory.{name}"
        )

    sys.modules["tools"] = importlib.import_module("mt5_mcp.tools")
    for name in ("news", "risk", "trades", "strategy", "candles"):
        sys.modules[f"tools.{name}"] = importlib.import_module(
            f"mt5_mcp.tools.{name}"
        )


_alias_test_modules()

from mt5_mcp.mt5_client import MT5Client


class FakeMT5Module:
    """Mimics the parts of the MetaTrader5 module used by MT5Client."""

    def __init__(self) -> None:
        self.account = {
            "login": 12345,
            "server": "Broker-Server",
            "balance": 10000.0,
            "equity": 10050.0,
            "margin": 100.0,
            "margin_free": 9900.0,
            "margin_level": 10050.0,
            "currency": "USD",
            "leverage": 100,
            "profit": 50.0,
        }
        self.retcode = 10009
        self.symbol = "EURUSD"

    def initialize(self, **kwargs) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def last_error(self):
        return (0, "ok")

    def account_info(self):
        return SimpleNamespace(**self.account)

    def symbol_info(self, symbol):
        return SimpleNamespace(
            symbol=symbol,
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
            trade_stops_level=0,
        )

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(
            time=int(time.time()), bid=1.1000, ask=1.10005, last=1.1000, volume=10
        )

    def copy_rates_from_pos(self, symbol, timeframe, from_pos, count):
        n = min(int(count), 100)
        base = int(time.time()) - n * 3600
        dtype = [
            ("time", "<i8"),
            ("open", "<f8"),
            ("high", "<f8"),
            ("low", "<f8"),
            ("close", "<f8"),
            ("tick_volume", "<i8"),
            ("spread", "<i4"),
            ("real_volume", "<i8"),
        ]
        data = np.zeros(n, dtype=dtype)
        price = 1.1000
        for i in range(n):
            data[i]["time"] = base + i * 3600
            data[i]["open"] = price
            price += 0.0003
            data[i]["close"] = price
            data[i]["high"] = price + 0.0002
            data[i]["low"] = price - 0.0002
            data[i]["tick_volume"] = 100
            data[i]["spread"] = 2
            data[i]["real_volume"] = 1000
        return data

    def order_send(self, request):
        return SimpleNamespace(
            retcode=self.retcode,
            order=12345,
            deal=67890,
            comment="done",
            request=request,
        )

    def order_calc_margin(self, action, symbol, volume, price):
        return price * volume * 10

    def order_calc_profit(self, action, symbol, volume, price_open, price_close):
        return (price_close - price_open) * volume * 100000

    def positions_get(self, symbol=""):
        return ()

    def orders_get(self, symbol=""):
        return ()

    def history_orders_get(self, *args, **kwargs):
        return ()

    def history_deals_get(self, *args, **kwargs):
        return ()

    def symbols_get(self, group=""):
        return ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD")


@pytest.fixture
def fake_mt5() -> FakeMT5Module:
    return FakeMT5Module()


@pytest.fixture
def mt5_client(fake_mt5: FakeMT5Module) -> MT5Client:
    client = MT5Client()
    client._mt5 = fake_mt5
    client._connected = True
    return client


class FakeClient:
    """In-memory client with the surface used by strategy/analysis code."""

    def __init__(self) -> None:
        self.frames: dict[tuple[str, str], pd.DataFrame] = {}
        self.bid = 1.2000
        self.ask = 1.20005
        self.account = {
            "login": 12345,
            "server": "Broker-Server",
            "balance": 10000.0,
            "equity": 10000.0,
            "margin": 0.0,
            "margin_free": 10000.0,
            "margin_level": 0.0,
            "currency": "USD",
            "leverage": 100,
            "profit": 0.0,
        }
        self.closed: list[dict] = []
        self.deals_history: list[dict] = []
        self._positions: list[dict] = []
        self.requests: list[dict] = []
        self.order_result: dict | None = None
        self._market_open = (True, "market open")

    def ensure_connected(self) -> None:
        return None

    def market_open(self, symbol: str) -> tuple[bool, str]:
        return self._market_open

    def order_send(self, request: dict) -> dict:
        self.requests.append(request)
        if self.order_result is not None:
            return dict(self.order_result)
        return {"retcode": 10009, "comment": "done", "order": 999001}

    def symbol_info(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "point": 0.00001,
            "digits": 5,
            "trade_mode": 0,
            "filling_mode": 2,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "tick_size": 0.00001,
            "tick_value": 1.0,
            "contract_size": 100000,
            "trade_stops_level": 0,
        }

    def tick(self, symbol: str) -> dict:
        return {
            "time": int(time.time()),
            "bid": self.bid,
            "ask": self.ask,
            "last": self.bid,
            "volume": 10,
        }

    def rates(self, symbol: str, timeframe: str, count: int):
        frame = self.frames.get((symbol.upper(), str(timeframe).upper()))
        if frame is None:
            raise RuntimeError(f"no data for {symbol} {timeframe}")
        return frame.tail(count).reset_index(drop=True)

    def account_info(self) -> dict:
        return dict(self.account)

    def order_calc_margin(self, action, symbol, volume, price) -> float:
        return price * volume * 10

    def order_calc_profit(
        self, action, symbol, volume, price_open, price_close
    ) -> float:
        return (price_close - price_open) * volume * 100000

    def positions(self, symbol: str = "") -> list[dict]:
        if symbol:
            return [p for p in self._positions if p["symbol"] == symbol]
        return list(self._positions)

    def pending_orders(self, symbol: str = "") -> list[dict]:
        return list(getattr(self, "_pending", []))

    def server_time(self):
        from datetime import datetime, timezone

        return datetime.now(timezone.utc)

    def closed_orders(self, days: int = 30, symbol: str = "") -> list[dict]:
        if symbol:
            return [o for o in self.closed if o["symbol"] == symbol]
        return list(self.closed)

    def deals_since(self, epoch_seconds: int) -> list[dict]:
        return list(self.deals_history)

    def deals(self, days: int = 30, symbol: str = "") -> list[dict]:
        if symbol:
            return [d for d in self.deals_history if d.get("symbol") == symbol]
        return list(self.deals_history)


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def client_module():
    import client as client_module

    original = client_module._client
    yield client_module
    client_module._client = original

"""Shared test fixtures: fake MT5 module, fake client."""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Sibling helpers (helpers.py) live next to this file. Prepend import mode adds
# the test file's directory to sys.path implicitly, but --import-mode=importlib
# does not, so make it explicit and keep both modes working.
sys.path.insert(0, str(Path(__file__).resolve().parent))


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

    original = client_module._clients.copy()
    yield client_module
    client_module._clients = original


@pytest.fixture(autouse=True)
def permissive_execution_gate(monkeypatch, tmp_path):
    """Keep ordinary tests off the execution gate's blocking path.

    The live allowlist holds five symbols and every opening order must carry
    a magic id, a comment and a stop-loss, so a bare test request (e.g. the
    broker-retcode check in test_mt5_client) would be rejected before it ever
    reached the fake MT5. This stubs ``check`` open and redirects the audit
    log to tmp; tests that assert blocking use ``strict_execution_gate`` /
    ``strict_gate``, which restore the real implementation.
    """
    from mt5_mcp import execution_gate as eg

    monkeypatch.setattr(eg, "GATE_LOG", tmp_path / "execution_gate.jsonl")
    monkeypatch.setattr(eg, "_check_impl", eg.check, raising=False)
    monkeypatch.setattr(eg, "check", lambda request, client=None, source="": [])
    yield eg


def _ALLOWLIST_SNAPSHOT() -> list[str]:
    """Allowlist as settings_guard expects it (live-positive symbols)."""
    import settings_guard as sg

    return list(sg.ALLOWLIST)


@pytest.fixture
def strict_gate(permissive_execution_gate, monkeypatch):
    """Real gate with consensus/compliance verdicts stubbed open.

    Stays real: classification, magic/comment requirements, the blocked list,
    the fail-closed allowlist (read from the consensus config), live-stats
    suspension and every exception being a violation.
    """
    from mt5_mcp import compliance, consensus
    from mt5_mcp import execution_gate as eg
    from mt5_mcp import learning_loop

    monkeypatch.setattr(learning_loop, "_closed_rows", lambda **_: [])

    monkeypatch.setattr(eg, "check", eg._check_impl)
    monkeypatch.setattr(
        consensus, "check",
        lambda symbol, side, source=None, **kw: {"allowed": True, "violations": []},
    )
    monkeypatch.setattr(
        consensus, "_cfg", lambda: {"allowed_symbols": _ALLOWLIST_SNAPSHOT()}
    )
    monkeypatch.setattr(
        compliance, "gate",
        lambda *a, **kw: {"allowed": True, "violations": []},
    )
    return eg


@pytest.fixture(autouse=True)
def pinned_risk_settings(monkeypatch):
    """Pin risk limits to the RiskConfig defaults for deterministic tests.

    ``settings.json`` is tuned for live trading (``max_open_risk_percent`` 3.0,
    ``max_trades_per_day`` 6, ``confidence_floor_percent`` 75) while the gate
    tests assert the strict defaults this fixture restores. Without it the suite
    fails purely because the live config was retuned -- see
    .smart-trader/failures.md F3. ``get_settings()`` is lru_cached, so the same
    object the gate reads is patched and reverted per test.

    Pinned to RiskConfig defaults: max_open_risk_percent 1.0, max_trades_per_day
    3, confidence_floor_percent 90, max_daily_loss_percent 3.0 (live is 4.0 --
    the daily-loss mode tests calibrate -300/-240 on 10k equity against it),
    max_risk_percent 0.5 (live is 1.0 -- test_correlation_cap's combined
    euro-group exposure of ~86 only exceeds the cap at 0.5%). Live values
    conservative_risk_percent 0.5 is deliberately NOT pinned: the conservative
    small-lot assertion in test_daily_loss_80pct_conservative needs it.

    reject_orders is pinned True so every test exercises enforcement; the live
    settings.json runs the forward test with reject_orders=false (log-only
    gates), which is covered explicitly by test_log_mode.py. The record-only
    filters from settings.json (allowed_hours_utc, blocked_setup_prefixes) are
    pinned open so the deterministic gate tests keep asserting their original
    violation sets; test_log_mode.py closes them explicitly.
    """
    from config import get_settings

    risk = get_settings().risk
    monkeypatch.setattr(get_settings().quality,"enabled",False)
    monkeypatch.setattr(get_settings().performance_risk,"enabled",False)
    monkeypatch.setattr(get_settings().prop_risk,"enabled",False)
    monkeypatch.setattr(risk,"conservative_risk_percent",0.25)
    monkeypatch.setattr(risk, "max_open_risk_percent", 1.0)
    monkeypatch.setattr(risk, "max_trades_per_day", 3)
    monkeypatch.setattr(risk, "confidence_floor_percent", 90.0)
    monkeypatch.setattr(risk, "max_daily_loss_percent", 3.0)
    monkeypatch.setattr(risk, "max_risk_percent", 0.5)
    monkeypatch.setattr(risk, "reject_orders", True)
    monkeypatch.setattr(risk, "allowed_hours_utc", None)
    monkeypatch.setattr(risk, "blocked_setup_prefixes", [])
    yield risk

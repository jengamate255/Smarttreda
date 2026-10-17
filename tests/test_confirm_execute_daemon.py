"""Tests: confirm_execute_daemon gates, caps, alert bridge, close path, exits."""

from __future__ import annotations

import json
import logging
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import confirm_execute_daemon as dem

# keep the daemon's file logger out of the tests (it shares the live log path)
dem.logger.handlers.clear()
dem.logger.addHandler(logging.NullHandler())
dem.logger.propagate = False


# ------------------------------------------------------------------ fakes
class FakePlan:
    def __init__(self, **over):
        base = dict(
            action="BUY", symbol="EURUSD", timeframe="H1", valid=True,
            confidence=70.0, trade_score=72.0, risk_reward=2.5,
            reason="test setup", entry=1.1000, sl=1.0950, tp=1.1125,
            lots=0.10, zone_strength=70.0, confirmation="bullish",
        )
        base.update(over)
        for k, v in base.items():
            setattr(self, k, v)


class FakeRes:
    def __init__(self, accepted=True, ticket=999, error="", checks=()):
        self.accepted = accepted
        self.ticket = ticket
        self.error = error
        self.checks = list(checks)


class FakeClient:
    def __init__(self, positions=None, deals=None, rates_df=None, balance=1000.0):
        self._positions = positions or []
        self._deals = deals or []
        self._rates = rates_df
        self.balance = balance

    def positions(self, sym=""):
        return [p for p in self._positions if not sym or p.get("symbol") == sym]

    def deals_since(self, since, sym=""):
        return self._deals

    def rates(self, sym, tf, count):
        return self._rates

    def account_info(self):
        return {"balance": self.balance, "equity": self.balance}


def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


@pytest.fixture
def h(monkeypatch, tmp_path):
    """Fake mt5_mcp.* dependencies for one test; records every call."""
    calls = {"open_market": [], "journal": [], "telegram": [], "evaluate": [],
             "gates": [], "stance": [], "notify": []}
    open_res = {"value": FakeRes()}

    def evaluate_strategy(**kw):
        calls["evaluate"].append(kw)
        return FakePlan()

    def market_health(sym, **kw):
        return {"health_score": 80.0}

    def open_market(sym, side, lots, sl, tp, comment=""):
        calls["open_market"].append(
            dict(sym=sym, side=side, lots=lots, sl=sl, tp=tp, comment=comment)
        )
        return open_res["value"]

    class _GateVerdict:
        def __init__(self):
            self.allowed = True
            self.violations = []

    def run_gates(**kw):
        calls["gates"].append(kw)
        return _GateVerdict()

    def publish_stance(source, symbol, side, score):
        calls["stance"].append(dict(source=source, symbol=symbol, side=side, score=score))

    def risk_cap():
        return 0.25

    def trade_filled(**kw):
        calls["notify"].append(kw)

    class _Journal:
        def record_trade(self, **kw):
            calls["journal"].append(kw)
            return 1

    monkeypatch.setitem(
        sys.modules, "mt5_mcp.strategy",
        _mod("mt5_mcp.strategy", evaluate_strategy=evaluate_strategy),
    )
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.market_intel",
        _mod("mt5_mcp.market_intel", market_health=market_health),
    )
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.tools.trades",
        _mod("mt5_mcp.tools.trades", open_market=open_market,
             close_position=lambda ticket, volume=None: FakeRes(ticket=ticket)),
    )
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.memory.trade_journal",
        _mod("mt5_mcp.memory.trade_journal", get_journal=lambda: _Journal()),
    )
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.telegram_alerts",
        _mod(
            "mt5_mcp.telegram_alerts",
            send_telegram=lambda msg: calls["telegram"].append(msg) or True,
            trade_entry_msg=lambda *a, **k: f"entry {a} {k}",
            trade_close_msg=lambda *a, **k: f"close {a} {k}",
            daily_summary_msg=lambda *a, **k: f"summary {a} {k}",
        ),
    )
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.pipeline",
        _mod("mt5_mcp.pipeline", run_gates=run_gates, publish_stance=publish_stance),
    )
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.consensus",
        _mod("mt5_mcp.consensus", risk_cap=risk_cap),
    )
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.notifier",
        _mod("mt5_mcp.notifier", trade_filled=trade_filled),
    )
    monkeypatch.setitem(
        sys.modules, "analysis_agent",
        _mod("analysis_agent", is_available=lambda short, health, **kw: (True, ["ok"])),
    )

    monkeypatch.setattr(dem, "GATE_LOG_PATH", tmp_path / "gate.jsonl")
    monkeypatch.setattr(dem, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(dem, "_STATE", {})
    monkeypatch.setattr(dem, "_seen_positions", {})
    monkeypatch.setattr(dem, "_zone_checked_bucket", {})
    monkeypatch.setattr(dem, "_seen_alert_ids", set())

    return types.SimpleNamespace(
        calls=calls, open_res=open_res, client=FakeClient(), tmp=tmp_path,
    )


def _gate_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ------------------------------------------------------------- env/config
def test_env_symbols_parse(monkeypatch):
    monkeypatch.setenv("CONFIRM_SYMBOLS", " eurusd , usdjpy ,")
    assert dem._env_symbols() == ["EURUSD", "USDJPY"]


def test_env_int_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("CONFIRM_POLL_SEC", "abc")
    assert dem._env_int("CONFIRM_POLL_SEC", 30) == 30


# ------------------------------------------------------------ entry budget
def test_entry_budget_cap_and_rollover(monkeypatch, tmp_path):
    monkeypatch.setattr(dem, "MAX_ENTRIES_24H", 2)
    monkeypatch.setattr(dem, "STATE_PATH", tmp_path / "s.json")
    state = {}
    now = time.time()
    assert dem.entry_budget_ok(state, now)
    dem._note_entry(state, now)
    dem._note_entry(state, now + 1)
    assert not dem.entry_budget_ok(state, now + 2)
    # entries older than 24h roll out of the window
    state["entries"] = [now - 90000]
    assert dem.entry_budget_ok(state, now + 2)
    # 0 disables the cap entirely
    monkeypatch.setattr(dem, "MAX_ENTRIES_24H", 0)
    assert dem.entry_budget_ok(state, now)


# ---------------------------------------------------------------- scanning
def test_scan_writes_gate_log(h):
    n = dem.scan_symbol(h.client, "EURUSD", {})
    assert n == 1
    rows = _gate_rows(dem.GATE_LOG_PATH)
    assert rows[-1]["symbol"] == "EURUSD"
    assert rows[-1]["avail"] is True
    assert rows[-1]["floors"] == {
        "conf": dem.MIN_CONF, "score": dem.MIN_SCORE, "health": dem.MIN_HEALTH,
        "rr": dem.MIN_RR, "zone": dem.MIN_ZONE,
    }


def test_scan_skips_when_position_open(h):
    h.client._positions = [{"ticket": 1, "symbol": "EURUSD"}]
    n = dem.scan_symbol(h.client, "EURUSD", {})
    assert n == 0
    assert h.calls["evaluate"] == []
    assert dem.GATE_LOG_PATH.exists() is False  # not even evaluated


def test_scan_dry_run_never_places(h, monkeypatch):
    monkeypatch.setattr(dem, "DRY_RUN", True)
    state = {}
    n = dem.scan_symbol(h.client, "EURUSD", state)
    assert n == 1
    assert h.calls["open_market"] == []
    assert h.calls["journal"] == []
    assert h.calls["telegram"] == []
    assert state.get("entries") is None  # dry runs do not consume the cap


def test_scan_execute_journals_and_notifies(h, monkeypatch):
    monkeypatch.setattr(dem, "DRY_RUN", False)
    state = {}
    n = dem.scan_symbol(h.client, "EURUSD", state)
    assert n == 1
    assert len(h.calls["open_market"]) == 1
    assert h.calls["open_market"][0]["comment"] == "confirm_exec relaxed"
    j = h.calls["journal"][0]
    assert j["ticket"] == 999
    assert j["setup"] == "confirm_exec"
    assert j["side"] == "BUY"
    assert j["entry"] == pytest.approx(1.1)
    assert state.get("entries"), "fill must count toward the 24h cap"
    assert h.calls["notify"], "fill card must be sent via notifier"


def test_scan_entry_cap_blocks_execution(h, monkeypatch):
    monkeypatch.setattr(dem, "DRY_RUN", False)
    monkeypatch.setattr(dem, "MAX_ENTRIES_24H", 3)
    state = {"entries": [time.time()] * 3}
    n = dem.scan_symbol(h.client, "EURUSD", state)
    assert n == 0
    assert h.calls["open_market"] == []
    assert h.calls["journal"] == []


def test_scan_rejected_fill_not_journaled(h, monkeypatch):
    monkeypatch.setattr(dem, "DRY_RUN", False)
    h.open_res["value"] = FakeRes(accepted=False, error="market closed")
    state = {}
    n = dem.scan_symbol(h.client, "EURUSD", state)
    assert n == 0
    assert h.calls["journal"] == []
    assert state.get("entries") is None


def test_scan_unavailable_plan_gates(h, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "analysis_agent",
        _mod("analysis_agent", is_available=lambda s, h_, **k: (False, ["no signal"])),
    )
    n = dem.scan_symbol(h.client, "EURUSD", {})
    assert n == 0
    assert h.calls["open_market"] == []
    rows = _gate_rows(dem.GATE_LOG_PATH)
    assert rows[-1]["avail"] is False
    assert rows[-1]["reasons"] == ["no signal"]


# ------------------------------------------------------------- compliance
def test_compliance_ok_variants(monkeypatch):
    box = {"mode": "normal", "halt_reason": "", "trades_today": 1, "max_trades_per_day": 5}
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.compliance",
        _mod("mt5_mcp.compliance", evaluate=lambda: dict(box)),
    )
    assert dem._compliance_ok() is True
    box.update(mode="halted", halt_reason="daily loss limit reached")
    assert dem._compliance_ok() is False
    box.update(mode="normal", halt_reason="", trades_today=5, max_trades_per_day=5)
    assert dem._compliance_ok() is False


# ----------------------------------------------------------- alert bridge
def test_pop_forced_symbols_filters_and_dedups(monkeypatch):
    recent = datetime.now(timezone.utc).isoformat()
    old = "2026-01-01T00:00:00+00:00"
    alerts = [
        {"id": "a1", "symbol": "EURUSD", "triggered": 1, "triggered_at": recent},
        {"id": "a2", "symbol": "AUDNZD", "triggered": 1, "triggered_at": old},     # stale
        {"id": "a3", "symbol": "GBPUSD", "triggered": 1, "triggered_at": recent},   # not watched
        {"id": "a4", "symbol": "USDJPY", "triggered": 0, "triggered_at": None},     # not fired
    ]
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.alerts",
        _mod("mt5_mcp.alerts", list_alerts=lambda include_triggered=True: alerts),
    )
    assert dem.pop_forced_symbols() == ["EURUSD"]
    assert dem.pop_forced_symbols() == []  # handled once


# ------------------------------------------------------------ close path
def test_handle_close_journals_and_sends_card(h):
    now = int(time.time())
    seen = {
        "ticket": 42, "symbol": "AUDNZD", "type": 0, "price_open": 1.2392,
        "sl": 1.23514, "tp": 1.2515, "volume": 0.1, "time": now - 600,
        "comment": "confirm_exec relaxed", "profit": -5.0, "price_current": 1.2370,
    }
    client = FakeClient(deals=[
        {"position_id": 42, "entry": 1, "type": 0, "profit": -6.0,
         "swap": -0.1, "fee": 0.0, "price": 1.2360, "time": now},
    ])
    dem._handle_close(client, 42, seen)
    j = h.calls["journal"][0]
    assert j["ticket"] == 42
    assert j["setup"] == "confirm_exec"
    assert j["pnl"] == pytest.approx(-6.1)
    assert j["exit_price"] == pytest.approx(1.2360)
    assert j["outcome"] == "loss"
    assert h.calls["telegram"], "close card must be sent"


def test_handle_close_without_deal_falls_back(h):
    dem._zone_exit_tickets.add(42)
    seen = {
        "ticket": 42, "symbol": "AUDNZD", "type": 0, "price_open": 1.2392,
        "sl": 1.23514, "tp": 1.2515, "volume": 0.1, "time": int(time.time()) - 600,
        "comment": "confirm_exec relaxed", "profit": -5.0, "price_current": 1.2340,
    }
    dem._handle_close(FakeClient(deals=[]), 42, seen)
    j = h.calls["journal"][0]
    assert j["pnl"] == pytest.approx(-5.0)          # last floating tick fallback
    assert j["reason"].startswith("close (fill deal not found")
    assert 42 not in dem._zone_exit_tickets          # consumed


def test_watch_positions_detects_close(h):
    pos = {
        "ticket": 7, "symbol": "EURUSD", "type": 0, "price_open": 1.1000,
        "sl": 1.0950, "tp": 1.1125, "volume": 0.1, "time": int(time.time()) - 600,
        "comment": "confirm_exec relaxed", "profit": 2.0, "price_current": 1.1020,
    }
    client = FakeClient(positions=[pos])
    dem.watch_positions(client)              # seed
    assert 7 in dem._seen_positions
    client._positions = []                   # position vanishes (SL/TP hit)
    dem.watch_positions(client)
    assert h.calls["journal"], "vanished position must be journaled"
    assert h.calls["telegram"], "close card must be sent"
    assert h.calls["journal"][0]["ticket"] == 7


# ------------------------------------------------------------- zone exits
def _h1_frame(closes):
    idx = pd.date_range("2026-09-22 00:00", periods=len(closes), freq="h")
    return pd.DataFrame({
        "time": idx, "open": closes,
        "high": [c + 0.001 for c in closes],
        "low": [c - 0.001 for c in closes],
        "close": closes,
    })


def test_zone_floor_broken_buy(h, monkeypatch):
    zone = types.SimpleNamespace(type="demand", lower=1.23514, upper=1.23901, strength=80.4)
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.supply_demand",
        _mod("mt5_mcp.supply_demand", detect_zones=lambda df, **kw: [zone]),
    )
    pos = {"symbol": "AUDNZD", "type": 0, "price_open": 1.2392}
    # last CLOSED bar (iloc[-2]) closes below the demand floor -> broken
    closes = [1.2400] * 60
    closes[-2] = 1.2340
    h.client._rates = _h1_frame(closes)
    assert dem._zone_floor_broken(h.client, pos) is True
    # last closed bar holds above the floor -> intact
    closes[-2] = 1.2360
    h.client._rates = _h1_frame(closes)
    assert dem._zone_floor_broken(h.client, pos) is False
    # too-weak zone is ignored
    weak = types.SimpleNamespace(type="demand", lower=1.23514, upper=1.23901, strength=40.0)
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.supply_demand",
        _mod("mt5_mcp.supply_demand", detect_zones=lambda df, **kw: [weak]),
    )
    assert dem._zone_floor_broken(h.client, pos) is False


def test_zone_ceiling_broken_sell(h, monkeypatch):
    zone = types.SimpleNamespace(type="supply", lower=1.2500, upper=1.2550, strength=75.0)
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.supply_demand",
        _mod("mt5_mcp.supply_demand", detect_zones=lambda df, **kw: [zone]),
    )
    pos = {"symbol": "EURUSD", "type": 1, "price_open": 1.2520}
    closes = [1.2510] * 60
    closes[-2] = 1.2560  # close above supply ceiling -> broken
    h.client._rates = _h1_frame(closes)
    assert dem._zone_floor_broken(h.client, pos) is True
    closes[-2] = 1.2540
    h.client._rates = _h1_frame(closes)
    assert dem._zone_floor_broken(h.client, pos) is False


def test_watch_positions_zone_exit_wires_ticket(h, monkeypatch):
    monkeypatch.setattr(dem, "ZONE_EXIT", True)
    zone = types.SimpleNamespace(type="demand", lower=1.23514, upper=1.23901, strength=80.4)
    monkeypatch.setitem(
        sys.modules, "mt5_mcp.supply_demand",
        _mod("mt5_mcp.supply_demand", detect_zones=lambda df, **kw: [zone]),
    )
    closes = [1.2400] * 60
    closes[-2] = 1.2340
    h.client._rates = _h1_frame(closes)
    pos = {
        "ticket": 55, "symbol": "AUDNZD", "type": 0, "price_open": 1.2392,
        "sl": 1.23514, "tp": 1.2515, "volume": 0.1, "time": int(time.time()) - 600,
        "comment": "confirm_exec relaxed", "profit": -5.0, "price_current": 1.2340,
    }
    h.client._positions = [pos]
    dem.watch_positions(h.client)
    assert 55 in dem._zone_exit_tickets  # close accepted -> flagged for the card


# ---------------------------------------------------------- daily summary
def test_daily_summary_sends_once_per_day(h, monkeypatch):
    monkeypatch.setattr(dem, "SUMMARY_HOUR_UTC", 0)
    client = FakeClient(deals=[
        {"position_id": 1, "entry": 1, "type": 0, "profit": 10.0, "swap": 0.0, "fee": 0.0},
        {"position_id": 2, "entry": 1, "type": 1, "profit": -4.0, "swap": 0.0, "fee": 0.0},
    ])
    state = {}
    dem.maybe_daily_summary(client, state)
    assert len(h.calls["telegram"]) == 1
    assert state.get("summary_date")
    # balance, equity, closed, wins, losses, pnl_today, open_positions
    assert "(1000.0, 1000.0, 2, 1, 1, 6.0, 0)" in str(h.calls["telegram"][0])
    dem.maybe_daily_summary(client, state)
    assert len(h.calls["telegram"]) == 1  # not repeated




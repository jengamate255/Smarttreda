"""Tests for the web dashboard API using a mocked MetaTrader5 module.

Run:  pytest test_web_dashboard.py -v
"""

import json
import time
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import web_dashboard


class FakeMT5:
    """Drop-in stand-in for the MetaTrader5 module."""

    import MetaTrader5 as _real

    TIMEFRAME_M1 = _real.TIMEFRAME_M1
    TIMEFRAME_M5 = _real.TIMEFRAME_M5
    TIMEFRAME_M15 = _real.TIMEFRAME_M15
    TIMEFRAME_M30 = _real.TIMEFRAME_M30
    TIMEFRAME_H1 = _real.TIMEFRAME_H1
    TIMEFRAME_H4 = _real.TIMEFRAME_H4
    TIMEFRAME_D1 = _real.TIMEFRAME_D1
    TIMEFRAME_W1 = _real.TIMEFRAME_W1
    DEAL_ENTRY_OUT = _real.DEAL_ENTRY_OUT
    DEAL_ENTRY_IN = _real.DEAL_ENTRY_IN
    DEAL_TYPE_BUY = _real.DEAL_TYPE_BUY
    DEAL_TYPE_SELL = _real.DEAL_TYPE_SELL
    DEAL_TYPE_BALANCE = _real.DEAL_TYPE_BALANCE
    ORDER_TYPE_BUY = _real.ORDER_TYPE_BUY
    ORDER_TYPE_SELL = _real.ORDER_TYPE_SELL
    SYMBOL_TRADE_MODE_FULL = _real.SYMBOL_TRADE_MODE_FULL
    SYMBOL_TRADE_MODE_LONGONLY = _real.SYMBOL_TRADE_MODE_LONGONLY

    def __init__(self):
        self.n_bars = 300
        self.rates_calls = 0
        self._build_rates()
        now = int(time.time())
        self.deals = [
            SimpleNamespace(
                time=now - 3600 * i,
                entry=self.DEAL_ENTRY_OUT,
                type=self.DEAL_TYPE_BUY,
                symbol="EURUSD",
                volume=0.1,
                price=1.1000,
                profit=p,
                swap=0.0,
                fee=0.0,
                comment="test",
            )
            for i, p in enumerate([120.0, -60.0, 40.0, -20.0, 80.0])
        ]

    def _build_rates(self):
        rng = np.random.default_rng(42)
        n = self.n_bars
        ret = rng.normal(0.0002, 0.0008, n).cumsum()
        close = 1.10 + ret
        opens = np.roll(close, 1)
        opens[0] = close[0]
        highs = np.maximum(opens, close) + rng.uniform(0.0, 0.0005, n)
        lows = np.minimum(opens, close) - rng.uniform(0.0, 0.0005, n)
        times = 1750000000 + np.arange(n, dtype=np.int64) * 3600
        self.rates = np.zeros(
            n,
            dtype=[
                ("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8"),
            ],
        )
        self.rates["time"] = times
        self.rates["open"] = opens
        self.rates["high"] = highs
        self.rates["low"] = lows
        self.rates["close"] = close
        self.rates["tick_volume"] = rng.integers(100, 500, n)
        self.rates["spread"] = 2
        self.rates["real_volume"] = 0

    def account_info(self):
        return SimpleNamespace(
            login=11911448, balance=10000.0, equity=10100.0, margin=100.0,
            margin_free=9900.0, margin_level=10100.0, currency="USD", leverage=100,
        )

    def positions_get(self, symbol=None):
        return [
            SimpleNamespace(
                ticket=1001, symbol="EURUSD", type=self.ORDER_TYPE_BUY, volume=0.1,
                price_open=1.1000, sl=1.0950, tp=1.1200, price_current=1.1050,
                profit=50.0, swap=-1.0, comment="",
            ),
            SimpleNamespace(
                ticket=1002, symbol="XAUUSD", type=self.ORDER_TYPE_SELL, volume=0.5,
                price_open=2000.0, sl=0.0, tp=0.0, price_current=2010.0,
                profit=-50.0, swap=0.0, comment="",
            ),
        ]

    def history_deals_get(self, from_dt=None, to_dt=None, group=None):
        if from_dt is None:
            return self.deals
        cutoff = int(from_dt) if isinstance(from_dt, int) else int(from_dt.timestamp())
        return [d for d in self.deals if d.time >= cutoff]

    def copy_rates_from_pos(self, symbol, timeframe, pos, count):
        if symbol != "EURUSD":
            return None
        self.rates_calls += 1
        return self.rates[:count]

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(
            time=1750000000 + self.n_bars * 3600, bid=1.1050, ask=1.1052,
            last=1.1051, volume=1000, flags=0,
        )

    def symbol_info(self, symbol):
        return SimpleNamespace(point=0.00001, name=symbol, trade_mode=self.SYMBOL_TRADE_MODE_FULL)

    def terminal_info(self):
        return SimpleNamespace(
            company="FundingPips", build=4025, connected=True, name="MetaTrader 5 Terminal",
        )

    def last_error(self):
        return (0, "OK")

    def initialize(self, path=""):
        return True

    def shutdown(self):
        return None


class FakeConnection:
    def __init__(self):
        self.connected = True

    def connect(self, config, attempts=1, delay=0.0):
        return True

    def reconnect(self):
        return True

    def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(web_dashboard, "mt5", FakeMT5())
    monkeypatch.setattr(web_dashboard, "mt5_connection", FakeConnection())
    web_dashboard._rates_cache.clear()
    web_dashboard._bot_cache.clear()
    web_dashboard._tradeplan_cache.clear()
    with TestClient(web_dashboard.app) as c:
        yield c


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["connected"] is True
    assert data["terminal"]["company"] == "FundingPips"
    assert data["terminal"]["build"] == 4025


def test_health_disconnected(client):
    web_dashboard.mt5_connection.connected = False
    res = client.get("/api/health")
    assert res.json()["connected"] is False


def test_rates_disconnected_503(client):
    web_dashboard.mt5_connection.connected = False
    res = client.get("/api/rates?symbol=EURUSD&timeframe=H1&count=300")
    assert res.status_code == 503


def test_account(client):
    res = client.get("/api/account")
    assert res.status_code == 200
    data = res.json()
    assert data["login"] == 11911448
    assert data["balance"] == 10000.0
    assert data["floating_pnl"] == -1.0  # 50 - 1 + (-50) + 0
    assert data["daily_pnl"] == data["daily_realized"] + data["floating_pnl"]


def test_positions(client):
    res = client.get("/api/positions")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 2
    assert data["positions"][0]["symbol"] == "EURUSD"  # sorted by symbol
    assert data["positions"][0]["type"] == "BUY"
    assert data["positions"][0]["total"] == 49.0


def test_history_stats(client):
    res = client.get("/api/history?days=30")
    assert res.status_code == 200
    stats = res.json()["stats"]
    assert stats["trades"] == 5
    assert stats["wins"] == 3
    assert stats["losses"] == 2
    assert stats["win_rate"] == 60.0
    assert stats["gross_profit"] == 240.0
    assert stats["gross_loss"] == 80.0
    assert stats["profit_factor"] == 3.0
    assert len(res.json()["recent"]) == 5


def test_rates(client):
    res = client.get("/api/rates?symbol=EURUSD&timeframe=H1&count=300")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 300
    assert len(data["close"]) == 300
    assert len(data["ema50"]) == 300
    assert len(data["rsi"]) == 300
    assert len(data["macd_hist"]) == 300
    assert data["tick"]["spread"] == 20  # (ask - bid) / point = 0.0002 / 0.00001


def test_rates_cached(client):
    web_dashboard._rates_cache.clear()
    mt5_fake = web_dashboard.mt5
    first = client.get("/api/rates?symbol=EURUSD&timeframe=H1&count=300").json()
    calls_after_first = mt5_fake.rates_calls
    second = client.get("/api/rates?symbol=EURUSD&timeframe=H1&count=300").json()
    assert first == second
    assert mt5_fake.rates_calls == calls_after_first  # served from cache


def test_rates_invalid_timeframe(client):
    res = client.get("/api/rates?timeframe=XYZ")
    assert res.status_code == 400


def test_rates_unknown_symbol(client):
    res = client.get("/api/rates?symbol=NOEXIST")
    assert res.status_code == 404


def test_bot_analysis(client):
    res = client.get("/api/bot-analysis?symbol=EURUSD&timeframe=H1&count=300")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 300
    assert data["trend"] is not None
    assert data["trend"]["direction"] in ("bullish", "bearish", "sideways")
    assert isinstance(data["zones"], list)
    assert "highs" in data["swings"] and "lows" in data["swings"]
    assert isinstance(data["patterns"], list)


def test_bot_analysis_cached(client):
    web_dashboard._bot_cache.clear()
    first = client.get("/api/bot-analysis?symbol=EURUSD&timeframe=H1&count=300").json()
    second = client.get("/api/bot-analysis?symbol=EURUSD&timeframe=H1&count=300").json()
    assert first == second


def test_trade_plan(client, monkeypatch):
    from common import TradePlan

    calls = []

    def fake_evaluate(**kwargs):
        calls.append(kwargs)
        return TradePlan(
            action="BUY", symbol="EURUSD", timeframe="H1", valid=True,
            confidence=71.5, trade_score=68.0, entry=1.1050, sl=1.0990,
            tp=1.1210, sl_pips=60.0, tp_pips=160.0, risk_reward=2.67,
            lots=0.15, risk_amount=7.5, htf_trend="bullish",
            confirmation="bullish_engulfing (bullish, conf 0.80)",
            zone_type="demand", zone_strength=72.0,
            reason="HTF trend bullish; demand zone 1.0980-1.1020; confirmation bullish_engulfing",
        )

    monkeypatch.setattr(web_dashboard, "evaluate_strategy", fake_evaluate)
    res = client.get("/api/trade-plan?symbol=EURUSD&timeframe=H1&lookback=300")
    assert res.status_code == 200
    plan = res.json()
    assert plan["action"] == "BUY"
    assert plan["valid"] is True
    assert plan["entry"] == 1.1050
    assert plan["lots"] == 0.15
    assert calls[0]["confirmation_timeframe"] == "H1"
    assert calls[0]["trend_timeframe"] == "H4"


def test_trade_plan_cached(client, monkeypatch):
    from common import TradePlan

    calls = []

    def fake_evaluate(**kwargs):
        calls.append(kwargs)
        return TradePlan(action="NONE", symbol="EURUSD")

    monkeypatch.setattr(web_dashboard, "evaluate_strategy", fake_evaluate)
    client.get("/api/trade-plan?symbol=EURUSD&timeframe=H1")
    client.get("/api/trade-plan?symbol=EURUSD&timeframe=H1")
    assert len(calls) == 1


def test_trade_plan_invalid_timeframe(client):
    res = client.get("/api/trade-plan?timeframe=XYZ")
    assert res.status_code == 400


def test_equity(client):
    res = client.get("/api/equity?days=30")
    assert res.status_code == 200
    data = res.json()
    assert data["dates"]  # 5 deals, all same day -> one date
    assert data["total"] == 160.0  # 120 - 60 + 40 - 20 + 80
    assert data["daily_pnl"] == [160.0]
    assert data["balance"] == [10000.0]  # anchored to current balance
    assert data["best_day"] == 160.0
    assert data["worst_day"] == 160.0


def test_journal(client, monkeypatch):
    class FakeJournal:
        def __init__(self):
            self.entries = [
                {
                    "ticket": 1, "symbol": "EURUSD", "side": "BUY",
                    "setup": "supply_demand", "entry": 1.1000, "exit_price": 1.1100,
                    "lots": 0.1, "pnl": 100.0, "outcome": "win",
                    "close_time": "2026-07-30T12:00:00+00:00",
                    "reason": "zone retest", "mistakes": None, "lessons": None,
                }
            ]

        def list_trades(self, limit=100):
            return self.entries[:limit]

        def sync_from_mt5(self, client_):
            return {"added": 0, "updated": 0}

    monkeypatch.setattr(web_dashboard, "get_journal", lambda: FakeJournal())
    res = client.get("/api/journal?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert data["entries"][0]["outcome"] == "win"
    assert data["entries"][0]["pnl"] == 100.0


def test_ws_ping_pong(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_text("ping")
        msg = ws.receive_json()
        assert msg == {"type": "pong"}


def test_index_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "MT5 Dashboard" in res.text


def test_quote(client):
    res = client.get("/api/quote?symbol=EURUSD")
    assert res.status_code == 200
    data = res.json()
    assert data["bid"] == 1.1050
    assert data["ask"] == 1.1052
    assert data["spread"] == 20  # 0.0002 / 0.00001 point


def test_news(client, monkeypatch):
    monkeypatch.setattr(
        web_dashboard,
        "news_filter",
        lambda horizon_hours=24: {
            "provider": "ForexFactoryNewsProvider",
            "events": [
                {
                    "time": __import__("datetime").datetime(2026, 7, 31, 12, 0, tzinfo=__import__("datetime").timezone.utc),
                    "currency": "USD",
                    "impact": "high",
                    "title": "NFP",
                    "forecast": "200K",
                    "previous": "180K",
                }
            ],
            "high_impact_count": 1,
            "high_impact_imminent": True,
            "imminent_events": [{"currency": "USD", "title": "NFP"}],
            "trading_blocked": True,
        },
    )
    res = client.get("/api/news?horizon_hours=24")
    assert res.status_code == 200
    data = res.json()
    assert data["trading_blocked"] is True
    assert data["events"][0]["impact"] == "high"
    assert data["events"][0]["time"] == "2026-07-31 12:00"


def test_risk(client, monkeypatch):
    monkeypatch.setattr(
        web_dashboard,
        "risk_report",
        lambda: {
            "equity": 10000.0, "daily_pnl": -50.0, "daily_limit": 200.0,
            "daily_loss_percent_used": 25.0, "weekly_pnl": -80.0,
            "weekly_limit": 400.0, "weekly_loss_percent_used": 20.0,
            "open_risk": 30.0, "max_open_risk": 150.0,
            "open_risk_percent_used": 20.0, "open_positions": 2,
            "max_positions": 5, "total_exposure": 60000.0,
            "warnings": [], "trading_allowed": True,
        },
    )
    res = client.get("/api/risk")
    assert res.status_code == 200
    data = res.json()
    assert data["trading_allowed"] is True
    assert data["daily_loss_percent_used"] == 25.0


def test_sessions(client, monkeypatch):
    monkeypatch.setattr(web_dashboard, "best_sessions", lambda: [{"hour": 8, "net_pnl": 150.0, "win_rate": 60.0}])
    monkeypatch.setattr(web_dashboard, "best_setups", lambda min_trades=1: [{"setup": "supply_retest", "net_pnl": 500.0, "win_rate": 70.0}])
    monkeypatch.setattr(web_dashboard, "best_pairs", lambda min_trades=1: [{"symbol": "XAUUSD", "net_pnl": 300.0, "win_rate": 65.0}])
    res = client.get("/api/sessions")
    assert res.status_code == 200
    data = res.json()
    assert data["sessions"][0]["hour"] == 8
    assert data["setups"][0]["setup"] == "supply_retest"
    assert data["pairs"][0]["symbol"] == "XAUUSD"


def _plan_buy():
    from common import TradePlan

    return TradePlan(
        action="BUY", symbol="EURUSD", timeframe="H1", valid=True,
        confidence=71.5, trade_score=68.0, entry=1.1050, sl=1.0990,
        tp=1.1210, lots=0.15, risk_amount=7.5, zone_type="demand",
        zone_upper=1.1020, zone_lower=1.0980,
        reason="HTF trend bullish; demand zone retest",
    )


def test_execute_plan(client, monkeypatch):
    from common import TradeResult

    monkeypatch.setattr(web_dashboard, "evaluate_strategy", lambda **k: _plan_buy())
    monkeypatch.setattr(
        web_dashboard,
        "risk_report",
        lambda: {"trading_allowed": True, "warnings": []},
    )
    sent = {}

    def fake_open_market(symbol, side, lots, sl=None, tp=None, comment=""):
        sent.update(symbol=symbol, side=side, lots=lots, sl=sl, tp=tp)
        return TradeResult(accepted=True, ticket=4242, checks=["ok"])

    monkeypatch.setattr(web_dashboard, "open_market", fake_open_market)
    res = client.post("/api/execute-plan", json={"symbol": "EURUSD", "timeframe": "H1"})
    assert res.status_code == 200
    data = res.json()
    assert data["accepted"] is True
    assert data["ticket"] == 4242
    assert sent == {"symbol": "EURUSD", "side": "BUY", "lots": 0.15, "sl": 1.0990, "tp": 1.1210}


def test_execute_plan_blocked_by_risk(client, monkeypatch):
    monkeypatch.setattr(web_dashboard, "evaluate_strategy", lambda **k: _plan_buy())
    monkeypatch.setattr(
        web_dashboard,
        "risk_report",
        lambda: {"trading_allowed": False, "warnings": ["DAILY LOSS LIMIT hit"]},
    )
    res = client.post("/api/execute-plan", json={"symbol": "EURUSD", "timeframe": "H1"})
    assert res.status_code == 400
    assert "risk limits" in res.json()["detail"].lower()


def test_execute_plan_no_setup(client, monkeypatch):
    from common import TradePlan

    monkeypatch.setattr(web_dashboard, "evaluate_strategy", lambda **k: TradePlan(action="NONE", symbol="EURUSD"))
    res = client.post("/api/execute-plan", json={"symbol": "EURUSD", "timeframe": "H1"})
    assert res.status_code == 400
    assert "no valid setup" in res.json()["detail"].lower()


def test_position_sltp(client, monkeypatch):
    from common import TradeResult

    monkeypatch.setattr(
        web_dashboard,
        "modify_sl_tp",
        lambda ticket, sl=None, tp=None: TradeResult(accepted=True, ticket=ticket),
    )
    res = client.post("/api/positions/1001/sltp", json={"sl": 1.0900, "tp": 1.1300})
    assert res.status_code == 200
    assert res.json()["accepted"] is True

    res = client.post("/api/positions/1001/sltp", json={})
    assert res.status_code == 400


def test_position_close(client, monkeypatch):
    from common import TradeResult

    monkeypatch.setattr(web_dashboard, "close_position", lambda ticket, volume=None: TradeResult(accepted=True, ticket=ticket))
    res = client.post("/api/positions/1001/close")
    assert res.status_code == 200
    assert res.json()["ticket"] == 1001


def test_journal_notes(client, monkeypatch):
    notes = []

    class FakeJournal:
        def list_trades(self, limit=100):
            return [{"ticket": 1, "symbol": "EURUSD", "side": "BUY", "setup": "manual", "pnl": 10.0, "outcome": "win"}]

        def update_notes(self, ticket, **fields):
            notes.append((ticket, fields))
            return True

    monkeypatch.setattr(web_dashboard, "get_journal", lambda: FakeJournal())
    res = client.post("/api/journal/1/notes", json={"reason": "zone retest", "lessons": "wait for confirmation"})
    assert res.status_code == 200
    assert notes == [(1, {"reason": "zone retest", "lessons": "wait for confirmation"})]


def test_journal_notes_not_found(client, monkeypatch):
    class FakeJournal:
        def update_notes(self, ticket, **fields):
            return False

    monkeypatch.setattr(web_dashboard, "get_journal", lambda: FakeJournal())
    res = client.post("/api/journal/999/notes", json={"reason": "x"})
    assert res.status_code == 404

# ---------------- Backtesting (blended from mt5_mcp backtest tooling) ----------------


def test_backtests_list(client):
    res = client.get("/api/backtests")
    assert res.status_code == 200
    data = res.json()
    assert set(data["groups"]) == {"month", "forward", "all"}
    assert data["groups"]["month"], "month dir should contain result rows"
    row = data["groups"]["month"][0]
    for key in ("symbol", "file", "dir", "trades", "win_rate", "profit_factor", "net_pnl", "max_drawdown_pct"):
        assert key in row
    assert "mc" in data
    assert data["mc"]["prob_positive_month"] >= 0.0
    assert data["generated"]


def test_backtest_detail(client):
    res = client.get("/api/backtest/run", params={"name": "month/AUDUSD_H4_H1+H4_z40_rr3_tp80-100.json"})
    assert res.status_code == 200
    data = res.json()
    assert data["params"]["symbol"] == "AUDUSD"
    assert isinstance(data["n_trades"], int)
    assert isinstance(data["trades"], list)
    assert isinstance(data["equity"], list)
    assert data["equity"][0]["label"] == "start"


def test_backtest_detail_missing(client):
    res = client.get("/api/backtest/run?name=month/NOPE.json")
    assert res.status_code == 404


def test_backtest_detail_traversal(client):
    res = client.get("/api/backtest/run?name=../web_dashboard.py")
    assert res.status_code == 404


def test_backtest_run_validation(client):
    res = client.post("/api/backtest/run", json={})
    assert res.status_code == 400
    assert "symbol" in res.json()["detail"].lower()

    res = client.post("/api/backtest/run", json={"symbol": "XAUUSD", "bars": 50})
    assert res.status_code == 400

    res = client.post("/api/backtest/run", json={"symbol": "XAUUSD", "since": "not-a-date"})
    assert res.status_code == 400

    res = client.post("/api/backtest/forward", json={"symbol": "XAUUSD"})
    assert res.status_code == 400


class FakeBtClient:
    def symbol_info(self, symbol):
        return {"point": 0.001, "name": symbol, "digits": 3}


def fake_bt_frames(symbol, tfs, bars):
    idx = pd.date_range("2026-06-01", periods=bars, freq="h")
    rng = np.random.default_rng(7)
    close = 2000 + rng.normal(0, 2, bars).cumsum()
    opens = np.roll(close, 1)
    opens[0] = close[0]
    highs = np.maximum(opens, close) + 1
    lows = np.minimum(opens, close) - 1
    return {
        tf: pd.DataFrame({"time": idx, "open": opens, "high": highs, "low": lows, "close": close})
        for tf in tfs
    }


class FakeBt:
    def __init__(self, params, frames, info):
        self.params = params

    def run(self):
        return web_dashboard.BacktestResult(
            params=asdict(self.params),
            n_trades=2,
            wins=1,
            losses=1,
            gross_profit=100.0,
            gross_loss=50.0,
            net_pnl=50.0,
            profit_factor=2.0,
            win_rate=50.0,
            expectancy=25.0,
            total_return_pct=0.5,
            max_drawdown_pct=1.0,
            avg_rr=3.0,
            start="2026-07-01 00:00:00",
            end="2026-07-31 23:00:00",
            trades=[
                {"entry_time": "2026-07-01 09:00:00", "exit_time": "2026-07-01 15:00:00",
                 "action": "BUY", "entry": 2000.0, "sl": 1990.0, "tp": 2100.0, "lots": 0.1,
                 "sl_pips": 10.0, "tp_pips": 100.0, "rr": 10.0, "exit_price": 2100.0,
                 "pnl": 100.0, "reason": "TP", "trend_confidence": 0.8, "zone_strength": 80.0,
                 "confirmation": "engulfing (bullish)", "ml_score": 0.0},
                {"entry_time": "2026-07-08 09:00:00", "exit_time": "2026-07-08 11:00:00",
                 "action": "BUY", "entry": 2010.0, "sl": 2000.0, "tp": 2110.0, "lots": 0.1,
                 "sl_pips": 10.0, "tp_pips": 100.0, "rr": 10.0, "exit_price": 2000.0,
                 "pnl": -50.0, "reason": "SL", "trend_confidence": 0.8, "zone_strength": 80.0,
                 "confirmation": "pin bar (bullish)", "ml_score": 0.0},
            ],
            errors=[],
        )


def test_backtest_run(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web_dashboard, "load_frames", fake_bt_frames)
    monkeypatch.setattr(web_dashboard, "Backtester", FakeBt)
    monkeypatch.setattr(web_dashboard, "get_client", lambda: FakeBtClient())
    monkeypatch.setattr(web_dashboard, "_BT_MONTH", str(tmp_path / "month"))

    res = client.post("/api/backtest/run", json={"symbol": "XAUUSD", "bars": 500})
    assert res.status_code == 200
    data = res.json()
    assert data["n_trades"] == 2
    assert data["net_pnl"] == 50.0
    assert data["elapsed_s"] >= 0
    assert len(data["equity"]) == 3
    assert data["saved"] == "month/XAUUSD_H4_H1+H4_z40_rr3_tp80-100.json"
    assert (tmp_path / "month" / "summary.csv").is_file()


def test_backtest_run_windowed_since(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web_dashboard, "load_frames", fake_bt_frames)
    monkeypatch.setattr(web_dashboard, "Backtester", FakeBt)
    monkeypatch.setattr(web_dashboard, "get_client", lambda: FakeBtClient())
    monkeypatch.setattr(web_dashboard, "_BT_MONTH", str(tmp_path / "month"))
    monkeypatch.setattr(web_dashboard, "_BT_FORWARD", str(tmp_path / "forward"))

    res = client.post("/api/backtest/run", json={"symbol": "XAUUSD", "since": "2026-07-01", "bars": 500})
    assert res.status_code == 200
    assert res.json()["saved"].startswith("forward/")


def test_backtest_forward(client, monkeypatch, tmp_path):
    monkeypatch.setattr(web_dashboard, "load_frames", fake_bt_frames)
    monkeypatch.setattr(web_dashboard, "Backtester", FakeBt)
    monkeypatch.setattr(web_dashboard, "get_client", lambda: FakeBtClient())
    monkeypatch.setattr(web_dashboard, "_BT_FORWARD", str(tmp_path / "forward"))

    res = client.post("/api/backtest/forward", json={"symbol": "XAUUSD", "since": "2026-07-01", "bars": 500})
    assert res.status_code == 200
    data = res.json()
    assert set(data) == {"windowed", "full", "elapsed_s"}
    assert data["windowed"]["n_trades"] == 2
    assert data["full"]["n_trades"] == 2


def test_backtest_trades_overlay(client, monkeypatch, tmp_path):
    from pathlib import Path
    results = Path(tmp_path) / "results"
    month = results / "month"
    month.mkdir(parents=True)
    (results / "results.json").write_text(
        '{"n_trades": 0, "params": {"symbol": "EURUSD", "trend_timeframe": "H4"}, "trades": []}',
        encoding="utf-8",
    )
    (month / "test.json").write_text(
        json.dumps({
            "params": {"symbol": "EURUSD", "trend_timeframe": "H4", "zone_timeframes": ["H1", "H4"]},
            "trades": [{
                "entry_time": "2026-01-05T08:00:00",
                "exit_time": "2026-01-05T12:00:00",
                "action": "SELL",
                "entry": 1.1000, "sl": 1.1200, "tp": 1.0600,
                "exit_price": 1.0590, "pnl": 82.0, "reason": "tp", "rr": 2.0,
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_dashboard, "_BT_RESULTS", str(results))

    res = client.get("/api/backtest/trades?name=month%2Ftest.json")
    assert res.status_code == 200
    data = res.json()
    assert data["symbol"] == "EURUSD"
    t = data["trades"][0]
    assert t["action"] == "SELL"
    assert t["pnl"] == 82.0
    assert t["entry_ts"] == int(pd.Timestamp("2026-01-05T08:00:00").tz_localize("UTC").timestamp())
    assert t["exit_ts"] == int(pd.Timestamp("2026-01-05T12:00:00").tz_localize("UTC").timestamp())

    res = client.get("/api/backtest/trades?name=month%2Fmissing.json")
    assert res.status_code == 404

    res = client.get("/api/backtest/trades?name=..%2F..%2Fsettings.json")
    assert res.status_code == 404

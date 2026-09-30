"""Tests for the prop firm compliance agent: veto gate and mode tiers."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from common import NewsEvent
from compliance import evaluate, gate
from tools.news import StubNewsProvider, get_provider, set_provider

MAGIC = 20260601


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    import compliance

    monkeypatch.setattr(compliance, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(compliance, "_record_event", lambda kind, message: None)
    original_provider = get_provider()
    set_provider(StubNewsProvider())
    yield
    set_provider(original_provider)


@pytest.fixture
def com_client(fake_client, client_module, monkeypatch):
    client_module.set_client(fake_client)
    return fake_client


def _deal(
    deal_type=0,
    entry=1,
    profit=0.0,
    position_id=1,
    deal_time=1_000_000,
    symbol="EURUSD",
    swap=0.0,
    fee=0.0,
):
    return {
        "type": deal_type,
        "entry": entry,
        "profit": profit,
        "swap": swap,
        "fee": fee,
        "time": deal_time,
        "position_id": position_id,
        "symbol": symbol,
    }


def _losses(n: int, pnl: float = -20.0) -> list[dict]:
    return [
        _deal(
            deal_type=0,
            entry=1,
            profit=pnl,
            position_id=i,
            deal_time=2_000_000 + i,
        )
        for i in range(n)
    ]


class TestGateBasics:
    def test_allows_compliant_trade(self, com_client):
        verdict = gate("EURUSD", "BUY", 0.05, sl_price=1.19500)
        assert verdict["allowed"] is True
        assert verdict["mode"] == "normal"

    def test_rejects_excess_risk(self, com_client):
        verdict = gate("EURUSD", "BUY", 0.5, sl_price=1.19500)
        assert verdict["allowed"] is False
        assert any("exceeds cap" in v for v in verdict["violations"])

    def test_requires_stop_loss(self, com_client):
        verdict = gate("EURUSD", "BUY", 0.1)
        assert verdict["allowed"] is False
        assert any("stop-loss required" in v for v in verdict["violations"])

    def test_unknown_symbol_rejected(self, com_client, monkeypatch):
        # FakeClient answers symbol_info for any string (there is no real symbol
        # universe in tests), so stub the unknown one -- the gate must reject
        # whenever symbol_info comes back empty, as live MT5 does for bad symbols.
        real = com_client.symbol_info
        monkeypatch.setattr(
            com_client,
            "symbol_info",
            lambda s: None if s == "NOTREAL" else real(s),
        )
        verdict = gate("NOTREAL", "BUY", 0.1, sl_price=1.19500)
        assert verdict["allowed"] is False
        assert any("not found" in v for v in verdict["violations"])

    def test_floating_risk_cap(self, com_client):
        com_client._positions.append(
            {
                "ticket": 1,
                "symbol": "EURUSD",
                "type": 0,
                "volume": 0.09,
                "price_open": 1.19500,
                "sl": 1.19000,
            }
        )
        verdict = gate("EURUSD", "BUY", 0.2, sl_price=1.19500)
        assert verdict["allowed"] is False
        assert any("floating risk" in v for v in verdict["violations"])

    def test_correlation_cap(self, com_client):
        com_client._positions.append(
            {
                "ticket": 1,
                "symbol": "GBPUSD",
                "type": 0,
                "volume": 0.09,
                "price_open": 1.19500,
                "sl": 1.19000,
            }
        )
        verdict = gate("EURUSD", "BUY", 0.09, sl_price=1.19500)
        assert verdict["allowed"] is False
        assert any("correlated exposure" in v for v in verdict["violations"])

    def test_trade_frequency_cap(self, com_client):
        com_client.deals_history = [_deal(entry=0) for _ in range(3)]
        verdict = gate("EURUSD", "BUY", 0.05, sl_price=1.19500)
        assert verdict["allowed"] is False
        assert any("daily trade limit" in v for v in verdict["violations"])

    def test_confidence_floor(self, com_client):
        verdict = gate("EURUSD", "BUY", 0.05, sl_price=1.19500, confidence=80)
        assert verdict["allowed"] is False
        assert any("below floor" in v for v in verdict["violations"])


class TestRevengeProtection:
    def test_pause_after_consecutive_losses(self, com_client):
        com_client.deals_history = _losses(3)
        first = gate("EURUSD", "BUY", 0.05, sl_price=1.19500)
        assert first["allowed"] is False
        assert any("revenge pause" in v for v in first["violations"])

    def test_resume_after_cooldown(self, com_client, tmp_path):
        import compliance

        com_client.deals_history = _losses(3)
        gate("EURUSD", "BUY", 0.05, sl_price=1.19500)
        state = compliance._load_state(compliance.STATE_PATH)
        state["revenge_paused_until"] = time.time() - 1.0
        compliance._save_state(compliance.STATE_PATH, state)
        verdict = gate("EURUSD", "BUY", 0.05, sl_price=1.19500)
        assert verdict["allowed"] is True

    def test_win_clears_pause(self, com_client, tmp_path):

        com_client.deals_history = _losses(3)
        gate("EURUSD", "BUY", 0.05, sl_price=1.19500)
        com_client.deals_history = _losses(3) + [
            _deal(entry=1, profit=30.0, position_id=99, deal_time=9_000_000)
        ]
        verdict = gate("EURUSD", "BUY", 0.05, sl_price=1.19500)
        assert verdict["allowed"] is True


class TestModes:
    def test_daily_loss_halt_cancels_pendings(self, com_client):
        com_client._pending = [{"ticket": 55, "magic": MAGIC}]
        com_client.deals_history = [
            _deal(entry=1, profit=-300.0, position_id=1, deal_time=2_000_000)
        ]
        status = evaluate()
        assert status["mode"] == "halted"
        assert status["status"] == "RED"
        assert any(r["action"] == 8 for r in com_client.requests)
        verdict = gate("EURUSD", "BUY", 0.05, sl_price=1.19500)
        assert verdict["allowed"] is False
        assert "halted" in " ".join(verdict["violations"])

    def test_daily_loss_80pct_conservative(self, com_client):
        com_client.deals_history = [
            _deal(entry=1, profit=-240.0, position_id=1, deal_time=2_000_000)
        ]
        status = evaluate()
        assert status["mode"] == "conservative"
        assert status["status"] == "YELLOW"
        verdict = gate("EURUSD", "BUY", 0.1, sl_price=1.19500)
        assert verdict["allowed"] is False
        assert any("exceeds cap" in v for v in verdict["violations"])
        verdict_small = gate("EURUSD", "BUY", 0.04, sl_price=1.19500)
        assert verdict_small["allowed"] is True

    def test_drawdown_conservative_and_halt(self, com_client, tmp_path):
        import compliance

        state = compliance._load_state(compliance.STATE_PATH)
        state["peak_equity"] = 10800.0
        compliance._save_state(compliance.STATE_PATH, state)
        assert evaluate()["mode"] == "conservative"

        state["peak_equity"] = 11000.0
        compliance._save_state(compliance.STATE_PATH, state)
        assert evaluate()["mode"] == "halted"

    def test_halted_state_persists(self, com_client, tmp_path):
        import compliance

        compliance._save_state(
            compliance.STATE_PATH,
            {
                "day_stamp": time.strftime("%Y-%m-%d"),
                "peak_equity": 10000.0,
                "mode": "halted",
                "halt_reason": "manual halt",
                "revenge_paused_until": 0.0,
            },
        )
        status = evaluate()
        assert status["mode"] == "halted"
        assert status["halt_reason"] == "manual halt"

    def test_day_roll_resets_halt(self, com_client, tmp_path):
        import compliance

        compliance._save_state(
            compliance.STATE_PATH,
            {
                "day_stamp": "2000-01-01",
                "peak_equity": 10000.0,
                "mode": "halted",
                "halt_reason": "old halt",
                "revenge_paused_until": 0.0,
            },
        )
        status = evaluate()
        assert status["mode"] == "normal"
        assert status["trading_allowed"] is True


class TestMarketFilters:
    def test_news_blackout_blocks(self, com_client, monkeypatch):
        class FakeNews:
            def __init__(self, events):
                self._events = events

            def upcoming(self, hours=24, currency=""):
                return self._events

        original = get_provider()
        try:
            set_provider(
                FakeNews(
                    [
                        NewsEvent(
                            time=datetime.now(timezone.utc) + timedelta(minutes=5),
                            currency="USD",
                            impact="high",
                            title="FOMC",
                        )
                    ]
                )
            )
            verdict = gate("EURUSD", "BUY", 0.05, sl_price=1.19500)
            assert verdict["allowed"] is False
            assert any("high-impact news" in v for v in verdict["violations"])
        finally:
            set_provider(original)

    def test_news_blackout_does_not_block_synthetic(self, com_client, monkeypatch):
        class FakeNews:
            def upcoming(self, hours=24, currency=""):
                return [
                    NewsEvent(
                        time=datetime.now(timezone.utc) + timedelta(minutes=5),
                        currency="USD",
                        impact="high",
                        title="FOMC",
                    )
                ]

        original = get_provider()
        try:
            set_provider(FakeNews())
            verdict = gate("Boom 500 Index", "SELL", 0.05, sl_price=1.20050)
            assert verdict["allowed"] is True
        finally:
            set_provider(original)

    def test_stale_calendar_warns_but_does_not_block_by_default(self, com_client):
        # StubNewsProvider carries no calendar data -> reported as stale.
        status = evaluate()

        assert status["news_stale"] is True
        assert status["news_data_age_sec"] is None
        assert status["news_blocked"] is False
        assert any("news calendar" in w for w in status["warnings"])

    def test_stale_calendar_blocks_when_limit_configured(self, com_client, monkeypatch):
        from config import get_settings

        monkeypatch.setattr(get_settings().risk, "news_stale_max_minutes", 30.0)

        class AgedNews:
            _cache_usable = True
            _source = "disk"
            _last_error = "HTTP Error 429: Too Many Requests"
            _failure_until = 0.0

            def __init__(self):
                self._data_saved_at = time.time() - 3 * 3600

            def upcoming(self, hours=24, currency=""):
                return []

        original = get_provider()
        try:
            set_provider(AgedNews())
            status = evaluate()
            assert status["news_blocked"] is True
            assert "stale" in (status["news_blocked_reason"] or "")

            verdict = gate("EURUSD", "BUY", 0.05, sl_price=1.19500)
            assert verdict["allowed"] is False
            assert any("stale" in v for v in verdict["violations"])
        finally:
            set_provider(original)

    def test_liquidity_blackout_blocks(self, com_client, monkeypatch):
        def fake_server_time():
            return datetime(2026, 1, 5, 0, 5, tzinfo=timezone.utc)

        monkeypatch.setattr(com_client, "server_time", fake_server_time)
        verdict = gate("EURUSD", "BUY", 0.05, sl_price=1.19500)
        assert verdict["allowed"] is False
        assert any("liquidity blackout" in v for v in verdict["violations"])


class TestDashboard:
    def test_status_fields(self, com_client):
        status = evaluate()
        for key in (
            "mode",
            "status",
            "balance",
            "equity",
            "daily_pnl",
            "daily_limit",
            "remaining_daily_buffer",
            "overall_drawdown_percent",
            "max_drawdown_percent",
            "remaining_drawdown_buffer",
            "open_risk",
            "open_positions",
            "trades_today",
            "consecutive_losses",
            "correlation_exposure",
            "per_trade_risk_cap_percent",
            "trading_allowed",
        ):
            assert key in status
        assert status["mode"] == "normal"
        assert status["status"] == "GREEN"
        assert status["trading_allowed"] is True

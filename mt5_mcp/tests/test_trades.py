"""Tests for the trade execution pipeline: preflight checks, orders, closes, SL/TP."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import client as client_module
from config import get_settings
from tools.trades import (
    MIN_DEVIATION_POINTS,
    _deviation,
    break_even,
    calc_lots,
    close_position,
    modify_sl_tp,
    open_market,
    pending_order,
    trail_stop,
)

EURUSD = "EURUSD"


@pytest.fixture(autouse=True)
def _use_fake(fake_client):
    client_module.set_client(fake_client)
    yield
    client_module.reset_client()


@pytest.fixture(autouse=True)
def _fake_state(tmp_path, monkeypatch):
    import compliance

    monkeypatch.setattr(compliance, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(compliance, "_record_event", lambda kind, message: None)


def _buy_position(**overrides) -> dict:
    pos = {
        "ticket": 1001,
        "symbol": EURUSD,
        "type": 0,
        "volume": 0.5,
        "price_open": 1.19500,
        "sl": 1.19000,
        "tp": 1.21500,
        "magic": 20260601,
    }
    pos.update(overrides)
    return pos


class TestPreflight:
    def test_buy_accepted(self, fake_client):
        result = open_market(EURUSD, "BUY", 0.09, sl=1.19500, tp=1.21500)
        assert result.accepted
        assert result.ticket == 999001
        req = fake_client.requests[-1]
        assert req["action"] == 1
        assert req["type"] == 0
        assert req["volume"] == 0.09

    def test_sell_accepted(self, fake_client):
        result = open_market(EURUSD, "SELL", 0.25, sl=1.20200, tp=1.19500)
        assert result.accepted
        req = fake_client.requests[-1]
        assert req["type"] == 1
        assert "spread" in " ".join(result.checks)

    def test_spread_too_wide_rejected(self, fake_client, monkeypatch):
        fake_client.bid, fake_client.ask = 1.2000, 1.20030
        monkeypatch.setattr(get_settings().risk, "max_spread_points", 1)
        result = open_market(EURUSD, "BUY", 0.01, sl=1.19500)
        assert not result.accepted
        assert "spread" in result.error.lower()

    def test_lot_out_of_broker_range_rejected(self, fake_client):
        result = open_market(EURUSD, "BUY", 0.001, sl=1.19500)
        assert not result.accepted
        assert "lot" in result.error.lower()

    def test_market_closed_rejected(self, fake_client):
        fake_client._market_open = (False, "weekend")
        result = open_market(EURUSD, "BUY", 0.1)
        assert not result.accepted
        assert "weekend" in result.error.lower()

    def test_no_stop_loss_rejected(self, fake_client):
        result = open_market(EURUSD, "BUY", 0.1)
        assert not result.accepted
        assert "stop-loss" in result.error.lower()

    def test_insufficient_margin_rejected(self, fake_client):
        fake_client.account["margin_free"] = 0.5
        result = open_market(EURUSD, "BUY", 0.08, sl=1.19500)
        assert not result.accepted
        assert "margin" in result.error.lower()

    def test_max_positions_rejected(self, fake_client, monkeypatch):
        monkeypatch.setattr(get_settings().risk, "max_positions", 1)
        fake_client._positions.append(
            _buy_position(ticket=1, symbol="XAUUSD", volume=0.01)
        )
        result = open_market(EURUSD, "BUY", 0.09, sl=1.19500)
        assert not result.accepted
        assert "positions" in result.error.lower()

    def test_volume_rounded_to_step(self, fake_client):
        result = open_market(EURUSD, "BUY", 0.087, sl=1.19500)
        assert result.accepted
        assert fake_client.requests[-1]["volume"] == 0.09


class TestSlippageAllowance:
    """deviation=0 means "fill at this exact price or reject" (retcode 10004).

    Gold reports trade_stops_level=0, so the old code sent 0 for every XAUUSD
    order.
    """

    def test_zero_broker_stop_level_uses_floor(self):
        assert _deviation({"trade_stops_level": 0}) == MIN_DEVIATION_POINTS

    def test_missing_stop_level_uses_floor(self):
        assert _deviation({}) == MIN_DEVIATION_POINTS

    def test_broker_min_stop_distance_wins_when_larger(self):
        assert _deviation({"trade_stops_level": 150}) == 150

    def test_market_order_sends_slippage_allowance(self, fake_client):
        result = open_market(EURUSD, "BUY", 0.09, sl=1.19500, tp=1.21500)
        assert result.accepted
        assert fake_client.requests[-1]["deviation"] >= MIN_DEVIATION_POINTS

    def test_close_order_sends_slippage_allowance(self, fake_client):
        fake_client._positions.append(_buy_position())
        result = close_position(1001)
        assert result.accepted
        assert fake_client.requests[-1]["deviation"] >= MIN_DEVIATION_POINTS


class TestPending:
    def test_buy_limit_below_ask(self, fake_client):
        result = pending_order(EURUSD, "buy_limit", 0.09, price=1.19800, sl=1.19500)
        assert result.accepted
        assert fake_client.requests[-1]["action"] == 5

    def test_buy_limit_above_ask_rejected(self, fake_client):
        result = pending_order(EURUSD, "buy_limit", 0.09, price=1.20500, sl=1.19500)
        assert not result.accepted
        assert "below current ask" in result.error.lower()


class TestCloseAndModify:
    def test_close_position(self, fake_client):
        fake_client._positions.append(_buy_position())
        result = close_position(1001)
        assert result.accepted
        req = fake_client.requests[-1]
        assert req["action"] == 1
        assert req["type"] == 1
        assert req["position"] == 1001

    def test_modify_sl_tp(self, fake_client):
        fake_client._positions.append(_buy_position())
        result = modify_sl_tp(1001, sl=1.19200, tp=1.22000)
        assert result.accepted
        req = fake_client.requests[-1]
        assert req["action"] == 6
        assert req["sl"] == 1.19200
        assert req["tp"] == 1.22000

    def test_trail_stop_moves_forward(self, fake_client):
        fake_client._positions.append(_buy_position(sl=1.19000))
        result = trail_stop(1001, trail_pips=20)
        assert result.accepted
        assert fake_client.requests[-1]["action"] == 6

    def test_trail_stop_backwards_rejected(self, fake_client):
        fake_client.bid = 1.19200
        fake_client._positions.append(_buy_position(sl=1.19400))
        result = trail_stop(1001, trail_pips=20)
        assert not result.accepted
        assert "backwards" in result.error.lower()

    def test_break_even(self, fake_client):
        fake_client.bid = 1.20000
        fake_client._positions.append(_buy_position())
        result = break_even(1001, offset_pips=5)
        assert result.accepted
        assert fake_client.requests[-1]["sl"] > 1.19500


class TestCalcLots:
    def test_risk_based_lot_size(self):
        result = calc_lots(EURUSD, sl_pips=20, risk_percent=0.5)
        assert result["lots"] == pytest.approx(0.25, abs=0.01)
        assert result["risk_amount"] == pytest.approx(50.0, abs=0.01)

    def test_calc_lots_zero_pips(self):
        result = calc_lots(EURUSD, sl_pips=0)
        assert "error" in result

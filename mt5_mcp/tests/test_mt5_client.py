"""Tests for the MT5 client: health checks, reconnect, data conversion."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mt5_client import MT5Error


def test_health_when_connected(mt5_client):
    health = mt5_client.health()
    assert health["ok"] is True
    assert health["connected"] is True


def test_health_marks_disconnected_when_account_missing(fake_mt5, mt5_client):
    fake_mt5.account = None
    health = mt5_client.health()
    assert health["ok"] is False
    assert health["connected"] is False


def test_rates_conversion(mt5_client):
    frame = mt5_client.rates("EURUSD", "H1", 50)
    assert list(frame.columns) == [
        "time",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
        "real_volume",
    ]
    assert len(frame) == 50
    assert frame["close"].dtype == float


def test_rates_invalid_timeframe(mt5_client):
    with pytest.raises(ValueError):
        mt5_client.rates("EURUSD", "BOGUS", 10)


def test_connect_raises_without_package():
    from mt5_client import MT5Client

    client = MT5Client()
    try:
        import MetaTrader5  # noqa: F401

        pytest.skip("MetaTrader5 installed; cannot test missing-package path")
    except ImportError:
        with pytest.raises(MT5Error, match="MetaTrader5 package is not installed"):
            client.connect()


def test_symbol_info_raises_for_unknown(fake_mt5, mt5_client):
    fake_mt5.symbol_info = lambda symbol: None
    with pytest.raises(MT5Error, match="does not exist"):
        mt5_client.symbol_info("NOPE")


def test_order_send_rejection(mt5_client):
    from common import RETCODE_DONE

    request = {"action": 1, "symbol": "EURUSD", "volume": 0.1, "type": 0, "price": 1.1}
    result = mt5_client.order_send(request)
    assert result["retcode"] == RETCODE_DONE


def test_shutdown(mt5_client):
    mt5_client.shutdown()
    assert mt5_client.health()["ok"] is False


def test_closed_orders_attaches_pnl_from_deals(fake_mt5, mt5_client):
    """MT5 orders carry no profit - realized P&L must be computed from the
    closing (entry=OUT) deals."""
    from collections import namedtuple

    Order = namedtuple(
        "Order",
        ["ticket", "time_setup", "time_done", "type", "state", "symbol",
         "price_open", "price_current", "volume_initial", "volume_current", "profit"],
    )
    Deal = namedtuple(
        "Deal",
        ["ticket", "order", "entry", "type", "symbol", "volume", "price",
         "profit", "swap", "fee", "time"],
    )
    fake_mt5.history_orders_get = lambda *args, **kw: (
        Order(ticket=1, time_setup=100, time_done=200, type=0, state=3,
              symbol="EURUSD", price_open=1.10, price_current=1.11,
              volume_initial=0.1, volume_current=0.0, profit=None),
        Order(ticket=2, time_setup=100, time_done=0, type=1, state=2,
              symbol="EURUSD", price_open=1.12, price_current=1.12,
              volume_initial=0.1, volume_current=0.1, profit=None),
        Order(ticket=3, time_setup=100, time_done=300, type=0, state=3,
              symbol="XAUUSD", price_open=2000.0, price_current=2010.0,
              volume_initial=0.5, volume_current=0.0, profit=None),
    )
    fake_mt5.history_deals_get = lambda *args, **kw: (
        Deal(ticket=11, order=1, entry=1, type=0, symbol="EURUSD", volume=0.1,
             price=1.11, profit=100.0, swap=-1.0, fee=-0.5, time=200),
        Deal(ticket=12, order=2, entry=0, type=1, symbol="EURUSD", volume=0.1,
             price=1.12, profit=0.0, swap=0.0, fee=0.0, time=150),
        Deal(ticket=13, order=3, entry=1, type=0, symbol="XAUUSD", volume=0.5,
             price=2010.0, profit=-50.0, swap=0.0, fee=0.0, time=300),
        Deal(ticket=14, order=3, entry=0, type=1, symbol="XAUUSD", volume=0.5,
             price=2005.0, profit=0.0, swap=0.0, fee=0.0, time=150),
    )

    closed = mt5_client.closed_orders(days=30)
    assert [o["ticket"] for o in closed] == [1, 3]  # order 2 still open
    assert closed[0]["profit"] == 98.5  # 100 - 1 - 0.5
    assert closed[1]["profit"] == -50.0


class _PartialHistoryModule:
    """Terminal-like module serving M5 bars only (no cached H1+)."""

    def __init__(self) -> None:
        self.rates_calls: list[tuple] = []
        self.n_bars = 288 * 5
        self.m5_time = 1_780_000_000

    def copy_rates_from_pos(self, symbol, tf, pos, count):
        self.rates_calls.append((symbol, tf, pos, count))
        if tf != 5:
            return None
        n = min(count, self.n_bars)
        ts = self.m5_time - self.n_bars * 300 + pos * 300 + np.arange(n) * 300
        close = 1.2 + np.arange(self.n_bars)[pos : pos + n] * 0.0001
        return np.array(
            list(
                zip(
                    ts,
                    close - 0.0002,
                    close + 0.0003,
                    close - 0.0001,
                    close,
                    np.full(n, 100),
                    np.full(n, 5),
                    np.full(n, 1000),
                    strict=True,
                ),
            ),
            dtype=[
                ("time", "<i8"),
                ("open", "<f8"),
                ("high", "<f8"),
                ("low", "<f8"),
                ("close", "<f8"),
                ("tick_volume", "<i8"),
                ("spread", "<i8"),
                ("real_volume", "<i8"),
            ],
        )

    def account_info(self):
        return {"login": 1}

    def terminal_info(self):
        return {}

    def shutdown(self):
        return None


def test_rates_aggregates_higher_timeframes_from_m5():
    from mt5_client import MT5Client

    fake = _PartialHistoryModule()
    client = MT5Client()
    client._mt5 = fake
    client._connected = True

    frame = client.rates("EURUSD", "H1", 24)
    assert len(frame) == 24
    assert (frame["time"].diff().dropna() == pd.Timedelta(hours=1)).all()
    assert (frame["close"].diff().dropna() > 0).all()

    frame4 = client.rates("EURUSD", "H4", 12)
    assert (frame4["time"].diff().dropna() == pd.Timedelta(hours=4)).all()

    frame_m5 = client.rates("EURUSD", "M5", 10)
    assert len(frame_m5) == 10
    assert (fake.rates_calls[0][1] == 60) and (fake.rates_calls[1][1] == 5)

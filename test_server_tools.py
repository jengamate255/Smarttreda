"""Tests for the root MCP tools in server.py using a mocked MetaTrader5 module.

Run:  pytest test_server_tools.py -v
"""

from types import SimpleNamespace

import pytest

import server


class FakeMT5:
    TIMEFRAME_M15 = 16383
    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16408
    TIMEFRAME_D1 = 16408
    DEAL_ENTRY_OUT = 1
    DEAL_TYPE_BUY = 0
    DEAL_TYPE_SELL = 1
    DEAL_TYPE_BALANCE = 2
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_REMOVE = 7
    SYMBOL_TRADE_MODE_FULL = 0
    SYMBOL_TRADE_MODE_LONGONLY = 1

    def __init__(self):
        self.account = SimpleNamespace(
            login=11911448,
            balance=10000.0,
            equity=10100.0,
            margin=100.0,
            margin_free=9900.0,
            margin_level=10100.0,
            currency="USD",
            leverage=100,
        )
        self.tick = SimpleNamespace(
            time=1750000000,
            bid=1.1050,
            ask=1.1052,
            last=1.1051,
            volume=1000,
            flags=0,
            spread=2,
        )
        self.symbol_info_obj = SimpleNamespace(
            name="EURUSD",
            bid=1.1050,
            ask=1.1052,
            spread=2,
            point=0.00001,
            digits=5,
            trade_mode=self.SYMBOL_TRADE_MODE_FULL,
        )
        self.positions = [
            SimpleNamespace(
                ticket=1001,
                symbol="EURUSD",
                type=self.ORDER_TYPE_BUY,
                volume=0.1,
                price_open=1.1000,
                sl=1.0950,
                tp=1.1200,
                price_current=1.1050,
                profit=50.0,
                swap=-1.0,
                comment="",
                magic=234000,
            )
        ]
        self.orders = [
            SimpleNamespace(
                ticket=2001,
                symbol="EURUSD",
                type=self.ORDER_TYPE_BUY_LIMIT,
                volume_initial=0.1,
                price_open=1.0900,
                sl=0.0,
                tp=0.0,
                comment="",
                magic=234000,
                time_setup=1750000000,
            )
        ]
        self.rates = [
            (1750000000 + i * 3600, 1.10 + i * 0.0001, 1.10 + i * 0.0001 + 0.0005, 1.10 + i * 0.0001 - 0.0005, 1.10 + i * 0.0001 + 0.0001, 100 + i, 2, 0)
            for i in range(100)
        ]
        self.last_error_msg = (0, "OK")

    def initialize(self, path=""):
        return True

    def login(self, login, password="", server=""):
        return True

    def shutdown(self):
        return None

    def account_info(self):
        return self.account

    def symbol_info(self, symbol):
        if symbol == "EURUSD":
            return self.symbol_info_obj
        return None

    def symbol_info_tick(self, symbol):
        if symbol == "EURUSD":
            return self.tick
        return None

    def terminal_info(self):
        return SimpleNamespace(
            company="FundingPips",
            build=4025,
            connected=True,
            name="MetaTrader 5 Terminal",
        )

    def positions_get(self, symbol=None, ticket=None):
        data = self.positions
        if symbol:
            data = [p for p in data if p.symbol == symbol]
        if ticket is not None:
            data = [p for p in data if p.ticket == ticket]
        return data

    def orders_get(self, symbol=None):
        if symbol:
            return [o for o in self.orders if o.symbol == symbol]
        return self.orders

    def history_deals_get(self, from_dt=None, to_dt=None, group=None):
        base = 1750000000
        deals = [
            SimpleNamespace(
                time=base - 3600 * i,
                time_msc=base * 1000 - i * 3600 * 1000,
                order=1000 + i,
                ticket=5000 + i,
                symbol="EURUSD",
                type=self.DEAL_TYPE_BUY,
                entry=self.DEAL_ENTRY_OUT,
                volume=0.1,
                price=1.10 + i * 0.001,
                profit=10.0 if i % 2 == 0 else -10.0,
                swap=0.0,
                fee=0.0,
                comment="test",
            )
            for i in range(10)
        ]
        return deals

    def copy_rates_from_pos(self, symbol, timeframe, pos, count):
        if symbol == "EURUSD":
            return self.rates[:count]
        return None

    def order_send(self, request):
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            deal=12345,
            order=12346,
            volume=request.get("volume", 0.1),
            price=request.get("price", 1.1050),
            bid=1.1050,
            ask=1.1052,
            comment="OK",
            request_id=1,
            retcode_external=0,
        )

    def last_error(self):
        return self.last_error_msg


@pytest.fixture(autouse=True)
def fake_mt5(monkeypatch):
    fake = FakeMT5()
    monkeypatch.setattr(server, "mt5", fake)
    server.mt5_connection._connected = True
    server.mt5_connection._config = server.MT5Config()
    yield fake
    server.mt5_connection._connected = False


@pytest.mark.asyncio
async def test_get_account_info():
    info = await server.get_account_info(SimpleNamespace())
    assert info.login == 11911448
    assert info.balance == 10000.0
    assert info.equity == 10100.0


@pytest.mark.asyncio
async def test_get_symbol_info():
    info = await server.get_symbol_info(SimpleNamespace(), "EURUSD")
    assert info.bid == 1.1050
    assert info.ask == 1.1052
    assert info.spread == 2


@pytest.mark.asyncio
async def test_get_positions():
    positions = await server.get_positions(SimpleNamespace(), "EURUSD")
    assert len(positions) == 1
    assert positions[0].ticket == 1001
    assert positions[0].symbol == "EURUSD"


@pytest.mark.asyncio
async def test_get_orders():
    orders = await server.get_orders(SimpleNamespace(), "EURUSD")
    assert len(orders) == 1
    assert orders[0]["ticket"] == 2001
    assert orders[0]["symbol"] == "EURUSD"


@pytest.mark.asyncio
async def test_place_order():
    req = server.OrderRequest(
        symbol="EURUSD",
        volume=0.1,
        type=server.mt5.ORDER_TYPE_BUY,
        price=1.1050,
        sl=1.0950,
        tp=1.1200,
    )
    result = await server.place_order(SimpleNamespace(), req)
    assert result.retcode == server.mt5.TRADE_RETCODE_DONE
    assert result.deal == 12345


@pytest.mark.asyncio
async def test_close_position():
    result = await server.close_position(SimpleNamespace(), ticket=1001)
    assert result.retcode == server.mt5.TRADE_RETCODE_DONE


@pytest.mark.asyncio
async def test_cancel_order():
    ok = await server.cancel_order(SimpleNamespace(), ticket=2001)
    assert ok is True


@pytest.mark.asyncio
async def test_get_rates():
    rates = await server.get_rates(SimpleNamespace(), "EURUSD", server.mt5.TIMEFRAME_H1, 10)
    assert len(rates) == 10
    assert rates[0]["close"] == 1.1001
    assert rates[0]["tick_volume"] == 100


@pytest.mark.asyncio
async def test_get_tick_info():
    tick = await server.get_tick_info(SimpleNamespace(), "EURUSD")
    assert tick["bid"] == 1.1050
    assert tick["ask"] == 1.1052


@pytest.mark.asyncio
async def test_get_history_deals_default_range():
    deals = await server.get_history_deals(SimpleNamespace())
    assert len(deals) == 10
    assert deals[0]["symbol"] == "EURUSD"


@pytest.mark.asyncio
async def test_get_technical_analysis():
    ta = await server.get_technical_analysis(SimpleNamespace(), "EURUSD", count=51)
    assert ta.symbol == "EURUSD"
    assert "H4" in ta.timeframes
    assert ta.timeframes["H4"]["trend"] in ("strong_uptrend", "weak_uptrend", "strong_downtrend", "weak_downtrend", "ranging", "insufficient_data")

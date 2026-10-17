"""Tests for pip maths.

``pip_value_per_lot`` feeds both the per-trade and the portfolio risk caps in
``compliance.py``, so a wrong value silently disables risk control (see the
block comment in ``mt5_mcp/common.py``).
"""

from __future__ import annotations

import pytest

import common
from common import pip_size, pip_value_per_lot, reset_pip_value_cache


def test_pip_size_fx_5digit():
    assert pip_size({"digits": 5, "point": 1e-5}) == 0.0001


def test_pip_size_fx_4digit():
    assert pip_size({"digits": 4, "point": 1e-4}) == 0.0001


def test_pip_size_jpy_3digit():
    assert pip_size({"digits": 3, "point": 0.001}) == 0.01


def test_pip_size_index_2digit():
    assert pip_size({"digits": 2, "point": 0.01}) == 1.0


def test_pip_size_7digit():
    assert abs(pip_size({"digits": 7, "point": 1e-7}) - 0.0001) < 1e-12


def test_pip_value_per_lot_fx():
    info = {"digits": 5, "point": 1e-5, "trade_tick_size": 1e-5, "trade_tick_value": 1.0}
    assert abs(pip_value_per_lot(info) - 10.0) < 1e-9


def test_pip_value_per_lot_index():
    info = {"digits": 2, "point": 0.01, "trade_tick_size": 0.25, "trade_tick_value": 5.0}
    assert abs(pip_value_per_lot(info) - 20.0) < 1e-9


# ---------------------------------------------------------------------------
# broker under-reporting repair (gold/silver/oil)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_pip_cache():
    reset_pip_value_cache()
    yield
    reset_pip_value_cache()


@pytest.fixture
def no_broker(monkeypatch):
    """Force the offline path: no order_calc_profit probe available."""
    monkeypatch.setattr(common, "_broker_pip_value", lambda symbol, info: 0.0)


def _usd_account(monkeypatch, currency="USD"):
    monkeypatch.setattr(common, "_ACCOUNT_CCY", currency)


def _gold_info(**overrides):
    """XAUUSD as FundingPips reports it: tick value 100x too small."""
    info = {
        "name": "XAUUSD",
        "digits": 2,
        "point": 0.01,
        "trade_tick_size": 0.01,
        "trade_tick_value": 0.01,
        "trade_contract_size": 100.0,
        "currency_profit": "USD",
        "ask": 0.0,
    }
    info.update(overrides)
    return info


def test_gold_under_report_is_repaired(monkeypatch, no_broker):
    info = _gold_info()
    # The reported tick value implies $1.00/lot/pip; the truth is $100.00.
    assert common._formula_pip_value(info) == pytest.approx(1.0)
    _usd_account(monkeypatch)
    assert pip_value_per_lot(info) == pytest.approx(100.0)


def test_gold_under_report_is_not_repaired_without_account_currency(
    monkeypatch, no_broker
):
    info = _gold_info()
    monkeypatch.setattr(common, "_ACCOUNT_CCY", None)
    monkeypatch.setattr(common, "_ACCOUNT_CCY_GAVE_UP", True)
    # Unresolvable account currency: fall back to the unvalidated formula...
    assert pip_value_per_lot(info) == pytest.approx(1.0)
    # ...and the negative result must NOT be cached, so a later session
    # (or the next call after connecting) still gets the corrected value.
    reset_pip_value_cache("USD")
    assert pip_value_per_lot(info) == pytest.approx(100.0)


def test_oil_under_report_is_repaired(monkeypatch, no_broker):
    info = {
        "name": "USOIL",
        "digits": 3,
        "point": 0.001,
        "trade_tick_size": 0.001,
        "trade_tick_value": 0.001,
        "trade_contract_size": 1000.0,
        "currency_profit": "USD",
    }
    assert common._formula_pip_value(info) == pytest.approx(0.01)
    _usd_account(monkeypatch)
    assert pip_value_per_lot(info) == pytest.approx(10.0)


def test_cross_currency_quote_keeps_broker_tick_value(monkeypatch, no_broker):
    """USDJPY profits in JPY, so contract maths must NOT be applied."""
    info = {
        "name": "USDJPY",
        "digits": 3,
        "point": 0.001,
        "trade_tick_size": 0.001,
        "trade_tick_value": 0.6352,
        "trade_contract_size": 100000.0,
        "currency_profit": "JPY",
    }
    _usd_account(monkeypatch)
    assert pip_value_per_lot(info) == pytest.approx(6.352)


def test_healthy_symbol_is_unchanged(monkeypatch, no_broker):
    """EURUSD already reports a correct tick value: no silent behaviour change."""
    info = {
        "name": "EURUSD",
        "digits": 5,
        "point": 1e-5,
        "trade_tick_size": 1e-5,
        "trade_tick_value": 1.0,
        "trade_contract_size": 100000.0,
        "currency_profit": "USD",
    }
    _usd_account(monkeypatch)
    assert pip_value_per_lot(info) == pytest.approx(10.0)


def test_broker_probe_wins_over_formula(monkeypatch):
    """order_calc_profit is the broker's own maths, so it is authoritative."""
    info = _gold_info(name="XAGUSD", trade_contract_size=5000.0, trade_tick_value=0.5)
    monkeypatch.setattr(common, "_ACCOUNT_CCY", None)
    monkeypatch.setattr(common, "_ACCOUNT_CCY_GAVE_UP", False)
    monkeypatch.setattr(common, "_broker_pip_value", lambda symbol, i: 50.0)
    assert pip_value_per_lot(info) == pytest.approx(50.0)


def test_implausible_probe_is_discarded(monkeypatch):
    """A nonsense probe (closed market, stale price) must not be trusted."""
    info = _gold_info()
    _usd_account(monkeypatch)
    monkeypatch.setattr(common, "_broker_pip_value", lambda symbol, i: 1e9)
    assert pip_value_per_lot(info) == pytest.approx(100.0)


def test_broker_probe_is_cached(monkeypatch):
    info = _gold_info()
    _usd_account(monkeypatch)
    calls = []

    def _probe(symbol, i):
        calls.append(symbol)
        return 100.0

    monkeypatch.setattr(common, "_broker_pip_value", _probe)
    for _ in range(5):
        assert pip_value_per_lot(info) == pytest.approx(100.0)
    assert calls == ["XAUUSD"]


"""Risk engine tests."""
from __future__ import annotations

import pytest

from src.risk.risk_engine import RiskManager, position_size


def test_risk_manager_from_config(cfg):
    rm = RiskManager.from_config(cfg, equity=50_000)
    assert rm.max_daily_loss_pct == pytest.approx(2.0)
    assert rm.max_drawdown_pct == pytest.approx(20.0)
    assert RiskManager.default_risk_percent(cfg) == pytest.approx(0.5)
    RiskManager.validate_percent(cfg, 0.5)  # supported
    with pytest.raises(ValueError):
        RiskManager.validate_percent(cfg, 0.37)  # unsupported
    ok, why = rm.can_trade(49_000)
    assert ok and why == "ok"


def test_position_size_basic():
    # 1 pip = 0.0001 EURUSD; pip value per lot = 10 USD; SL 20 pips; risk 0.5%.
    res = position_size(
        account_equity=100_000,
        risk_percent=0.5,
        entry=1.1000,
        sl=1.0980,
        pip_value_per_lot=10.0,
    )
    # risk_amount = 500 USD; risk_per_lot = 20 pips * 10 USD = 200 USD -> 2.5 lots
    assert res.lots == pytest.approx(2.5, rel=1e-6)
    assert res.risk_amount == pytest.approx(500.0)
    assert res.rounded_down is False


def test_position_size_rounds_down_to_step():
    res = position_size(
        account_equity=10_000,
        risk_percent=1.0,
        entry=1.2000,
        sl=1.1970,
        pip_value_per_lot=10.0,
    )
    assert abs(res.lots - round(res.lots / 0.01) * 0.01) < 1e-9
    assert res.rounded_down is True
    assert res.lots > 0


def test_position_size_respects_min_and_max():
    small = position_size(
        account_equity=100, risk_percent=0.25,
        entry=1.1000, sl=1.0995, pip_value_per_lot=10.0,
    )
    assert small.lots >= 0.01
    big = position_size(
        account_equity=10_000_000, risk_percent=1.0,
        entry=1.1000, sl=1.0999, pip_value_per_lot=10.0,
        max_lots=100.0,
    )
    assert big.lots <= 100.0


def test_position_size_rejects_bad_input():
    with pytest.raises(ValueError):
        position_size(0, 1.0, 1.0, 1.01, 10.0)
    with pytest.raises(ValueError):
        position_size(100_000, 0, 1.0, 1.01, 10.0)
    with pytest.raises(ValueError):
        position_size(100_000, 1.0, 1.10, 1.10, 10.0)


def test_risk_manager_daily_loss_gate():
    rm = RiskManager(equity=100_000, max_daily_loss_pct=2.0, max_drawdown_pct=20.0)
    ok, why = rm.can_trade(99_000)
    assert ok and why == "ok"
    ok, why = rm.can_trade(97_900)
    assert not ok and why == "daily_loss_limit"


def test_risk_manager_drawdown_gate():
    rm = RiskManager(equity=100_000, max_daily_loss_pct=30.0, max_drawdown_pct=20.0)
    rm.can_trade(110_000)  # equity goes up -> peak 110k
    ok, why = rm.can_trade(90_000)
    assert ok  # drawdown 18.2% < 20%
    ok, why = rm.can_trade(86_000)
    assert not ok and why == "max_drawdown_limit"

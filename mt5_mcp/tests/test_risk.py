"""Tests for risk calculations and the risk manager."""

from __future__ import annotations

from tools.risk import risk_report
from tools.trades import calc_lots


def test_calc_lots_math(fake_client, client_module):
    client_module.set_client(fake_client)
    result = calc_lots("EURUSD", sl_pips=25.0, risk_percent=0.5)
    assert result["lots"] == 0.2
    assert result["risk_amount"] == 50.0
    assert result["equity"] == 10000.0


def test_calc_lots_respects_step(fake_client, client_module):
    client_module.set_client(fake_client)
    result = calc_lots("EURUSD", sl_pips=25.0, risk_percent=0.07)
    assert result["lots"] == 0.03  # 0.028 rounds to step 0.01


def test_calc_lots_rejects_excess_risk(fake_client, client_module):
    client_module.set_client(fake_client)
    result = calc_lots("EURUSD", sl_pips=25.0, risk_percent=5.0)
    assert "error" in result


def test_risk_report_limits(fake_client, client_module):
    client_module.set_client(fake_client)
    report = risk_report()
    assert report["equity"] == 10000.0
    assert report["daily_limit"] == 300.0
    assert report["weekly_limit"] == 600.0
    assert report["max_open_risk"] == 100.0
    assert report["trading_allowed"] is True


def test_risk_report_blocks_after_daily_loss(fake_client, client_module):
    client_module.set_client(fake_client)
    fake_client.deals_history = [
        {"type": 0, "profit": -250.0, "time": 1000000},
        {"type": 1, "profit": -100.0, "time": 1000001},
    ]
    report = risk_report()
    assert report["trading_allowed"] is False
    assert any("DAILY" in w for w in report["warnings"])

"""The dashboard must report policy without exposing credentials or claiming enforcement."""
from types import SimpleNamespace

import web_diagnostic


def test_runtime_reports_record_only_and_cached_drift_without_secrets(monkeypatch):
    import mt5_mcp.config as config
    import mt5_mcp.consensus as consensus
    import mt5_mcp.compliance as compliance

    cached = SimpleNamespace(max_risk_percent=0.5, max_daily_loss_percent=4.0,
        max_trades_per_day=2, confidence_floor_percent=80, reject_orders=False,
        blocked_symbols=[])
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(risk=cached, api_token="secret"))
    monkeypatch.setattr(consensus, "_cfg", lambda: {"enabled": True,
        "allowed_symbols": ["BTCUSD"], "risk_percent": 0.5, "secret": "secret"})
    monkeypatch.setattr(compliance, "evaluate", lambda: {"mode": "normal",
        "remaining_daily_buffer": 123.0, "per_trade_risk_cap_percent": 0.5, "secret": "secret"})
    monkeypatch.setattr(web_diagnostic, "_load_settings", lambda: {"risk": {"max_risk_percent": 1.0}})
    monkeypatch.setenv("MT5_DESK_MODE", "SIM")
    monkeypatch.setenv("MT5_DESK_ENABLED", "true")

    result = web_diagnostic.diagnostic_runtime()
    assert result["risk"]["reject_orders"] is False
    assert result["settings_drift"] == ["max_risk_percent"]
    assert result["compliance"]["remaining_daily_buffer"] == 123.0
    assert result["desk"] == {"enabled": True, "mode": "SIM"}
    assert "secret" not in str(result)


def test_runtime_compliance_failure_is_explicit(monkeypatch):
    import mt5_mcp.config as config
    import mt5_mcp.consensus as consensus
    import mt5_mcp.compliance as compliance

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(risk=SimpleNamespace()))
    monkeypatch.setattr(consensus, "_cfg", lambda: {"enabled": False})
    monkeypatch.setattr(web_diagnostic, "_load_settings", lambda: {})
    def unavailable():
        raise RuntimeError("private diagnostic details")
    monkeypatch.setattr(compliance, "evaluate", unavailable)
    result = web_diagnostic.diagnostic_runtime()
    assert result["compliance"] == {"error": "Compliance telemetry unavailable"}
    assert result["consensus"]["enabled"] is False
    assert "private diagnostic details" not in str(result)

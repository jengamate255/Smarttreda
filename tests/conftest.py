"""Shared fixtures for ml_engine tests."""
import pytest


@pytest.fixture(autouse=True)
def isolate_live_risk_profiles(monkeypatch):
    """Production profile activation must not change unrelated test assumptions."""
    from mt5_mcp.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings.performance_risk,"enabled",False)
    monkeypatch.setattr(settings.prop_risk,"enabled",False)

"""Tests for configuration loading."""

from __future__ import annotations

import json
from pathlib import Path

from config import Settings, setup_logging


def test_defaults_without_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.risk.default_risk_percent == 0.5
    assert settings.risk.min_risk_reward == 3.0
    assert settings.strategy.trend_fast_ema == 50
    assert settings.memory.db_path == "memory/journal.db"


def test_resolve_paths_makes_absolute(tmp_path):
    settings = Settings(memory={"db_path": "memory/journal.db"})
    resolved = settings.resolve_paths(base=tmp_path)
    assert Path(resolved.memory.db_path).is_absolute()
    assert str(resolved.memory.db_path).startswith(str(tmp_path))


def test_get_settings_reads_settings_json(tmp_path, monkeypatch):
    import config

    # Clear env vars that override settings
    for var in ["MT5_MCP_MT5_LOGIN", "MT5_MCP_MT5_PASSWORD", "MT5_MCP_MT5_SERVER", "MT5_MCP_MT5_TERMINAL_PATH"]:
        monkeypatch.delenv(var, raising=False)

    payload = {"risk": {"default_risk_percent": 0.75}, "mt5": {"login": 123456}}
    (tmp_path / "settings.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    config.get_settings.cache_clear()
    settings = config.get_settings()
    assert settings.risk.default_risk_percent == 0.75
    assert settings.mt5.login == 123456
    config.get_settings.cache_clear()


def test_env_overrides(tmp_path, monkeypatch):
    import config

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MT5_MCP_RISK_DEFAULT", "0.9")
    monkeypatch.setenv("MT5_MCP_LOG_LEVEL", "DEBUG")
    config.get_settings.cache_clear()
    settings = config.get_settings()
    assert settings.risk.default_risk_percent == 0.9
    assert settings.logging.level == "DEBUG"
    config.get_settings.cache_clear()


def test_setup_logging_creates_file(tmp_path, monkeypatch):

    monkeypatch.chdir(tmp_path)
    settings = Settings(logging={"log_dir": str(tmp_path / "logs")})
    logger = setup_logging(settings)
    assert logger.name == "mt5_mcp"
    assert (tmp_path / "logs" / "mt5_mcp.log").exists()

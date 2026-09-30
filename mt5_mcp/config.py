"""Configuration loading for the MT5 MCP server.

Settings are loaded from ``settings.json`` and can be overridden with
environment variables (prefixed ``MT5_MCP_``) or a local ``.env`` file.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

load_dotenv(PROJECT_DIR / ".env")
load_dotenv(BASE_DIR / ".env")

_Transport = Literal["stdio", "streamable-http"]


class ServerConfig(BaseModel):
    name: str = "mt5-mcp"
    version: str = "1.0.0"
    transport: _Transport = "stdio"
    host: str = "127.0.0.1"
    port: int = 9000


class MT5Config(BaseModel):
    terminal_path: str = ""
    login: int | None = None
    password: str = ""
    server: str = ""
    connect_timeout_seconds: int = 60
    reconnect_attempts: int = 10
    reconnect_delay_seconds: int = 10
    health_check_interval_seconds: int = 60

    @property
    def is_configured(self) -> bool:
        return self.login is not None and self.login > 0


class RiskConfig(BaseModel):
    default_risk_percent: float = 0.5
    max_risk_percent: float = 0.5
    conservative_risk_percent: float = 0.25
    max_daily_loss_percent: float = 3.0
    max_weekly_loss_percent: float = 6.0
    max_open_risk_percent: float = 1.0
    max_drawdown_percent: float = 8.0
    daily_conservative_ratio: float = 0.8
    drawdown_conservative_ratio: float = 0.8
    drawdown_halt_ratio: float = 0.95
    min_risk_reward: float = 3.0
    target_tp_min_pips: float = 80.0
    target_tp_max_pips: float = 100.0
    sl_min_pips: float = 20.0
    sl_max_pips: float = 30.0
    max_spread_points: float = 40.0
    max_spread_relative: float = 0.0
    max_positions: int = 5
    max_trades_per_day: int = 3
    max_trades_per_day_green_bonus: int = 2
    max_consecutive_losses: int = 3
    revenge_cooldown_minutes: float = 60.0
    news_blackout_minutes: float = 30.0
    # 0 = report stale calendar data as a warning only (fail-open).
    # Set > 0 (minutes) to blackout entries once the news feed is older.
    news_stale_max_minutes: float = 0.0
    liquidity_blackout_start: str = "00:00"
    liquidity_blackout_end: str = "00:15"
    confidence_floor_percent: float = 90.0
    allowed_sessions: list[str] | None = None
    min_volume: float | None = None
    max_volume: float | None = None
    blocked_symbols: list[str] = Field(default_factory=list)


class StrategyConfig(BaseModel):
    trend_timeframe: str = "H4"
    zone_timeframes: list[str] = Field(default_factory=lambda: ["H1", "H4"])
    confirmation_timeframe: str = "H1"
    trend_fast_ema: int = 50
    trend_slow_ema: int = 200
    min_zone_score: float = 70.0
    min_trade_score: float = 70.0
    min_confidence_percent: float = 90.0
    lookback_candles: int = 400
    # Require at least ``mtf_min_aligned`` of the other timeframes to agree with
    # the trend timeframe before a plan is considered tradable. Off by default so
    # library/MCP callers keep the previous behaviour; settings.json enables it.
    require_mtf_alignment: bool = False
    mtf_min_aligned: int = 1


class DerivConfig(BaseModel):
    enabled: bool = False
    ws_url: str = "wss://ws.derivws.com/websockets/v3"
    app_id: int = 1089
    api_token: str = ""
    request_timeout_seconds: float = 15.0
    connect_timeout_seconds: float = 20.0
    reconnect_attempts: int = 5
    reconnect_delay_seconds: float = 5.0
    ping_interval_seconds: float = 30.0


class MemoryConfig(BaseModel):
    db_path: str = "memory/journal.db"
    auto_sync_history_days: int = 30
    screenshot_dir: str = "memory/screenshots"
    report_dir: str = "memory/reports"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    log_dir: str = "logs"
    max_bytes: int = 5_242_880
    backup_count: int = 5


class BoomCrashConfig(BaseModel):
    trail_arm_r: float = 1.0
    trail_atr_distance: float = 1.0
    trail_atr_min: float = 1.0
    trail_atr_max: float = 1.5
    partial_exit_fraction: float = 0.5
    partial_exit_enabled: bool = True
    watchlist: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    mt5: MT5Config = Field(default_factory=MT5Config)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    deriv: DerivConfig = Field(default_factory=DerivConfig)
    deriv_mt5: MT5Config = Field(default_factory=MT5Config)
    boom_crash: BoomCrashConfig = Field(default_factory=BoomCrashConfig)

    def resolve_paths(self, base: Path = BASE_DIR) -> Settings:
        data = self.model_dump()
        for section in ("memory", "logging"):
            for key in ("db_path", "screenshot_dir", "report_dir", "log_dir"):
                raw = data.get(section, {}).get(key)
                if raw and isinstance(raw, str) and not Path(raw).is_absolute():
                    data[section][key] = str(base / raw)
        return Settings.model_validate(data)


def _env_int(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float | None = None) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, tuple[str, str, type]] = {
        "mt5.terminal_path": ("MT5_MCP_MT5_TERMINAL_PATH", "mt5.terminal_path", str),
        "mt5.login": ("MT5_MCP_MT5_LOGIN", "mt5.login", int),
        "mt5.password": ("MT5_MCP_MT5_PASSWORD", "mt5.password", str),
        "mt5.server": ("MT5_MCP_MT5_SERVER", "mt5.server", str),
        "deriv.enabled": ("MT5_MCP_DERIV_ENABLED", "deriv.enabled", str),
        "deriv.api_token": ("MT5_MCP_DERIV_API_TOKEN", "deriv.api_token", str),
        "deriv_mt5.terminal_path": (
            "MT5_MCP_DERIV_MT5_TERMINAL_PATH",
            "deriv_mt5.terminal_path",
            str,
        ),
        "deriv_mt5.login": ("MT5_MCP_DERIV_MT5_LOGIN", "deriv_mt5.login", int),
        "deriv_mt5.password": ("MT5_MCP_DERIV_MT5_PASSWORD", "deriv_mt5.password", str),
        "deriv_mt5.server": ("MT5_MCP_DERIV_MT5_SERVER", "deriv_mt5.server", str),
        "risk.default_risk_percent": (
            "MT5_MCP_RISK_DEFAULT",
            "risk.default_risk_percent",
            float,
        ),
        "risk.max_risk_percent": ("MT5_MCP_RISK_MAX", "risk.max_risk_percent", float),
        "risk.min_risk_reward": ("MT5_MCP_RISK_MIN_RR", "risk.min_risk_reward", float),
        "server.transport": ("MT5_MCP_TRANSPORT", "server.transport", str),
        "logging.level": ("MT5_MCP_LOG_LEVEL", "logging.level", str),
    }
    for env_name, path, cast in mapping.values():
        raw = os.getenv(env_name)
        if raw is None or raw == "":
            continue
        section, key = path.split(".", 1)
        try:
            if cast is str and key == "enabled":
                data[section][key] = _env_bool(env_name, data[section].get(key, False))
            else:
                data[section][key] = cast(raw)
        except (KeyError, TypeError, ValueError):
            continue
    return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings_path = BASE_DIR / "settings.json"
    data: dict[str, Any] = {
        "server": {},
        "mt5": {},
        "risk": {},
        "strategy": {},
        "memory": {},
        "logging": {},
        "boom_crash": {},
        "deriv": {},
        "deriv_mt5": {},
    }
    if settings_path.exists():
        try:
            with settings_path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                for section, section_data in data.items():
                    if section in loaded and isinstance(loaded[section], dict):
                        section_data.update(loaded[section])
        except (OSError, json.JSONDecodeError) as exc:
            logging.getLogger("mt5_mcp.config").warning(
                "Could not read settings.json: %s", exc
            )
    data = _apply_env_overrides(data)
    settings = Settings.model_validate(data)
    return settings.resolve_paths()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


def setup_logging(settings: Settings | None = None) -> logging.Logger:
    settings = settings or get_settings()
    log_dir = Path(settings.logging.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("mt5_mcp")
    root.setLevel(settings.logging.level.upper())
    root.handlers.clear()
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_dir / "mt5_mcp.log",
        maxBytes=settings.logging.max_bytes,
        backupCount=settings.logging.backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    # Also add an error-only handler
    err_handler = RotatingFileHandler(
        log_dir / "errors.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    err_handler.setLevel(logging.WARNING)
    err_handler.setFormatter(fmt)
    root.addHandler(err_handler)
    return root

"""Application settings using Pydantic BaseSettings."""

from __future__ import annotations

from datetime import time
from enum import Enum
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Timeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


TIMEFRAME_HIERARCHY: dict[Timeframe, int] = {
    Timeframe.D1: 5,
    Timeframe.H4: 4,
    Timeframe.H1: 3,
    Timeframe.M15: 2,
    Timeframe.M5: 1,
    Timeframe.M1: 0,
}


class SessionWindow:
    def __init__(self, name: str, start: time, end: time) -> None:
        self.name = name
        self.start = start
        self.end = end

    def contains(self, utc_time: time) -> bool:
        if self.start <= self.end:
            return self.start <= utc_time <= self.end
        return utc_time >= self.start or utc_time <= self.end


SESSIONS: dict[str, SessionWindow] = {
    "asian": SessionWindow("asian", time(0, 0), time(8, 0)),
    "london": SessionWindow("london", time(7, 0), time(16, 0)),
    "new_york": SessionWindow("new_york", time(12, 0), time(21, 0)),
    "overlap": SessionWindow("overlap", time(12, 0), time(16, 0)),
}


class ExperimentDefaults(BaseSettings):
    train_ratio: float = 0.6
    validation_ratio: float = 0.2
    test_ratio: float = 0.2
    monte_carlo_iterations: int = 1000

    @field_validator("train_ratio", "validation_ratio", "test_ratio")
    @classmethod
    def _validate_ratios(cls, v: float) -> float:
        if not 0.0 < v < 1.0:
            raise ValueError("Ratio must be between 0 and 1 (exclusive)")
        return v


class KillSwitchThresholds(BaseSettings):
    max_drawdown: float = 0.10
    daily_loss_limit: float = 0.03

    @field_validator("max_drawdown", "daily_loss_limit")
    @classmethod
    def _validate_pct(cls, v: float) -> float:
        if not 0.0 < v < 1.0:
            raise ValueError("Threshold must be between 0 and 1 (exclusive)")
        return v


class DatabaseSettings(BaseSettings):
    url: str = "sqlite+aiosqlite:///trading.db"
    pool_size: int = 5
    max_overflow: int = 10


class RedisSettings(BaseSettings):
    url: str | None = None


class Settings(BaseSettings):
    model_config = {"env_prefix": "TRADING_AI_", "env_nested_delimiter": "__"}

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    experiment: ExperimentDefaults = Field(default_factory=ExperimentDefaults)
    kill_switch: KillSwitchThresholds = Field(default_factory=KillSwitchThresholds)

    supported_symbols: list[str] = Field(
        default=["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "XAUUSD"]
    )
    supported_timeframes: list[Timeframe] = Field(
        default=[Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1]
    )

    risk_per_trade: float = 0.005
    min_risk_reward: float = 3.0

    # Recursive self-learning automation
    auto_self_learn: bool = True
    auto_self_learn_interval_s: float = 3600.0
    self_learn_population_size: int = 3
    self_learn_multi_regime: bool = True

    # Prop-firm pair constraints
    prop_allowed_symbols: list[str] = Field(
        default=["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]
    )
    max_spread_pips: float = 1.5       # reject if current spread > this
    min_adr_pips: float = 40.0         # require minimum Average Daily Range
    max_adr_pips: float = 300.0        # avoid extremely volatile days
    session_filter_enabled: bool = True  # only trade during allowed sessions

    @field_validator("risk_per_trade")
    @classmethod
    def _validate_risk(cls, v: float) -> float:
        if not 0.0 < v < 1.0:
            raise ValueError("risk_per_trade must be between 0 and 1 (exclusive)")
        return v

    @field_validator("min_risk_reward")
    @classmethod
    def _validate_min_rr(cls, v: float) -> float:
        if v < 1.0:
            raise ValueError("min_risk_reward must be >= 1.0")
        return v

    def get_session_for_time(self, utc_time: time) -> list[str]:
        return [name for name, window in SESSIONS.items() if window.contains(utc_time)]


settings = Settings()

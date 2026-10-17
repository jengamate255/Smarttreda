"""Engine configuration: paths, timeframes, detector parameters.

All detector thresholds live here (never hard-coded in loops) so experiments
can pin and vary them deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "ml_engine_data"
RAW_DIR = DATA_DIR / "raw"
FEATURES_DIR = DATA_DIR / "features"
DATASETS_DIR = DATA_DIR / "datasets"
MODELS_DIR = DATA_DIR / "models"
EXPERIMENTS_DB = DATA_DIR / "experiments.db"
REPORTS_DIR = DATA_DIR / "reports"
VIZ_DIR = DATA_DIR / "viz"

TIMEFRAME_MINUTES: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M20": 20,
    "M30": 30,
    "H1": 60,
    "H2": 120,
    "H3": 180,
    "H4": 240,
    "H6": 360,
    "H8": 480,
    "H12": 720,
    "D1": 1440,
}


@dataclass(frozen=True)
class SwingConfig:
    left: int = 5
    right: int = 5


@dataclass(frozen=True)
class ZoneConfig:
    atr_period: int = 14
    base_bars: int = 4
    impulse_mult: float = 1.5
    # Research default: scan the whole frame (0 = no lookback limit). The
    # mt5_mcp live detector uses 400; here a limit silently truncated the
    # dataset to the final 400 bars (found via data audit 2026-09-03).
    lookback: int = 0
    max_base_range_atr: float = 1.0
    max_base_body_atr: float = 0.5
    breach_atr: float = 0.25


@dataclass(frozen=True)
class LiquidityConfig:
    atr_period: int = 14
    equal_tolerance_atr: float = 0.2
    sweep_min_wick_atr: float = 1.0
    swing_lookback: int = 5


@dataclass(frozen=True)
class PriceActionConfig:
    atr_period: int = 14
    engulf_body_mult: float = 1.1
    engulf_body_range_frac: float = 0.3
    pin_body_max_frac: float = 0.35
    pin_wick_body_mult: float = 2.0
    pin_wick_range_frac: float = 0.5


@dataclass(frozen=True)
class BreakoutConfig:
    atr_period: int = 14
    range_bars: int = 20
    buffer_atr: float = 0.12
    retest_max_bars: int = 12
    retest_tolerance_atr: float = 0.5


@dataclass(frozen=True)
class FeatureConfig:
    ema_periods: tuple[int, ...] = (20, 50, 100, 200)
    rsi_period: int = 14
    atr_period: int = 14
    roc_period: int = 10
    volume_avg_period: int = 20
    volatility_window: int = 20
    atr_pct_window: int = 200


@dataclass(frozen=True)
class RegimeConfig:
    atr_period: int = 14
    atr_pct_window: int = 200
    high_vol_pct: float = 80.0
    low_vol_pct: float = 20.0
    ema_fast: int = 50
    ema_slow: int = 200
    adx_period: int = 14


@dataclass(frozen=True)
class LabelConfig:
    max_hold_bars: int = 200
    barrier_targets: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0)


@dataclass(frozen=True)
class SetupConfig:
    """Setup composer thresholds."""

    # A setup needs: structure alignment + zone OR liquidity trigger +
    # price-action confirmation, all causally visible at the trigger bar.
    require_structure_alignment: bool = True
    zone_max_dist_atr: float = 1.5
    sweep_max_age_bars: int = 10
    confirmation_max_age_bars: int = 3
    stop_atr_buffer: float = 1.0
    min_risk_reward: float = 1.5
    default_rr: float = 2.0


@dataclass(frozen=True)
class WalkForwardConfig:
    train_bars: int = 1500
    test_bars: int = 400
    step_bars: int = 400
    purge_bars: int = 250
    min_train_setups: int = 40
    min_test_setups: int = 10


@dataclass(frozen=True)
class BacktestConfig:
    spread_points: float = 15.0
    slippage_points: float = 1.0
    commission_per_lot: float = 7.0
    start_balance: float = 10_000.0
    risk_percent: float = 0.5
    max_concurrent: int = 1
    min_probability: float = 0.5
    point: float = 0.00001
    contract_size: float = 100_000.0
    pip_value_per_lot: float = 10.0
    pip_size: float = 0.0001


@dataclass(frozen=True)
class EngineConfig:
    """Root configuration object."""

    timeframes: tuple[str, ...] = ("H4", "H1", "M15")  # high -> low
    symbols: tuple[str, ...] = (
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "XAUUSD",
        "XAGUSD",
        "JP225",
        "SPX500",
        "USOIL",
    )
    swing: SwingConfig = field(default_factory=SwingConfig)
    zone: ZoneConfig = field(default_factory=ZoneConfig)
    liquidity: LiquidityConfig = field(default_factory=LiquidityConfig)
    price_action: PriceActionConfig = field(default_factory=PriceActionConfig)
    breakout: BreakoutConfig = field(default_factory=BreakoutConfig)
    feature: FeatureConfig = field(default_factory=FeatureConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    label: LabelConfig = field(default_factory=LabelConfig)
    setup: SetupConfig = field(default_factory=SetupConfig)
    walk_forward: WalkForwardConfig = field(default_factory=WalkForwardConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)

    @property
    def base_timeframe(self) -> str:
        """Lowest timeframe = signal/composition frame."""
        return self.timeframes[-1]

    @property
    def higher_timeframes(self) -> tuple[str, ...]:
        return self.timeframes[:-1]


DEFAULT_CONFIG = EngineConfig()


def ensure_dirs() -> None:
    for path in (
        DATA_DIR,
        RAW_DIR,
        FEATURES_DIR,
        DATASETS_DIR,
        MODELS_DIR,
        REPORTS_DIR,
        VIZ_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)

"""Risk management module — position sizing, drawdown guards, and trade approval."""

from forex_rl.src.risk.risk_manager import RiskConfig, RiskManager
from forex_rl.src.risk.position_sizing import (
    anti_martingale,
    fixed_fractional,
    kelly_criterion,
    volatility_adjusted,
)

__all__ = [
    "RiskConfig",
    "RiskManager",
    "PositionSizer",
    "anti_martingale",
    "fixed_fractional",
    "kelly_criterion",
    "volatility_adjusted",
]


class PositionSizer:
    """Namespace alias for position sizing functions.

    Provides a class-based interface that mirrors the module-level functions
    for codebases that prefer object access::

        sizer = PositionSizer()
        lots = sizer.fixed_fractional(balance, risk_pct, sl_distance, tick_value)
    """

    @staticmethod
    def fixed_fractional(
        balance: float,
        risk_pct: float,
        sl_distance: float,
        tick_value: float,
    ) -> float:
        return fixed_fractional(balance, risk_pct, sl_distance, tick_value)

    @staticmethod
    def kelly_criterion(
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> float:
        return kelly_criterion(win_rate, avg_win, avg_loss)

    @staticmethod
    def anti_martingale(
        balance: float,
        risk_pct: float,
        consecutive_wins: int,
    ) -> float:
        return anti_martingale(balance, risk_pct, consecutive_wins)

    @staticmethod
    def volatility_adjusted(
        atr: float,
        target_vol: float,
        current_vol: float,
    ) -> float:
        return volatility_adjusted(atr, target_vol, current_vol)

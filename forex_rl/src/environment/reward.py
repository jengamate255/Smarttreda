"""Modular reward functions for the forex RL trading environment.

Provides a pluggable reward system with four concrete strategies:
SimpleProfitReward, RiskAdjustedReward, SharpeReward, and ParetoReward,
plus a Portfolio tracker that maintains full account state.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ── Enumerations ────────────────────────────────────────────────────────


class TradeSide(str, Enum):
    """Trade direction."""

    BUY = "BUY"
    SELL = "SELL"


class ExitReason(str, Enum):
    """Why a position was closed."""

    STOP_LOSS = "sl"
    TAKE_PROFIT = "tp"
    MANUAL = "manual"
    TIMEOUT = "timeout"


class MarketRegime(str, Enum):
    """Synchronised with market.regime.Regime."""

    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    TRANSITION = "TRANSITION"


# ── Data containers ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class TradeResult:
    """Immutable record of a single completed trade."""

    entry_price: float
    exit_price: float
    side: TradeSide
    volume: float  # lots
    bars_held: int
    pnl: float  # absolute P&L in account currency
    pnl_pct: float  # percentage return on balance at entry
    reason: ExitReason
    symbol: str = ""
    timestamp: Any = None


@dataclass
class PortfolioState:
    """Snapshot of the portfolio at a point in time."""

    equity: float
    balance: float
    peak_equity: float
    drawdown: float  # current drawdown as fraction (0–1)
    max_drawdown: float  # worst drawdown seen so far
    total_trades: int
    wins: int
    losses: int
    open_positions: int
    daily_pnl: float = 0.0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    bars_since_last_trade: int = 999

    @property
    def win_rate(self) -> float:
        """Win rate as a fraction (0–1)."""
        if self.total_trades == 0:
            return 0.0
        return self.wins / self.total_trades

    @property
    def profit_factor(self) -> float:
        """Placeholder — actual PF requires gross win/loss sums."""
        return 0.0


@dataclass
class MarketState:
    """Minimal market context passed to reward functions."""

    regime: MarketRegime = MarketRegime.TRANSITION
    volatility_percentile: float = 0.5
    atr: float = 0.0
    trend_direction: float = 0.0  # −1 … +1


@dataclass
class RewardConfig:
    """All tuneable reward parameters with sensible defaults."""

    # ── SimpleProfitReward ─────────────────────────────────────
    simple_scale: float = 100.0
    simple_clip: float = 1.0

    # ── RiskAdjustedReward ─────────────────────────────────────
    ra_transaction_cost: float = 0.0002  # 2 pips as fraction
    ra_dd_quad_weight: float = 5.0
    ra_dd_threshold: float = 0.03  # penalty kicks in above 3 %
    ra_overtrading_window: int = 100
    ra_overtrading_limit: int = 5
    ra_overtrading_penalty: float = 0.1
    ra_revenge_window: int = 3
    ra_revenge_penalty: float = 0.3
    ra_hold_bad_limit: int = 20
    ra_hold_bad_penalty: float = 0.02
    ra_regime_bonus: float = 0.05
    ra_clip: float = 2.0

    # ── SharpeReward ───────────────────────────────────────────
    sharpe_window: int = 50
    sharpe_target: float = 1.5
    sharpe_scale: float = 0.5
    sharpe_clip: float = 2.0

    # ── ParetoReward ───────────────────────────────────────────
    pareto_w_profit: float = 0.4
    pareto_w_sharpe: float = 0.3
    pareto_w_drawdown: float = 0.3
    pareto_sharpe_window: int = 50
    pareto_target_sharpe: float = 1.5
    pareto_max_dd_limit: float = 0.15
    pareto_clip: float = 2.0


# ── Portfolio tracker ──────────────────────────────────────────────────


@dataclass
class _OpenPosition:
    """Internal bookkeeping for an open position."""

    symbol: str
    side: TradeSide
    volume: float
    entry_price: float
    entry_bar: int
    sl: float = 0.0
    tp: float = 0.0


class Portfolio:
    """Full portfolio state tracker.

    Maintains equity, balance, trade history, open positions, drawdown,
    and daily P&L.  The environment should call the mutation methods
    (``open_position``, ``close_position``) at each step.
    """

    def __init__(self, initial_balance: float = 10_000.0) -> None:
        self._initial_balance: float = initial_balance
        self._balance: float = initial_balance
        self._equity: float = initial_balance
        self._peak_equity: float = initial_balance
        self._max_drawdown: float = 0.0

        self._trade_history: list[TradeResult] = []
        self._open_positions: list[_OpenPosition] = []

        self._total_trades: int = 0
        self._wins: int = 0
        self._losses: int = 0
        self._consecutive_wins: int = 0
        self._consecutive_losses: int = 0

        self._daily_pnl: float = 0.0
        self._last_trade_bar: int = -999
        self._bars_since_last_trade: int = 999

        # Rolling windows for analytics
        self._recent_pnl: deque[float] = deque(maxlen=500)
        self._daily_pnl_history: deque[float] = deque(maxlen=252)

    # ── Mutations ──────────────────────────────────────────────

    def open_position(
        self,
        symbol: str,
        side: TradeSide,
        volume: float,
        entry_price: float,
        current_bar: int,
        sl: float = 0.0,
        tp: float = 0.0,
    ) -> None:
        """Register a new open position."""
        pos = _OpenPosition(
            symbol=symbol,
            side=side,
            volume=volume,
            entry_price=entry_price,
            entry_bar=current_bar,
            sl=sl,
            tp=tp,
        )
        self._open_positions.append(pos)
        self._bars_since_last_trade = 0
        self._last_trade_bar = current_bar

    def close_position(
        self,
        index: int,
        exit_price: float,
        current_bar: int,
        reason: ExitReason = ExitReason.MANUAL,
    ) -> TradeResult:
        """Close an open position by index and return the TradeResult."""
        if index < 0 or index >= len(self._open_positions):
            raise IndexError(f"No open position at index {index}")

        pos = self._open_positions.pop(index)
        bars_held = current_bar - pos.entry_bar

        # P&L calculation
        if pos.side == TradeSide.BUY:
            raw_pnl = (exit_price - pos.entry_price) * pos.volume
        else:
            raw_pnl = (pos.entry_price - exit_price) * pos.volume

        # Simplified: assume tick_value == volume for synthetic units.
        # In production, multiply by pip_value / tick_size from symbol_info.
        pnl = raw_pnl
        pnl_pct = pnl / self._balance if self._balance > 0 else 0.0

        # Update balance
        self._balance += pnl
        self._balance = max(self._balance, 0.0)

        # Update equity & drawdown
        self._equity = self._balance
        if self._equity > self._peak_equity:
            self._peak_equity = self._equity
        dd = 1.0 - (self._equity / self._peak_equity) if self._peak_equity > 0 else 0.0
        if dd > self._max_drawdown:
            self._max_drawdown = dd

        # Daily P&L
        self._daily_pnl += pnl
        self._recent_pnl.append(pnl)

        # Win/loss stats
        self._total_trades += 1
        if pnl >= 0:
            self._wins += 1
            self._consecutive_wins += 1
            self._consecutive_losses = 0
        else:
            self._losses += 1
            self._consecutive_losses += 1
            self._consecutive_wins = 0

        result = TradeResult(
            entry_price=pos.entry_price,
            exit_price=exit_price,
            side=pos.side,
            volume=pos.volume,
            bars_held=bars_held,
            pnl=pnl,
            pnl_pct=pnl_pct,
            reason=reason,
            symbol=pos.symbol,
        )
        self._trade_history.append(result)
        return result

    def update_equity(self, unrealised_pnl: float) -> None:
        """Update equity with floating P&L (call every bar)."""
        self._equity = self._balance + unrealised_pnl
        if self._equity > self._peak_equity:
            self._peak_equity = self._equity

    def reset_daily(self) -> None:
        """Reset daily P&L tracking (call at session boundary)."""
        self._daily_pnl_history.append(self._daily_pnl)
        self._daily_pnl = 0.0

    def tick(self) -> None:
        """Advance one bar — increments the bars-since-last-trade counter."""
        self._bars_since_last_trade += 1

    # ── Queries ────────────────────────────────────────────────

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def peak_equity(self) -> float:
        return self._peak_equity

    @property
    def drawdown(self) -> float:
        dd = 1.0 - (self._equity / self._peak_equity) if self._peak_equity > 0 else 0.0
        return max(dd, 0.0)

    @property
    def max_drawdown(self) -> float:
        return self._max_drawdown

    @property
    def trade_history(self) -> list[TradeResult]:
        return list(self._trade_history)

    @property
    def open_positions(self) -> list[_OpenPosition]:
        return list(self._open_positions)

    @property
    def total_trades(self) -> int:
        return self._total_trades

    @property
    def wins(self) -> int:
        return self._wins

    @property
    def losses(self) -> int:
        return self._losses

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def consecutive_wins(self) -> int:
        return self._consecutive_wins

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def bars_since_last_trade(self) -> int:
        return self._bars_since_last_trade

    @property
    def open_risk(self) -> float:
        """Sum of risk (distance to SL × volume) across all open positions.

        If no SL is set, risk is estimated as 2 × ATR × volume.
        """
        total = 0.0
        for pos in self._open_positions:
            if pos.sl > 0:
                dist = abs(pos.entry_price - pos.sl)
            else:
                dist = pos.entry_price * 0.01  # fallback 1 %
            total += dist * pos.volume
        return total

    def recent_pnl_series(self, window: int = 50) -> list[float]:
        """Return the last *window* P&L values."""
        return list(self._recent_pnl)[-window:]

    def stats(self) -> dict[str, float]:
        """Return a dict of all tracked metrics."""
        dd = self.drawdown
        gross_win = sum(t.pnl for t in self._trade_history if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self._trade_history if t.pnl < 0))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

        recent = self.recent_pnl_series(50)
        sharpe = 0.0
        if len(recent) > 5:
            arr = np.array(recent)
            std = float(arr.std())
            if std > 0:
                sharpe = float(arr.mean()) / std * np.sqrt(252)

        return {
            "equity": self._equity,
            "balance": self._balance,
            "peak_equity": self._peak_equity,
            "drawdown": dd,
            "max_drawdown": self._max_drawdown,
            "total_trades": self._total_trades,
            "wins": self._wins,
            "losses": self._losses,
            "win_rate": self.win_rate,
            "profit_factor": pf,
            "daily_pnl": self._daily_pnl,
            "open_positions": len(self._open_positions),
            "open_risk": self.open_risk,
            "consecutive_wins": self._consecutive_wins,
            "consecutive_losses": self._consecutive_losses,
            "bars_since_last_trade": self._bars_since_last_trade,
            "rolling_sharpe_50": sharpe,
        }

    @property
    def win_rate(self) -> float:
        if self._total_trades == 0:
            return 0.0
        return self._wins / self._total_trades

    def to_state(self) -> PortfolioState:
        """Build a PortfolioState snapshot."""
        return PortfolioState(
            equity=self._equity,
            balance=self._balance,
            peak_equity=self._peak_equity,
            drawdown=self.drawdown,
            max_drawdown=self._max_drawdown,
            total_trades=self._total_trades,
            wins=self._wins,
            losses=self._losses,
            open_positions=len(self._open_positions),
            daily_pnl=self._daily_pnl,
            consecutive_wins=self._consecutive_wins,
            consecutive_losses=self._consecutive_losses,
            bars_since_last_trade=self._bars_since_last_trade,
        )


# ── RewardFunction base class ──────────────────────────────────────────


class RewardFunction(ABC):
    """Abstract base for all reward functions."""

    @abstractmethod
    def compute(
        self,
        trade_result: TradeResult,
        portfolio_state: PortfolioState,
        market_state: MarketState,
    ) -> float:
        """Return scalar reward for the given trade outcome."""


# ── SimpleProfitReward ─────────────────────────────────────────────────


class SimpleProfitReward(RewardFunction):
    """Reward = pnl_pct × scale_factor, clipped to [−clip, +clip].

    The simplest possible shaping: good trades earn positive reward,
    bad trades earn negative reward, magnitude is proportional to return.
    """

    def __init__(self, config: Optional[RewardConfig] = None) -> None:
        self.cfg = config or RewardConfig()

    def compute(
        self,
        trade_result: TradeResult,
        portfolio_state: PortfolioState,
        market_state: MarketState,
    ) -> float:
        raw = trade_result.pnl_pct * self.cfg.simple_scale
        return float(np.clip(raw, -self.cfg.simple_clip, self.cfg.simple_clip))


# ── RiskAdjustedReward ─────────────────────────────────────────────────


class RiskAdjustedReward(RewardFunction):
    """Multi-factor reward that penalises risky and undisciplined behaviour.

    Components:
        base P&L − transaction cost − drawdown penalty
        − overtrading penalty − revenge trading penalty
        − holding bad trades penalty + regime bonus
    """

    def __init__(self, config: Optional[RewardConfig] = None) -> None:
        self.cfg = config or RewardConfig()

    def compute(
        self,
        trade_result: TradeResult,
        portfolio_state: PortfolioState,
        market_state: MarketState,
    ) -> float:
        cfg = self.cfg

        # 1. Base P&L reward
        reward = trade_result.pnl_pct

        # 2. Transaction cost penalty (spread + slippage approximation)
        reward -= cfg.ra_transaction_cost

        # 3. Drawdown penalty — quadratic, accelerates with drawdown
        dd = portfolio_state.drawdown
        if dd > cfg.ra_dd_threshold:
            excess_dd = dd - cfg.ra_dd_threshold
            reward -= cfg.ra_dd_quad_weight * excess_dd * excess_dd

        # 4. Overtrading penalty — too many trades in the window
        window = portfolio_state.total_trades
        # Use trade history length as proxy for trades in window
        n_recent = min(portfolio_state.total_trades, cfg.ra_overtrading_window)
        if n_recent > cfg.ra_overtrading_limit:
            reward -= cfg.ra_overtrading_penalty * (n_recent - cfg.ra_overtrading_limit)

        # 5. Revenge trading penalty — trade taken too soon after a loss
        if portfolio_state.bars_since_last_trade < cfg.ra_revenge_window:
            # Check if last trade was a loss
            if portfolio_state.consecutive_losses > 0:
                reward -= cfg.ra_revenge_penalty

        # 6. Holding bad trades penalty — only at exit
        if trade_result.bars_held > cfg.ra_hold_bad_limit and trade_result.pnl < 0:
            excess_bars = trade_result.bars_held - cfg.ra_hold_bad_limit
            reward -= cfg.ra_hold_bad_penalty * excess_bars

        # 7. Regime bonus — small reward for trading in the correct direction
        regime = market_state.regime
        if trade_result.side == TradeSide.BUY and regime == MarketRegime.TREND_UP:
            reward += cfg.ra_regime_bonus
        elif trade_result.side == TradeSide.SELL and regime == MarketRegime.TREND_DOWN:
            reward += cfg.ra_regime_bonus

        return float(np.clip(reward, -cfg.ra_clip, cfg.ra_clip))


# ── SharpeReward ────────────────────────────────────────────────────────


class SharpeReward(RewardFunction):
    """Reward based on rolling Sharpe ratio of recent trades.

    Penalises volatile equity curves even if the total P&L is positive.
    Reward = (rolling_sharpe − target_sharpe) × scale.
    """

    def __init__(self, config: Optional[RewardConfig] = None) -> None:
        self.cfg = config or RewardConfig()

    def compute(
        self,
        trade_result: TradeResult,
        portfolio_state: PortfolioState,
        market_state: MarketState,
    ) -> float:
        cfg = self.cfg
        # Build return series from recent trades
        # We use pnl_pct as the per-trade return
        trades = _get_recent_pnl_pct(portfolio_state, cfg.sharpe_window)

        if len(trades) < 5:
            # Not enough data — fall back to simple profit signal
            return float(np.clip(
                trade_result.pnl_pct * 50.0,
                -cfg.sharpe_clip,
                cfg.sharpe_clip,
            ))

        arr = np.array(trades, dtype=np.float64)
        mean_r = float(arr.mean())
        std_r = float(arr.std())

        if std_r < 1e-10:
            sharpe = 5.0 if mean_r > 0 else -5.0
        else:
            sharpe = mean_r / std_r * np.sqrt(252)

        reward = (sharpe - cfg.sharpe_target) * cfg.sharpe_scale
        return float(np.clip(reward, -cfg.sharpe_clip, cfg.sharpe_clip))


def _get_recent_pnl_pct(state: PortfolioState, window: int) -> list[float]:
    """Extract recent pnl_pct values from portfolio state.

    This is a helper — in production the Portfolio object would supply this.
    We approximate from win rate and average trade.
    """
    # Since PortfolioState is a snapshot, we reconstruct a rough series.
    # The real implementation would pass the actual history through.
    total = state.total_trades
    if total == 0:
        return []
    wr = state.win_rate
    # Synthetic series: wins are +wr, losses are −(1−wr), scaled by equity
    n = min(total, window)
    equity = state.equity if state.equity > 0 else 10000.0
    series = []
    for _ in range(n):
        if np.random.random() < wr:
            series.append(wr * 0.005)
        else:
            series.append(-(1.0 - wr) * 0.003)
    return series


# ── ParetoReward ────────────────────────────────────────────────────────


class ParetoReward(RewardFunction):
    """Multi-objective scalarisation combining profit, Sharpe, and drawdown.

    reward = w_profit × norm_profit + w_sharpe × norm_sharpe
             − w_drawdown × dd_penalty

    Each component is normalised to roughly [−1, 1] before weighting.
    """

    def __init__(self, config: Optional[RewardConfig] = None) -> None:
        self.cfg = config or RewardConfig()

    def compute(
        self,
        trade_result: TradeResult,
        portfolio_state: PortfolioState,
        market_state: MarketState,
    ) -> float:
        cfg = self.cfg

        # ── Profit component ──────────────────────────────────
        norm_profit = np.clip(trade_result.pnl_pct * 100.0, -1.0, 1.0)

        # ── Sharpe component ──────────────────────────────────
        trades = _get_recent_pnl_pct(portfolio_state, cfg.pareto_sharpe_window)
        if len(trades) >= 5:
            arr = np.array(trades, dtype=np.float64)
            mean_r = float(arr.mean())
            std_r = float(arr.std())
            sharpe = mean_r / std_r * np.sqrt(252) if std_r > 1e-10 else 0.0
        else:
            sharpe = 0.0

        # Normalise Sharpe to [−1, 1] assuming target range [−3, +3]
        norm_sharpe = float(np.clip(sharpe / 3.0, -1.0, 1.0))

        # ── Drawdown component ────────────────────────────────
        dd = portfolio_state.drawdown
        # Quadratic penalty: 0 at dd=0, −1 at dd=pareto_max_dd_limit
        dd_ratio = min(dd / cfg.pareto_max_dd_limit, 1.0)
        dd_penalty = dd_ratio * dd_ratio  # quadratic

        # ── Composite scalarisation ───────────────────────────
        reward = (
            cfg.pareto_w_profit * norm_profit
            + cfg.pareto_w_sharpe * norm_sharpe
            - cfg.pareto_w_drawdown * dd_penalty
        )

        return float(np.clip(reward, -cfg.pareto_clip, cfg.pareto_clip))


# ── Factory ─────────────────────────────────────────────────────────────


_REWARD_REGISTRY: dict[str, type[RewardFunction]] = {
    "simple": SimpleProfitReward,
    "risk_adjusted": RiskAdjustedReward,
    "sharpe": SharpeReward,
    "pareto": ParetoReward,
}


def create_reward(name: str, config: Optional[RewardConfig] = None) -> RewardFunction:
    """Instantiate a reward function by name.

    Supported names: ``simple``, ``risk_adjusted``, ``sharpe``, ``pareto``.
    """
    cls = _REWARD_REGISTRY.get(name.lower())
    if cls is None:
        raise ValueError(
            f"Unknown reward function {name!r}. "
            f"Available: {list(_REWARD_REGISTRY.keys())}"
        )
    return cls(config=config)

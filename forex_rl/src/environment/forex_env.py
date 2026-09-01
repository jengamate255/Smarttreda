"""Gymnasium-compatible forex trading environment with realistic execution simulation.

Provides ``ForexTradingEnv`` — a fully featured RL environment that wraps OHLCV
data with pre-computed features into a standard Gymnasium interface.  Includes
realistic spread / slippage / commission simulation, position management with
stop-loss and take-profit, and multiple reward formulations.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import gymnasium as gym
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class EnvConfig:
    """All tuneable environment parameters in a single dataclass."""

    lookback: int = 64
    initial_balance: float = 10000.0
    risk_per_trade: float = 0.005
    max_positions: int = 3
    spread_pips: float = 1.5
    slippage_pips: float = 0.5
    commission_per_lot: float = 7.0
    point_value: float = 10.0  # USD per pip per standard lot
    sl_atr_multiplier: float = 2.0
    tp_atr_multiplier: float = 3.0
    action_mode: str = "discrete"  # "discrete" | "continuous"
    reward_type: str = "risk_adjusted"  # "simple" | "risk_adjusted" | "sharpe"
    normalize_obs: bool = True
    max_episode_steps: int = 1000
    inaction_penalty: float = 0.05  # penalty per step when flat (0.05 = -$500 over 500 steps at 1.0 scale)
    contract_size: float = 100000.0  # units per lot (FX standard lot; XAUUSD=100)
    pip_size: float = 0.0001  # price increment of 1 pip (JPY pairs & XAUUSD: 0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# Portfolio tracker
# ═══════════════════════════════════════════════════════════════════════════════


class Portfolio:
    """Tracks account balance, open positions, equity history and drawdown."""

    __slots__ = (
        "initial_balance",
        "balance",
        "positions",
        "equity_curve",
        "peak_equity",
        "total_trades",
        "wins",
        "total_pnl",
        "contract_size",
    )

    def __init__(self, initial_balance: float, contract_size: float = 100000.0) -> None:
        self.initial_balance = initial_balance
        self.contract_size = contract_size
        self.balance: float = initial_balance
        self.positions: List[Dict[str, Any]] = []
        self.equity_curve: List[float] = [initial_balance]
        self.peak_equity: float = initial_balance
        self.total_trades: int = 0
        self.wins: int = 0
        self.total_pnl: float = 0.0

    # ── Queries ──────────────────────────────────────────────────────────

    def equity(self, current_price: float) -> float:
        """Total equity = balance + unrealised P&L of all open positions."""
        unrealised = sum(
            self._unrealised_pnl(p, current_price) for p in self.positions
        )
        return self.balance + unrealised

    def unrealised_pnl(self, current_price: float) -> float:
        return sum(
            self._unrealised_pnl(p, current_price) for p in self.positions
        )

    def current_drawdown(self, current_price: float) -> float:
        eq = self.equity(current_price)
        if eq > self.peak_equity:
            self.peak_equity = eq
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - eq) / self.peak_equity

    def risk_exposure(self, current_price: float) -> float:
        """Fraction of initial balance at risk across open positions."""
        total_risk = 0.0
        for p in self.positions:
            if p["sl"] is not None:
                # Per-position contract size (FX=100000, XAUUSD=100)
                risk = abs(p["entry_price"] - p["sl"]) * p["volume"] * p.get("contract_size", 100000.0)
                total_risk += risk
        return min(total_risk / self.initial_balance, 1.0) if self.initial_balance > 0 else 0.0

    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.wins / self.total_trades

    # ── Mutations ────────────────────────────────────────────────────────

    def add_position(
        self,
        side: int,
        entry_price: float,
        volume: float,
        sl: Optional[float],
        tp: Optional[float],
        commission: float,
        step: int,
    ) -> Dict[str, Any]:
        pos: Dict[str, Any] = {
            "side": side,
            "entry_price": entry_price,
            "volume": volume,
            "sl": sl,
            "tp": tp,
            "commission": commission,
            "open_step": step,
            "bars_held": 0,
            "contract_size": self.contract_size,
        }
        # Commission is round-trip and charged once at close (see
        # ExecutionSimulator.commission). Do NOT deduct here — it was
        # previously double-charged (entry + exit).
        self.positions.append(pos)
        return pos

    def close_position(
        self,
        idx: int,
        exit_price: float,
        commission: float,
    ) -> float:
        pos = self.positions[idx]
        pnl = self._unrealised_pnl(pos, exit_price) - commission
        self.balance += pnl
        self.total_trades += 1
        if pnl > 0:
            self.wins += 1
        self.total_pnl += pnl
        del self.positions[idx]
        return pnl

    def close_all(self, exit_price: float, commission_per_pos: float) -> float:
        total_pnl = 0.0
        while self.positions:
            total_pnl += self.close_position(0, exit_price, commission_per_pos)
        return total_pnl

    def record_equity(self, equity: float) -> None:
        self.equity_curve.append(equity)
        if equity > self.peak_equity:
            self.peak_equity = equity

    def increment_bars(self) -> None:
        for p in self.positions:
            p["bars_held"] += 1

    # ── Internal ─────────────────────────────────────────────────────────

    @staticmethod
    def _unrealised_pnl(pos: Dict[str, Any], current_price: float) -> float:
        direction = pos["side"]  # +1 long, -1 short
        # Per-position contract size (FX=100000 units, XAUUSD=100 oz).
        return direction * (current_price - pos["entry_price"]) * pos["volume"] * pos.get("contract_size", 100000.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Execution simulator
# ═══════════════════════════════════════════════════════════════════════════════


class ExecutionSimulator:
    """Simulates realistic order execution: spread, slippage, commission."""

    __slots__ = (
        "spread_pips",
        "slippage_pips",
        "commission_per_lot",
        "point_value",
        "min_sl_distance_pips",
        "pip_size",
        "contract_size",
    )

    def __init__(
        self,
        spread_pips: float = 1.5,
        slippage_pips: float = 0.5,
        commission_per_lot: float = 7.0,
        point_value: float = 10.0,
        min_sl_distance_pips: float = 5.0,
        pip_size: float = 0.0001,
        contract_size: float = 100000.0,
    ) -> None:
        self.spread_pips = spread_pips
        self.slippage_pips = slippage_pips
        self.commission_per_lot = commission_per_lot
        self.point_value = point_value
        self.min_sl_distance_pips = min_sl_distance_pips
        self.pip_size = pip_size
        self.contract_size = contract_size

    def entry_price(self, mid_price: float, side: int) -> float:
        """Return the execution price including spread and slippage."""
        half_spread = self.spread_pips / 2.0
        slippage = self.slippage_pips * (0.5 + 0.5 * np.random.random())
        if side == 1:  # long — buy at ask
            return mid_price + (half_spread + slippage) * self.pip_size
        else:  # short — sell at bid
            return mid_price - (half_spread + slippage) * self.pip_size

    def exit_price(self, mid_price: float, side: int) -> float:
        """Return the exit price including spread and slippage."""
        half_spread = self.spread_pips / 2.0
        slippage = self.slippage_pips * (0.5 + 0.5 * np.random.random())
        if side == 1:  # long — sell at bid
            return mid_price - (half_spread + slippage) * self.pip_size
        else:  # short — buy at ask
            return mid_price + (half_spread + slippage) * self.pip_size

    def commission(self, volume: float) -> float:
        """Round-trip commission for *volume* lots.

        Charged ONCE per trade (at close) to match `close_position`'s
        `pnl = _unrealised_pnl - commission`. Previously it was deducted at
        BOTH add_position and close_position (double-charged).
        """
        return self.commission_per_lot * volume

    def adjust_sl(self, sl: float, entry: float, side: int, atr: float) -> Optional[float]:
        """Enforce minimum SL distance; return adjusted SL or None."""
        min_dist = self.min_sl_distance_pips * self.pip_size
        dist = abs(entry - sl)
        if dist < min_dist:
            dist = max(min_dist, atr * 0.5)
        if side == 1:
            return entry - dist
        else:
            return entry + dist

    def adjust_tp(self, tp: float, entry: float, side: int, atr: float) -> Optional[float]:
        min_dist = self.min_sl_distance_pips * self.pip_size
        dist = abs(tp - entry)
        if dist < min_dist:
            dist = max(min_dist, atr * 1.0)
        if side == 1:
            return entry + dist
        else:
            return entry - dist

    def lot_size(self, equity: float, risk_pct: float, sl_pips: float) -> float:
        """Risk-based lot sizing, contract-aware.

        USD risk per pip per lot = contract_size * pip_size
        (FX: 100000*0.0001 = $10; XAUUSD: 100*0.01 = $1).
        """
        if sl_pips <= 0:
            return 0.01
        risk_amount = equity * risk_pct
        usd_per_pip = self.contract_size * self.pip_size
        if usd_per_pip <= 0:
            usd_per_pip = self.point_value
        lot = risk_amount / (sl_pips * usd_per_pip)
        return max(0.01, round(lot, 2))


# ═══════════════════════════════════════════════════════════════════════════════
# Forex Trading Environment
# ═══════════════════════════════════════════════════════════════════════════════

# Market feature column names (47 total)
_MARKET_FEATURE_NAMES: List[str] = [
    # From FeatureEngine (28)
    "returns", "log_returns", "candle_body", "upper_wick", "lower_wick", "candle_range",
    "ema_20", "ema_50", "ema_100", "ema_200", "ema_slope_20", "distance_from_ema_50",
    "rsi_14", "macd_line", "macd_signal", "macd_histogram", "roc_10",
    "atr_14", "rolling_volatility_20", "volatility_percentile",
    "session_asian", "session_london", "session_newyork", "session_overlap",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    # From MarketStructure (4)
    "structure_state", "bos_signal", "choch_signal", "trend_strength",
    # From SupplyDemandDetector (6)
    "nearest_demand_dist", "nearest_supply_dist",
    "demand_zone_strength", "supply_zone_strength",
    "in_demand_zone", "in_supply_zone",
    # From RegimeDetector (9)
    "regime_trend_up", "regime_trend_down", "regime_range",
    "regime_high_vol", "regime_low_vol", "regime_transition",
    "regime_strength", "adx_value", "trend_direction",
]

_ACCOUNT_FEATURE_COUNT = 6

_ACTION_MAP = {
    0: "sell",
    1: "hold",
    2: "buy",
}


class ForexTradingEnv(gym.Env):
    """Gymnasium-compatible forex trading environment.

    Observation: ``(lookback, n_market_features + 6)`` float32 array.
    Action: ``Discrete(3)`` or ``Box(-1, 1, (1,))``.

    The agent sees a rolling window of market features plus constant
    account-state features.  It may open / close / hold a position.
    Execution includes spread, slippage and commission.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data (columns ``open, high, low, close, volume``; DatetimeIndex).
    config : EnvConfig
        All tuneable parameters.
    features : np.ndarray, optional
        Pre-computed feature matrix ``(n_bars, 47)``.  If *None*, the
        environment expects ``df`` to already contain all required columns.
    regime_labels : pd.Series, optional
        Regime label strings (unused in numeric features — regime is
        encoded numerically via ``compute_regime_features``).
    """

    metadata: Dict[str, Any] = {"render_modes": ["human"]}

    def __init__(
        self,
        df: pd.DataFrame,
        config: Optional[EnvConfig] = None,
        features: Optional[np.ndarray] = None,
        regime_labels: Optional[pd.Series] = None,
        feature_names: Optional[List[str]] = None,
    ) -> None:
        super().__init__()
        self.cfg = config or EnvConfig()

        # ── Store raw data ──────────────────────────────────────────────
        self.df = df.reset_index(drop=True)
        self.n_bars = len(self.df)
        self.close = self.df["close"].values.astype(np.float64)
        self.high = self.df["high"].values.astype(np.float64)
        self.low = self.df["low"].values.astype(np.float64)
        self.open = self.df["open"].values.astype(np.float64)

        # ── Feature matrix ─────────────────────────────────────────────
        if features is not None:
            self.features = np.asarray(features, dtype=np.float32)
        else:
            self.features = self._extract_features_from_df()

        # Column names for named-feature access (regime gating etc.)
        self._feature_names: List[str] = list(feature_names) if feature_names else []

        self.n_market_features = self.features.shape[1]
        self.n_total_features = self.n_market_features + _ACCOUNT_FEATURE_COUNT

        # ── Normalisation stats (computed once) ─────────────────────────
        if self.cfg.normalize_obs:
            self._obs_mean: Optional[np.ndarray] = None
            self._obs_std: Optional[np.ndarray] = None
            self._compute_norm_stats()
        else:
            self._obs_mean = None
            self._obs_std = None

        # ── Spaces ─────────────────────────────────────────────────────
        obs_shape = (self.cfg.lookback, self.n_total_features)
        self.observation_space = gym.spaces.Box(
            low=-np.inf, high=np.inf, shape=obs_shape, dtype=np.float32,
        )
        if self.cfg.action_mode == "discrete":
            self.action_space = gym.spaces.Discrete(3)
        else:
            self.action_space = gym.spaces.Box(
                low=-1.0, high=1.0, shape=(1,), dtype=np.float32,
            )

        # ── Execution engine ───────────────────────────────────────────
        self.exec_sim = ExecutionSimulator(
            spread_pips=self.cfg.spread_pips,
            slippage_pips=self.cfg.slippage_pips,
            commission_per_lot=self.cfg.commission_per_lot,
            point_value=self.cfg.point_value,
            pip_size=self.cfg.pip_size,
            contract_size=self.cfg.contract_size,
        )

        # ── Runtime state (set in reset) ──────────────────────────────
        self.portfolio: Optional[Portfolio] = None
        self.current_step: int = 0
        self.episode_step: int = 0
        self.current_price: float = 0.0
        self.current_atr: float = 0.0
        self.done: bool = False
        self._eval_mode: bool = False
        self._eval_start: int = 0

        # ── Reward bookkeeping ─────────────────────────────────────────
        self._prev_equity: float = self.cfg.initial_balance
        self._reward_buffer: List[float] = []

        logger.info(
            "ForexTradingEnv created: bars=%d  lookback=%d  features=%d  "
            "action=%s  reward=%s",
            self.n_bars,
            self.cfg.lookback,
            self.n_total_features,
            self.cfg.action_mode,
            self.cfg.reward_type,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # Gymnasium API
    # ═══════════════════════════════════════════════════════════════════════

    def reset(
        self,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        super().reset(seed=seed)

        min_start = self.cfg.lookback
        max_start = self.n_bars - 2  # need at least 1 bar for current price

        if max_start <= min_start:
            raise ValueError(
                f"Not enough data: {self.n_bars} bars, need > {self.cfg.lookback + 2}"
            )

        if self._eval_mode:
            self.current_step = self._eval_start
        else:
            if self._np_random is not None:
                self.current_step = int(
                    self._np_random.integers(min_start, max_start)
                )
            else:
                self.current_step = min_start

        self.portfolio = Portfolio(self.cfg.initial_balance, contract_size=self.cfg.contract_size)
        self.episode_step = 0
        self.done = False
        self._prev_equity = self.cfg.initial_balance
        self._reward_buffer = []
        self._last_trades_closed: List[Dict[str, Any]] = []

        self.current_price = float(self.close[self.current_step])
        self.current_atr = self._get_atr(self.current_step)

        obs = self._get_observation()
        info = self._build_info()
        return obs, info

    def step(
        self, action: Union[int, np.ndarray]
    ) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        if self.done:
            raise RuntimeError("Episode finished. Call reset() before stepping.")

        # ── Parse action ────────────────────────────────────────────────
        parsed = self._parse_action(action)
        prev_equity = self.portfolio.equity(self.current_price)

        # ── Advance price ───────────────────────────────────────────────
        self.current_step += 1
        self.episode_step += 1
        self.current_price = float(self.close[self.current_step])
        self.current_atr = self._get_atr(self.current_step)

        # ── Check SL / TP on existing positions before new action ──────
        # NOTE: the trade-list reset MUST happen BEFORE _check_positions().
        # Previously the reset came AFTER, so every SL/TP closure was
        # appended then immediately wiped — balance took the loss but the
        # trade log (and PF/win-rate) never saw it. Signal/episode-end
        # closes were the only survivors, inflating PF on losing folds.
        self._last_trades_closed: List[Dict[str, Any]] = []
        self._check_positions()

        # ── Execute the new action ──────────────────────────────────────
        self._execute_action(parsed)

        # ── Increment bars held ─────────────────────────────────────────
        self.portfolio.increment_bars()

        # ── Equity & reward ─────────────────────────────────────────────
        current_equity = self.portfolio.equity(self.current_price)
        self.portfolio.record_equity(current_equity)
        reward = self._compute_reward(prev_equity, current_equity)
        self._prev_equity = current_equity

        # ── Termination ─────────────────────────────────────────────────
        terminated = self._check_terminated()
        truncated = self.episode_step >= self.cfg.max_episode_steps
        self.done = terminated or truncated

        # On episode end, force-close any open positions at market so the
        # trade log and equity curve reconcile (previously unrealized P&L
        # vanished from the trade log on truncation, inflating PF).
        if self.done and self.portfolio.positions:
            self._close_all_positions("episode_end")

        # ── Observation & info ──────────────────────────────────────────
        obs = self._get_observation()
        info = self._build_info()
        if self._last_trades_closed:
            info["trade_closed"] = self._last_trades_closed[-1]
            info["trades_closed"] = list(self._last_trades_closed)

        return obs, reward, terminated, truncated, info

    # ═══════════════════════════════════════════════════════════════════════
    # Action execution
    # ═══════════════════════════════════════════════════════════════════════

    def _parse_action(self, action: Union[int, np.ndarray]) -> str:
        if self.cfg.action_mode == "discrete":
            idx = int(action) if isinstance(action, (int, np.integer)) else int(action.item())
            idx = max(0, min(idx, 2))
            return _ACTION_MAP[idx]
        else:
            val = float(action) if isinstance(action, (int, float)) else float(action.flat[0])
            val = np.clip(val, -1.0, 1.0)
            if val < -0.1:
                return "sell"
            elif val > 0.1:
                return "buy"
            return "hold"

    def _execute_action(self, action: str) -> None:
        has_long = any(p["side"] == 1 for p in self.portfolio.positions)
        has_short = any(p["side"] == -1 for p in self.portfolio.positions)

        if action == "buy":
            # Close existing shorts first
            while has_short:
                self._close_position_by_side(-1, "signal")
                has_short = any(p["side"] == -1 for p in self.portfolio.positions)
            # Open long if under limit
            if len(self.portfolio.positions) < self.cfg.max_positions and not has_long:
                self._open_position(1)

        elif action == "sell":
            # Close existing longs first
            while has_long:
                self._close_position_by_side(1, "signal")
                has_long = any(p["side"] == 1 for p in self.portfolio.positions)
            # Open short if under limit
            if len(self.portfolio.positions) < self.cfg.max_positions and not has_short:
                self._open_position(-1)

        # "hold" → do nothing

    def _open_position(self, side: int) -> None:
        mid = self.current_price
        entry = self.exec_sim.entry_price(mid, side)
        atr = self.current_atr if self.current_atr > 0 else 1e-6

        sl_dist = atr * self.cfg.sl_atr_multiplier
        tp_dist = atr * self.cfg.tp_atr_multiplier

        if side == 1:
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        sl = self.exec_sim.adjust_sl(sl, entry, side, atr)
        tp = self.exec_sim.adjust_tp(tp, entry, side, atr)

        sl_pips = abs(entry - (sl or entry)) / self.exec_sim.pip_size
        if sl_pips <= 0:
            sl_pips = self.cfg.sl_atr_multiplier * atr / self.exec_sim.pip_size

        volume = self.exec_sim.lot_size(
            self.portfolio.equity(mid), self.cfg.risk_per_trade, sl_pips
        )
        if volume < 0.01:
            return

        comm = self.exec_sim.commission(volume)
        self.portfolio.add_position(side, entry, volume, sl, tp, comm, self.current_step)

    def _close_position_by_side(self, side: int, reason: str) -> None:
        for i, pos in enumerate(self.portfolio.positions):
            if pos["side"] == side:
                exit_px = self.exec_sim.exit_price(self.current_price, side)
                comm = self.exec_sim.commission(pos["volume"])
                entry_price = pos["entry_price"]
                pnl = pos["side"] * (exit_px - entry_price) * pos["volume"] * pos.get("contract_size", 100000.0) - comm
                self._last_trades_closed.append({
                    "entry_price": entry_price,
                    "exit_price": exit_px,
                    "side": "BUY" if side == 1 else "SELL",
                    "pnl": pnl,
                    "pnl_pct": pnl / self.portfolio.balance * 100 if self.portfolio.balance > 0 else 0,
                    "duration": pos.get("bars_held", 0),
                    "slippage": abs(exit_px - self.current_price),
                    "spread_cost": self.exec_sim.spread_pips,
                    "reason": reason,
                })
                self.portfolio.close_position(i, exit_px, comm)
                return

    def _close_all_positions(self, reason: str) -> None:
        if not self.portfolio.positions:
            return
        # Close each position at ONE deterministic per-position exit price so
        # the trade record and the portfolio book the same fill. Previously
        # two different randomised exits were drawn (record vs balance) which
        # introduced reconciliation drift. Episode-end exits skip slippage
        # (deterministic mid-fill) for exact reconciliation.
        while self.portfolio.positions:
            pos = self.portfolio.positions[0]
            exit_px = self.current_price
            comm = self.exec_sim.commission(pos["volume"])
            entry_price = pos["entry_price"]
            pnl = pos["side"] * (exit_px - entry_price) * pos["volume"] * pos.get("contract_size", 100000.0) - comm
            self._last_trades_closed.append({
                "entry_price": entry_price,
                "exit_price": exit_px,
                "side": "BUY" if pos["side"] == 1 else "SELL",
                "pnl": pnl,
                "pnl_pct": pnl / self.portfolio.balance * 100 if self.portfolio.balance > 0 else 0,
                "duration": pos.get("bars_held", 0),
                "slippage": 0.0,
                "spread_cost": self.exec_sim.spread_pips,
                "reason": reason,
            })
            self.portfolio.close_position(0, exit_px, comm)

    # ═══════════════════════════════════════════════════════════════════════
    # SL / TP checking
    # ═══════════════════════════════════════════════════════════════════════

    def _check_positions(self) -> None:
        to_close: List[Tuple[int, str]] = []
        for i, pos in enumerate(self.portfolio.positions):
            bar_high = self.high[self.current_step]
            bar_low = self.low[self.current_step]

            if pos["side"] == 1:  # long
                if pos["sl"] is not None and bar_low <= pos["sl"]:
                    to_close.append((i, "sl"))
                elif pos["tp"] is not None and bar_high >= pos["tp"]:
                    to_close.append((i, "tp"))
            else:  # short
                if pos["sl"] is not None and bar_high >= pos["sl"]:
                    to_close.append((i, "sl"))
                elif pos["tp"] is not None and bar_low <= pos["tp"]:
                    to_close.append((i, "tp"))

        # Close in reverse order to keep indices valid
        for idx, reason in reversed(to_close):
            if idx < len(self.portfolio.positions):
                if reason == "sl":
                    exit_px = self.portfolio.positions[idx]["sl"]
                else:
                    exit_px = self.portfolio.positions[idx]["tp"]
                pos = self.portfolio.positions[idx]
                comm = self.exec_sim.commission(pos["volume"])
                entry_price = pos["entry_price"]
                pnl = pos["side"] * (exit_px - entry_price) * pos["volume"] * pos.get("contract_size", 100000.0) - comm
                self._last_trades_closed.append({
                    "entry_price": entry_price,
                    "exit_price": exit_px,
                    "side": "BUY" if pos["side"] == 1 else "SELL",
                    "pnl": pnl,
                    "pnl_pct": pnl / self.portfolio.balance * 100 if self.portfolio.balance > 0 else 0,
                    "duration": pos.get("bars_held", 0),
                    "slippage": abs(exit_px - exit_px),  # SL/TP is exact
                    "spread_cost": self.exec_sim.spread_pips,
                    "reason": reason,
                })
                self.portfolio.close_position(idx, exit_px, comm)

    # ═══════════════════════════════════════════════════════════════════════
    # Observation
    # ═══════════════════════════════════════════════════════════════════════

    def _get_observation(self) -> np.ndarray:
        start = self.current_step - self.cfg.lookback
        end = self.current_step
        market = self.features[start:end].astype(np.float32)

        # Handle NaN: forward-fill then zero-fill
        if np.any(np.isnan(market)):
            self._fillna_market(market)

        # Account features (broadcast to every row)
        acct = self._account_features()
        acct_block = np.tile(acct, (self.cfg.lookback, 1))

        obs = np.concatenate([market, acct_block], axis=1)

        if self.cfg.normalize_obs and self._obs_mean is not None:
            obs = (obs - self._obs_mean) / self._obs_std

        return obs

    def _account_features(self) -> np.ndarray:
        """Return (n_account_features,) vector describing current account state."""
        eq = self.portfolio.equity(self.current_price)
        init_bal = self.cfg.initial_balance

        pos_dir = 0.0
        unreal_pnl = 0.0
        bars_in_trade = 0.0
        if self.portfolio.positions:
            pos_dir = float(self.portfolio.positions[0]["side"])
            unreal_pnl = self.portfolio.unrealised_pnl(self.current_price) / init_bal
            bars_in_trade = float(self.portfolio.positions[0]["bars_held"])
            max_bars = float(self.cfg.max_episode_steps)
            bars_in_trade = min(bars_in_trade / max_bars, 1.0)

        return np.array([
            pos_dir,
            np.clip(unreal_pnl, -1.0, 1.0),
            bars_in_trade,
            eq / init_bal if init_bal > 0 else 1.0,
            self.portfolio.current_drawdown(self.current_price),
            self.portfolio.risk_exposure(self.current_price),
        ], dtype=np.float32)

    def _fillna_market(self, arr: np.ndarray) -> None:
        """Forward-fill then zero-fill NaN values in-place."""
        n_rows, n_cols = arr.shape
        for c in range(n_cols):
            col = arr[:, c]
            nans = np.isnan(col)
            if not np.any(nans):
                continue
            # Forward fill
            mask = ~nans
            if np.any(mask):
                idx = np.where(mask, np.arange(n_rows), 0)
                np.maximum.accumulate(idx, out=idx)
                arr[:, c] = col[idx]
            # Zero-fill remaining leading NaNs
            nans = np.isnan(arr[:, c])
            if np.any(nans):
                arr[nans, c] = 0.0

    def _compute_norm_stats(self) -> None:
        """Compute mean/std over the full (market + account) observation.

        Account features are synthetic so we use fixed reasonable ranges.
        The mean/std are computed over a (lookback, n_total_features) window
        that mimics a real observation.
        """
        n = min(self.n_bars, 2000)
        sample = self.features[:n].copy()
        self._fillna_market(sample)

        # Account-feature fixed stats (pos, unreal_pnl, bars_held, equity_ratio, drawdown, risk_exp)
        acct_means = np.array([0.0, 0.0, 0.1, 1.0, 0.02, 0.05], dtype=np.float32)
        acct_stds  = np.array([1.0, 0.05, 0.2, 0.1, 0.05, 0.1], dtype=np.float32)

        mkt_mean = np.mean(sample, axis=0).astype(np.float32)
        mkt_std  = np.std(sample, axis=0).astype(np.float32)
        mkt_std[mkt_std < 1e-8] = 1.0

        self._obs_mean = np.concatenate([mkt_mean, acct_means]).astype(np.float32)
        self._obs_std  = np.concatenate([mkt_std, acct_stds]).astype(np.float32)

    # ═══════════════════════════════════════════════════════════════════════
    # Reward
    # ═══════════════════════════════════════════════════════════════════════

    def _compute_reward(self, prev_equity: float, curr_equity: float) -> float:
        if self.cfg.reward_type == "simple":
            return self._reward_simple(prev_equity, curr_equity)
        elif self.cfg.reward_type == "risk_adjusted":
            return self._reward_risk_adjusted(prev_equity, curr_equity)
        elif self.cfg.reward_type == "sharpe":
            return self._reward_sharpe(prev_equity, curr_equity)
        elif self.cfg.reward_type == "regime_gated":
            return self._reward_regime_gated(prev_equity, curr_equity)
        return self._reward_simple(prev_equity, curr_equity)

    def _reward_simple(self, prev_eq: float, curr_eq: float) -> float:
        """Raw P&L reward. No clipping — uses configurable inaction_penalty."""
        pnl = curr_eq - prev_eq
        reward = pnl / self.cfg.initial_balance * 100.0
        if not self.portfolio.positions:
            reward -= self.cfg.inaction_penalty
        return float(reward)

    def _reward_risk_adjusted(self, prev_eq: float, curr_eq: float) -> float:
        """ATR-scaled P&L reward with configurable inaction penalty."""
        pnl = curr_eq - prev_eq
        reward = pnl / self.cfg.initial_balance * 100.0
        if not self.portfolio.positions:
            reward -= self.cfg.inaction_penalty
        dd = self.portfolio.current_drawdown(self.current_price)
        if dd > 0.20:
            reward -= (dd - 0.20) * 5.0
        return float(reward)

    def _reward_sharpe(self, prev_eq: float, curr_eq: float) -> float:
        ret = (curr_eq - prev_eq) / self.cfg.initial_balance
        self._reward_buffer.append(ret)
        if len(self._reward_buffer) > 100:
            self._reward_buffer.pop(0)
        if len(self._reward_buffer) < 10:
            return self._reward_simple(prev_eq, curr_eq)
        arr = np.array(self._reward_buffer)
        mean = arr.mean()
        std = arr.std()
        sharpe = mean / std * np.sqrt(252) if std > 1e-10 else 0.0
        return float(np.clip(sharpe / 10.0, -1.0, 1.0))

    def _current_regime_trendiness(self) -> float:
        """Trendiness score in [-1, 1] at the current bar.

        Reads the regime/trend feature columns by name from the feature
        matrix (falls back to 0 if unavailable). +1 = strong uptrend,
        -1 = strong downtrend, ~0 = ranging.
        """
        names = getattr(self, "_feature_names", None) or []
        feats = self.features
        row = feats[min(self.current_step, len(feats) - 1)]

        def col(name: str, default: float = 0.0) -> float:
            if name in names:
                try:
                    v = float(row[names.index(name)])
                    return v if np.isfinite(v) else default
                except (ValueError, IndexError):
                    return default
            return default

        trend_up = col("regime_trend_up")
        trend_down = col("regime_trend_down")
        direction = col("trend_direction")
        strength = col("regime_strength", 0.0)

        # Prefer explicit trend direction * strength; fall back to
        # regime one-hot asymmetry.
        if abs(direction) > 1e-6:
            return float(np.clip(direction * max(abs(strength), 0.3), -1.0, 1.0))
        if trend_up > trend_down:
            return float(np.clip(trend_up - trend_down, 0.0, 1.0))
        if trend_down > trend_up:
            return float(np.clip(-(trend_down - trend_up), -1.0, 0.0))
        return 0.0

    def _reward_regime_gated(self, prev_eq: float, curr_eq: float) -> float:
        """Regime-gated P&L reward.

        Replaces the flat inaction penalty (which forces trading in ALL
        conditions, including ranges where the agent has no edge):
        - Flat in a TRENDING regime  -> inaction penalty (missed move)
        - Flat in a RANGING regime   -> small OK bonus (correct patience)
        - Position AGAINST the trend -> adverse-exposure penalty per step
        - Position WITH the trend    -> alignment bonus per step
        P&L still dominates; gates only shape entry timing.
        """
        pnl = curr_eq - prev_eq
        reward = pnl / self.cfg.initial_balance * 100.0

        trendiness = self._current_regime_trendiness()
        trending = abs(trendiness) > 0.25  # meaningful directional regime

        if not self.portfolio.positions:
            if trending:
                reward -= self.cfg.inaction_penalty          # flat in trend = missed move
            else:
                reward += 0.005                             # patient in range = correct
        else:
            for p in self.portfolio.positions:
                aligned = (p["side"] > 0 and trendiness > 0) or (p["side"] < 0 and trendiness < 0)
                if trending and aligned:
                    reward += 0.01                          # riding the trend
                elif trending and not aligned:
                    reward -= 0.01                          # fighting the trend
        return float(reward)

    # ═══════════════════════════════════════════════════════════════════════
    # Termination
    # ═══════════════════════════════════════════════════════════════════════

    def _check_terminated(self) -> bool:
        eq = self.portfolio.equity(self.current_price)
        # Ruin: equity dropped below 50% of initial
        if eq < self.cfg.initial_balance * 0.5:
            return True
        # Ran out of data
        if self.current_step >= self.n_bars - 1:
            return True
        return False

    # ═══════════════════════════════════════════════════════════════════════
    # Info
    # ═══════════════════════════════════════════════════════════════════════

    def _build_info(self) -> Dict[str, Any]:
        eq = self.portfolio.equity(self.current_price)
        info: Dict[str, Any] = {
            "equity": eq,
            "balance": self.portfolio.balance,
            "drawdown": self.portfolio.current_drawdown(self.current_price),
            "position": (
                {
                    "entry_price": self.portfolio.positions[0]["entry_price"],
                    "side": self.portfolio.positions[0]["side"],
                    "volume": self.portfolio.positions[0]["volume"],
                    "sl": self.portfolio.positions[0]["sl"],
                    "tp": self.portfolio.positions[0]["tp"],
                    "bars_held": self.portfolio.positions[0]["bars_held"],
                }
                if self.portfolio.positions
                else None
            ),
            "n_positions": len(self.portfolio.positions),
            "unrealised_pnl": self.portfolio.unrealised_pnl(self.current_price),
            "trades_taken": self.portfolio.total_trades,
            "win_rate": self.portfolio.win_rate(),
            "total_pnl": self.portfolio.total_pnl,
            "current_step": self.current_step,
            "episode_step": self.episode_step,
            "current_price": self.current_price,
            "current_atr": self.current_atr,
        }
        return info

    # ═══════════════════════════════════════════════════════════════════════
    # Eval mode helpers
    # ═══════════════════════════════════════════════════════════════════════

    def set_eval_mode(self, start_step: Optional[int] = None) -> None:
        """Enable deterministic evaluation: sequential start from *start_step*."""
        self._eval_mode = True
        if start_step is not None:
            self._eval_start = max(self.cfg.lookback, start_step)
        else:
            self._eval_start = self.cfg.lookback

    def set_train_mode(self) -> None:
        """Disable eval mode (random start positions)."""
        self._eval_mode = False

    # ═══════════════════════════════════════════════════════════════════════
    # Feature extraction from DataFrame
    # ═══════════════════════════════════════════════════════════════════════

    def _extract_features_from_df(self) -> np.ndarray:
        """Build the 47-feature matrix from DataFrame columns.

        Attempts to import and run the full feature pipeline.  Falls back
        to a minimal feature set if the market modules are unavailable.
        """
        try:
            return self._build_features_full_pipeline()
        except Exception:
            logger.warning(
                "Full feature pipeline failed, falling back to minimal features",
                exc_info=True,
            )
            return self._build_features_minimal()

    def _build_features_full_pipeline(self) -> np.ndarray:
        from forex_rl.src.market.indicators import FeatureEngine
        from forex_rl.src.market.structure import MarketStructure
        from forex_rl.src.market.supply_demand import SupplyDemandDetector
        from forex_rl.src.market.regime import RegimeDetector, RegimeConfig

        ohlcv = self.df[["open", "high", "low", "close", "volume"]].copy()
        if not isinstance(ohlcv.index, pd.DatetimeIndex):
            ohlcv.index = pd.date_range("2020-01-01", periods=len(ohlcv), freq="1h", tz="UTC")

        fe = FeatureEngine()
        enriched = fe.compute_all(ohlcv)
        market_feats = enriched[fe.feature_names()].values.astype(np.float32)

        ms = MarketStructure()
        struct_feats = ms.compute_structure_features(ohlcv)
        struct_vals = struct_feats.values.astype(np.float32)

        sd = SupplyDemandDetector()
        sd_feats = sd.compute_sd_features(ohlcv)
        sd_vals = sd_feats.values.astype(np.float32)

        rd = RegimeDetector()
        regime_feats = rd.compute_regime_features(ohlcv)
        regime_vals = regime_feats.values.astype(np.float32)

        all_feats = np.hstack([market_feats, struct_vals, sd_vals, regime_vals])
        all_feats = np.nan_to_num(all_feats, nan=0.0)
        return all_feats

    def _build_features_minimal(self) -> np.ndarray:
        """Minimal 47-feature set for environments without the full pipeline."""
        close = self.df["close"].values.astype(np.float64)
        high = self.df["high"].values.astype(np.float64)
        low = self.df["low"].values.astype(np.float64)
        opn = self.df["open"].values.astype(np.float64)
        vol = self.df["volume"].values.astype(np.float64)
        n = len(close)

        features = np.zeros((n, 47), dtype=np.float32)

        # 0-5: Price features
        ret = np.zeros(n)
        ret[1:] = (close[1:] - close[:-1]) / np.where(close[:-1] != 0, close[:-1], 1.0)
        features[:, 0] = ret
        log_ret = np.zeros(n)
        log_ret[1:] = np.log(np.where(close[:-1] != 0, close[1:] / close[:-1], 1.0))
        features[:, 1] = log_ret
        body = np.where(close != 0, (close - opn) / close, 0.0)
        features[:, 2] = body
        upper = np.where(close != 0, high - np.maximum(opn, close), 0.0)
        features[:, 3] = upper
        lower = np.where(close != 0, np.minimum(opn, close) - low, 0.0)
        features[:, 4] = lower
        rng = np.where(close != 0, high - low, 0.0)
        features[:, 5] = rng

        # 6-11: Trend features
        for i, span in enumerate([20, 50, 100, 200]):
            ema = pd.Series(close).ewm(span=span, adjust=False, min_periods=1).mean().values
            features[:, 6 + i] = ema / np.where(close != 0, close, 1.0) - 1.0
        ema20 = pd.Series(close).ewm(span=20, adjust=False, min_periods=1).mean().values
        ema50 = pd.Series(close).ewm(span=50, adjust=False, min_periods=1).mean().values
        atr14 = self._compute_atr_series(high, low, close)
        slope = np.zeros(n)
        slope[5:] = (ema20[5:] - ema20[:-5]) / np.where(atr14[5:] != 0, atr14[5:], 1.0)
        features[:, 10] = slope
        features[:, 11] = np.where(ema50 != 0, (close - ema50) / ema50, 0.0)

        # 12-16: Momentum
        rsi = self._compute_rsi_series(close)
        features[:, 12] = rsi / 100.0
        ema12 = pd.Series(close).ewm(span=12, adjust=False, min_periods=1).mean().values
        ema26 = pd.Series(close).ewm(span=26, adjust=False, min_periods=1).mean().values
        macd_line = ema12 - ema26
        macd_sig = pd.Series(macd_line).ewm(span=9, adjust=False, min_periods=1).mean().values
        features[:, 13] = macd_line / np.where(close != 0, close, 1.0)
        features[:, 14] = macd_sig / np.where(close != 0, close, 1.0)
        features[:, 15] = (macd_line - macd_sig) / np.where(close != 0, close, 1.0)
        roc = np.zeros(n)
        roc[10:] = (close[10:] - close[:-10]) / np.where(close[:-10] != 0, close[:-10], 1.0)
        features[:, 16] = roc

        # 17-19: Volatility
        features[:, 17] = atr14 / np.where(close != 0, close, 1.0)
        roll_vol = pd.Series(ret).rolling(20, min_periods=10).std().fillna(0.0).values
        features[:, 18] = roll_vol
        vol_pct = pd.Series(atr14).rolling(100, min_periods=1).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1] if len(x) > 1 else 0.5,
            raw=False,
        ).fillna(0.5).values
        features[:, 19] = vol_pct

        # 20-23: Sessions (index-based)
        if isinstance(self.df.index, pd.DatetimeIndex):
            hour = self.df.index.hour
        else:
            hour = np.zeros(n, dtype=int)
        features[:, 20] = ((hour >= 0) & (hour < 8)).astype(np.float32)
        features[:, 21] = ((hour >= 7) & (hour < 16)).astype(np.float32)
        features[:, 22] = ((hour >= 12) & (hour < 21)).astype(np.float32)
        features[:, 23] = ((hour >= 12) & (hour < 16)).astype(np.float32)

        # 24-27: Time
        h = hour.astype(np.float64)
        dow = self.df.index.dayofweek.astype(np.float64) if isinstance(self.df.index, pd.DatetimeIndex) else np.zeros(n)
        features[:, 24] = np.sin(2 * np.pi * h / 24.0).astype(np.float32)
        features[:, 25] = np.cos(2 * np.pi * h / 24.0).astype(np.float32)
        features[:, 26] = np.sin(2 * np.pi * dow / 7.0).astype(np.float32)
        features[:, 27] = np.cos(2 * np.pi * dow / 7.0).astype(np.float32)

        # 28-31: Structure (placeholders — neutral state)
        features[:, 28] = 0.4  # NEUTRAL encoded as 4/5 normalized
        features[:, 29] = 0.0
        features[:, 30] = 0.0
        features[:, 31] = 0.0

        # 32-37: Supply/Demand
        features[:, 32] = 0.5
        features[:, 33] = 0.5
        features[:, 34] = 0.0
        features[:, 35] = 0.0
        features[:, 36] = 0.0
        features[:, 37] = 0.0

        # 38-46: Regime
        features[:, 38] = 0.0
        features[:, 39] = 0.0
        features[:, 40] = 1.0  # RANGE default
        features[:, 41] = 0.0
        features[:, 42] = 0.0
        features[:, 43] = 0.0
        features[:, 44] = 0.5
        features[:, 45] = 0.2
        features[:, 46] = 0.0

        return features

    # ── Indicator helpers ────────────────────────────────────────────────

    @staticmethod
    def _compute_atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
        n = len(close)
        tr = np.zeros(n)
        tr[0] = high[0] - low[0]
        for i in range(1, n):
            tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        atr = np.zeros(n)
        atr[0] = tr[0]
        alpha = 1.0 / 14.0
        for i in range(1, n):
            atr[i] = atr[i - 1] * (1 - alpha) + tr[i] * alpha
        return atr

    @staticmethod
    def _compute_rsi_series(close: np.ndarray, period: int = 14) -> np.ndarray:
        n = len(close)
        rsi = np.full(n, 50.0)
        if n < period + 1:
            return rsi
        delta = np.diff(close)
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        avg_gain = np.mean(gain[:period])
        avg_loss = np.mean(loss[:period])
        alpha = 1.0 / period
        for i in range(period, n):
            avg_gain = avg_gain * (1 - alpha) + gain[i - 1] * alpha
            avg_loss = avg_loss * (1 - alpha) + loss[i - 1] * alpha
            if avg_loss < 1e-15:
                rsi[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi[i] = 100.0 - 100.0 / (1.0 + rs)
        return rsi

    def _get_atr(self, step: int) -> float:
        if step < 14:
            return abs(self.high[step] - self.low[step])
        h = self.high[max(0, step - 13): step + 1]
        l = self.low[max(0, step - 13): step + 1]
        c = self.close[max(0, step - 13): step + 1]
        tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
        tr[0] = h[0] - l[0]
        return float(np.mean(tr))

    # ═══════════════════════════════════════════════════════════════════════
    # Rendering
    # ═══════════════════════════════════════════════════════════════════════

    def render(self) -> None:
        eq = self.portfolio.equity(self.current_price) if self.portfolio else 0.0
        pos = f"LONG" if any(p["side"] == 1 for p in (self.portfolio.positions if self.portfolio else [])) else \
              f"SHORT" if any(p["side"] == -1 for p in (self.portfolio.positions if self.portfolio else [])) else \
              "FLAT"
        print(
            f"Step {self.episode_step:4d} | "
            f"Price {self.current_price:.5f} | "
            f"Eq ${eq:,.2f} | "
            f"{pos} | "
            f"Trades {self.portfolio.total_trades if self.portfolio else 0}"
        )

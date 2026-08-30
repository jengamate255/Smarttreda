"""Risk manager — the final authority on trade approval.

Every proposed trade MUST pass through ``RiskManager.can_trade`` before
execution.  The manager enforces drawdown limits, position counts, open
risk caps, minimum R:R, and spread sanity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from forex_rl.src.risk.position_sizing import fixed_fractional

logger = logging.getLogger(__name__)


# ── Configuration ──────────────────────────────────────────────────────


@dataclass
class RiskConfig:
    """All risk parameters with defaults from the project spec."""

    risk_per_trade: float = 0.005  # 0.5 %
    max_account_drawdown: float = 0.10  # 10 %
    max_daily_drawdown: float = 0.02  # 2 %
    max_open_risk: float = 0.01  # 1 % of equity
    maximum_positions: int = 3
    minimum_rr: float = 3.0  # 1:3 reward-to-risk
    max_spread_pips: float = 5.0
    slippage_pips: float = 0.5
    commission_per_lot: float = 7.0
    max_position_size: float = 10.0  # hard lot ceiling
    min_position_size: float = 0.01
    # Time-based guards
    max_bars_in_trade: int = 200
    cooldown_bars_after_loss: int = 3
    # Correlation guard
    max_correlated_exposure: int = 2  # max positions in correlated pairs


# ── Proposed Trade ─────────────────────────────────────────────────────


@dataclass
class ProposedTrade:
    """A trade the agent wants to open."""

    symbol: str
    side: str  # "BUY" or "SELL"
    entry_price: float
    sl_price: float
    tp_price: float
    lots: float
    tick_value: float = 1.0  # monetary value per tick per lot
    tick_size: float = 0.0001  # minimum price movement
    spread_pips: float = 1.5


# ── RiskManager ─────────────────────────────────────────────────────────


class RiskManager:
    """Central risk gatekeeper — checks every constraint before approval.

    Usage::

        rm = RiskManager(RiskConfig())
        allowed, reason = rm.can_trade(portfolio_state, proposed)
        if allowed:
            lots = rm.compute_position_size(...)
            execute(lots)
    """

    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self.cfg = config or RiskConfig()
        self._daily_start_equity: float = 0.0
        self._trade_count_today: int = 0

    # ── Public API ─────────────────────────────────────────────

    def can_trade(
        self,
        portfolio_state: dict,
        proposed_trade: dict,
    ) -> tuple[bool, str]:
        """Evaluate all risk constraints against the proposed trade.

        Parameters
        ----------
        portfolio_state : dict with keys matching PortfolioState fields:
            equity, balance, drawdown, max_drawdown, total_trades,
            open_positions, daily_pnl, open_risk, consecutive_losses,
            bars_since_last_trade, and ``open_position_details`` (list of dicts).
        proposed_trade : dict with keys matching ProposedTrade fields:
            symbol, side, entry_price, sl_price, tp_price, lots,
            tick_value, tick_size, spread_pips.

        Returns
        -------
        (allowed: bool, reason: str)
        """
        cfg = self.cfg
        equity = portfolio_state.get("equity", 0.0)
        balance = portfolio_state.get("balance", 0.0)

        # ── Check 1: Daily drawdown limit ──────────────────────
        daily_pnl = portfolio_state.get("daily_pnl", 0.0)
        if self._daily_start_equity > 0:
            daily_dd = abs(daily_pnl) / self._daily_start_equity
        else:
            daily_dd = abs(daily_pnl) / equity if equity > 0 else 0.0

        if daily_dd >= cfg.max_daily_drawdown:
            return False, (
                f"DAILY_DD breached: {daily_dd:.4f} >= {cfg.max_daily_drawdown}"
            )

        # ── Check 2: Max account drawdown ─────────────────────
        max_dd = portfolio_state.get("max_drawdown", 0.0)
        if max_dd >= cfg.max_account_drawdown:
            return False, (
                f"MAX_DD breached: {max_dd:.4f} >= {cfg.max_account_drawdown}"
            )

        # ── Check 3: Max open positions ────────────────────────
        open_pos = portfolio_state.get("open_positions", 0)
        if open_pos >= cfg.maximum_positions:
            return False, (
                f"MAX_POSITIONS reached: {open_pos} >= {cfg.maximum_positions}"
            )

        # ── Check 4: Max open risk ─────────────────────────────
        open_risk = portfolio_state.get("open_risk", 0.0)
        new_risk = self._estimate_trade_risk(proposed_trade)
        total_risk = (open_risk + new_risk) / equity if equity > 0 else 1.0
        if total_risk > cfg.max_open_risk:
            return False, (
                f"OPEN_RISK exceeded: {total_risk:.4f} > {cfg.max_open_risk}"
            )

        # ── Check 5: Minimum R:R ──────────────────────────────
        entry = proposed_trade.get("entry_price", 0.0)
        sl = proposed_trade.get("sl_price", 0.0)
        tp = proposed_trade.get("tp_price", 0.0)
        if entry > 0 and sl > 0 and tp > 0:
            sl_dist = abs(entry - sl)
            tp_dist = abs(tp - entry)
            if sl_dist > 0:
                rr = tp_dist / sl_dist
                if rr < cfg.minimum_rr - 1e-6:
                    return False, (
                        f"MIN_RR not met: {rr:.2f} < {cfg.minimum_rr}"
                    )

        # ── Check 6: Spread sanity ─────────────────────────────
        spread = proposed_trade.get("spread_pips", 0.0)
        if spread > cfg.max_spread_pips:
            return False, (
                f"SPREAD too wide: {spread:.1f} > {cfg.max_spread_pips}"
            )

        # ── Check 7: SL/TP in valid direction ──────────────────
        side = proposed_trade.get("side", "")
        if side == "BUY":
            if sl >= entry:
                return False, "BUY SL must be below entry"
            if tp <= entry:
                return False, "BUY TP must be above entry"
        elif side == "SELL":
            if sl <= entry:
                return False, "SELL SL must be above entry"
            if tp >= entry:
                return False, "SELL TP must be below entry"

        # ── Check 8: Position size within bounds ───────────────
        lots = proposed_trade.get("lots", 0.0)
        if lots < cfg.min_position_size:
            return False, (
                f"LOTS too small: {lots} < {cfg.min_position_size}"
            )
        if lots > cfg.max_position_size:
            return False, (
                f"LOTS too large: {lots} > {cfg.max_position_size}"
            )

        # ── Check 9: Cooldown after consecutive losses ─────────
        cons_loss = portfolio_state.get("consecutive_losses", 0)
        bars_since = portfolio_state.get("bars_since_last_trade", 999)
        if cons_loss >= 2 and bars_since < cfg.cooldown_bars_after_loss:
            return False, (
                f"COOLDOWN: {bars_since} bars since last loss, "
                f"need {cfg.cooldown_bars_after_loss}"
            )

        return True, "APPROVED"

    def compute_position_size(
        self,
        balance: float,
        entry_price: float,
        sl_price: float,
        symbol_info: Optional[dict] = None,
    ) -> float:
        """Compute the risk-based lot size.

        Parameters
        ----------
        balance : Current account balance.
        entry_price : Planned entry price.
        sl_price : Planned stop-loss price.
        symbol_info : Optional dict with ``tick_value`` and ``tick_size``.

        Returns
        -------
        Lot size (float), clamped to [min_position_size, max_position_size].
        """
        cfg = self.cfg

        tick_value = 1.0
        tick_size = 0.0001
        if symbol_info:
            tick_value = symbol_info.get("tick_value", tick_value)
            tick_size = symbol_info.get("tick_size", tick_size)

        sl_distance = abs(entry_price - sl_price)
        if sl_distance <= 0:
            return cfg.min_position_size

        lots = fixed_fractional(
            balance=balance,
            risk_pct=cfg.risk_per_trade,
            sl_distance=sl_distance,
            tick_value=tick_value,
        )

        # Clamp
        lots = max(lots, cfg.min_position_size)
        lots = min(lots, cfg.max_position_size)

        return lots

    def validate_exit(
        self,
        position: dict,
        current_price: float,
        reason: str,
    ) -> bool:
        """Validate that a proposed exit is reasonable.

        Parameters
        ----------
        position : dict with keys: entry_price, side, sl, tp, volume, bars_held.
        current_price : Current market price.
        reason : Exit reason string ('sl', 'tp', 'manual', 'timeout').

        Returns
        -------
        True if the exit is valid.
        """
        cfg = self.cfg
        entry = position.get("entry_price", 0.0)
        side = position.get("side", "")
        bars_held = position.get("bars_held", 0)

        if reason == "timeout" and bars_held < cfg.max_bars_in_trade:
            logger.warning(
                "Timeout exit at %d bars but max is %d", bars_held, cfg.max_bars_in_trade
            )
            return False

        if reason == "sl":
            sl = position.get("sl", 0.0)
            if side == "BUY" and current_price > sl:
                logger.warning("BUY SL triggered but price %.5f > SL %.5f", current_price, sl)
                return False
            if side == "SELL" and current_price < sl:
                logger.warning("SELL SL triggered but price %.5f < SL %.5f", current_price, sl)
                return False

        if reason == "tp":
            tp = position.get("tp", 0.0)
            if side == "BUY" and current_price < tp:
                logger.warning("BUY TP triggered but price %.5f < TP %.5f", current_price, tp)
                return False
            if side == "SELL" and current_price > tp:
                logger.warning("SELL TP triggered but price %.5f > TP %.5f", current_price, tp)
                return False

        return True

    def set_daily_start(self, equity: float) -> None:
        """Record the equity at the start of the trading day."""
        self._daily_start_equity = equity
        self._trade_count_today = 0

    def increment_trade_count(self) -> None:
        """Call after each trade is executed."""
        self._trade_count_today += 1

    # ── Private helpers ────────────────────────────────────────

    def _estimate_trade_risk(self, trade: dict) -> float:
        """Estimate the monetary risk of a proposed trade."""
        entry = trade.get("entry_price", 0.0)
        sl = trade.get("sl_price", 0.0)
        lots = trade.get("lots", 0.0)
        tick_value = trade.get("tick_value", 1.0)
        tick_size = trade.get("tick_size", 0.0001)

        sl_dist = abs(entry - sl)
        if tick_size <= 0:
            return lots * sl_dist
        risk = lots * sl_dist * (tick_value / tick_size)
        return risk

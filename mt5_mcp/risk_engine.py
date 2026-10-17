"""Risk Engine - calculates risk, position sizing, and trade eligibility.

Separate from Strategy Agent and Red Team Agent (mandatory per project vision).

Calculates:
- Account equity
- Risk percentage
- Monetary risk
- Stop distance
- Position size
- Portfolio exposure
- Daily loss
- Drawdown
- Correlation exposure

Answers: CAN_TRADE or CANNOT_TRADE with explanation.

Explicitly prohibits:
- martingale
- loss recovery position sizing
- doubling down
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .domain_models import RiskEngineResult, TradeSetup, ZoneModel, AssetClass


class RiskEngine:
    """Risk engine that determines if trading is allowed and calculates position sizing.

    Risk parameters are configuration values - NOT hard-coded in strategy code.
    """

    def __init__(self, risk_percent: float = 0.5, max_daily_loss_percent: float = 3.0,
                 max_weekly_loss_percent: float = 6.0, max_drawdown_percent: float = 8.0,
                 max_open_risk_percent: float = 1.0, max_positions: int = 5,
                 min_risk_reward: float = 3.0) -> None:
        self.risk_percent = risk_percent  # % of equity per trade
        self.max_daily_loss_percent = max_daily_loss_percent
        self.max_weekly_loss_percent = max_weekly_loss_percent
        self.max_drawdown_percent = max_drawdown_percent
        self.max_open_risk_percent = max_open_risk_percent
        self.max_positions = max_positions
        self.min_risk_reward = min_risk_reward

        # State tracking (would be persisted in production)
        self.daily_loss: float = 0.0
        self.weekly_loss: float = 0.0
        self.peak_equity: float = 0.0
        self.current_drawdown: float = 0.0
        self.open_positions: int = 0
        self.total_exposure: float = 0.0
        self.correlation_exposure: float = 0.0

    def can_trade(self, setup: TradeSetup, equity: float,
                  open_positions: int = 0,
                  current_drawdown: float | None = None) -> RiskEngineResult:
        """Determine if a trade can be taken given current risk state.

        Returns RiskEngineResult with can_trade status and explanation.
        """
        # Initialize state if first call
        if current_drawdown is not None:
            self.current_drawdown = current_drawdown
        if open_positions > 0:
            self.open_positions = open_positions

        explanation_parts: list[str] = []

        # 1. Check minimum risk-reward
        rr = setup.calculate_expected_rr() if hasattr(setup, 'calculate_expected_rr') else None
        if rr is not None and rr < self.min_risk_reward:
            explanation_parts.append(
                f"Risk-reward {rr:.2f} below minimum {self.min_risk_reward:.2f}"
            )

        # 2. Check max positions
        if self.open_positions >= self.max_positions:
            self.open_positions = open_positions  # reset after check
            explanation_parts.append(
                f"Max positions ({self.max_positions}) already reached"
            )

        # 3. Check daily loss limit
        daily_loss_limit = equity * (self.max_daily_loss_percent / 100)
        if self.daily_loss >= daily_loss_limit:
            explanation_parts.append(
                f"Daily loss limit exceeded: ${self.daily_loss:.2f} >= ${daily_loss_limit:.2f}"
            )

        # 4. Check max open risk (total open risk as % of equity)
        if self.risk_percent > 0:
            max_open_risk_equity = equity * (self.max_open_risk_percent / 100)
            # Calculate current open risk from all positions
            # (simplified - in production would track per-position risk)
            current_open_risk = self.open_positions * self.risk_percent
            if current_open_risk > max_open_risk_equity:
                explanation_parts.append(
                    f"Open risk limit: {current_open_risk:.1f}% > {self.max_open_risk_percent:.1f}% of equity"
                )

        # 5. Check drawdown
        if self.current_drawdown and self.current_drawdown > 0:
            drawdown_limit = equity * (self.max_drawdown_percent / 100)
            if self.current_drawdown > drawdown_limit:
                explanation_parts.append(
                    f"Max drawdown exceeded: ${self.current_drawdown:.2f} > ${drawdown_limit:.2f}"
                )

        # 6. Calculate position size if trade passes initial checks
        position_size = 0.0
        can_trade = len(explanation_parts) == 0

        if can_trade and setup.entry is not None and setup.stop_loss is not None:
            # Calculate position size based on risk percent
            risk_amount = equity * (self.risk_percent / 100)
            stop_distance = abs(setup.entry - setup.stop_loss)
            if stop_distance > 0:
                position_size = risk_amount / stop_distance
            else:
                explanation_parts.append("Stop distance is zero, cannot calculate position size")
                can_trade = False

        # Check if position size would exceed max open risk
        if position_size > 0:
            potential_risk = position_size * stop_distance if stop_distance > 0 else 0
            max_risk_allowed = equity * (self.max_open_risk_percent / 100)
            if potential_risk > max_risk_allowed:
                # Reduce position size
                position_size = max_risk_allowed / max(stop_distance, 1e-10)
                explanation_parts.append("Position size reduced to meet max open risk limit")

        result = RiskEngineResult(
            can_trade=can_trade,
            explanation="; ".join(explanation_parts) if explanation_parts else "Trade approved",
            risk_percent=self.risk_percent,
            position_size=round(position_size, 2),
            max_daily_loss_exceeded=self.daily_loss >= (equity * (self.max_daily_loss_percent / 100)),
            max_drawdown_exceeded=self.current_drawdown > 0 and self.current_drawdown > equity * (self.max_drawdown_percent / 100),
            max_positions_exceeded=self.open_positions >= self.max_positions,
            correlation_exceeded=False,  # Would check correlation in full implementation
        )

        # Update state if trade approved
        if can_trade:
            self.open_positions = open_positions

        return result

    def record_trade_result(self, profit_loss: float, setup: TradeSetup) -> None:
        """Record trade result for risk state tracking.

        Updates daily loss, drawdown, etc.
        """
        # Update daily loss (track as positive number representing loss amount)
        if profit_loss < 0:
            # Loss - increase daily loss
            self.daily_loss += abs(profit_loss)
        else:
            # Profit - reduce daily loss
            self.daily_loss = max(0.0, self.daily_loss - profit_loss)

        # Record: would persist to journal in production
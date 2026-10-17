"""RiskAgent: independent risk engine that cannot be overridden by strategy agents.

Architecture: StrategyAgent -> RiskAgent -> Execution permission
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_ai.models import BacktestConfig, RiskApproval, RiskRejection, Side
from trading_ai.risk.portfolio import PortfolioRiskTracker


@dataclass
class RiskCheckResult:
    approved: bool
    position_size: float = 0.0
    risk_amount: float = 0.0
    risk_reward: float = 0.0
    margin_required: float = 0.0
    violations: list[str] = field(default_factory=list)


class RiskAgent:
    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.portfolio = PortfolioRiskTracker(
            initial_capital=config.initial_capital,
            max_open_positions=config.max_open_positions,
            max_correlated_exposure=config.max_correlated_exposure,
            max_daily_loss=config.max_daily_loss,
            max_drawdown=config.max_drawdown,
        )
        self._hard_limits = {
            "max_risk_per_trade": config.max_risk_per_trade,
            "max_daily_loss": config.max_daily_loss,
            "max_drawdown": config.max_drawdown,
            "max_open_positions": config.max_open_positions,
            "max_correlated_exposure": config.max_correlated_exposure,
        }

    def evaluate(
        self,
        symbol: str,
        side: Side,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        account_balance: float,
        timestamp: datetime | None = None,
    ) -> RiskApproval | RiskRejection:
        violations: list[str] = []

        if account_balance <= 0:
            return RiskRejection(
                reason="Account balance is zero or negative",
                violations=["Invalid account balance"],
            )

        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            return RiskRejection(
                reason="Stop loss distance is zero",
                violations=["Invalid stop loss"],
            )

        risk_reward = abs(take_profit - entry_price) / stop_distance
        if risk_reward < self.config.min_risk_reward - 1e-9:
            violations.append(
                f"Risk/Reward {risk_reward:.2f} below minimum {self.config.min_risk_reward}"
            )

        risk_amount = account_balance * self.config.risk_per_trade
        if risk_amount / account_balance > self._hard_limits["max_risk_per_trade"]:
            risk_amount = account_balance * self._hard_limits["max_risk_per_trade"]
            violations.append(
                f"Risk per trade capped at {self._hard_limits['max_risk_per_trade']:.2%}"
            )

        position_size = risk_amount / stop_distance
        margin_required = (position_size * entry_price) / self.config.leverage

        if margin_required > account_balance * 0.5:
            violations.append(
                f"Margin requirement ({margin_required:.2f}) exceeds 50% of balance"
            )

        if timestamp is not None:
            self.portfolio.update_equity(account_balance, timestamp)

        ok, msg = self.portfolio.check_position_limit()
        if not ok:
            violations.append(msg)

        ok, msg = self.portfolio.check_correlated_exposure(symbol)
        if not ok:
            violations.append(msg)

        ok, msg = self.portfolio.check_daily_loss()
        if not ok:
            violations.append(msg)

        ok, msg = self.portfolio.check_drawdown()
        if not ok:
            violations.append(msg)

        if violations:
            return RiskRejection(
                reason="Risk checks failed",
                violations=violations,
            )

        return RiskApproval(
            approved=True,
            position_size=position_size,
            risk_amount=risk_amount,
            risk_reward=risk_reward,
            margin_required=margin_required,
            reason="Approved",
            adjustments={
                "original_risk_per_trade": self.config.risk_per_trade,
                "capped_risk_per_trade": min(
                    self.config.risk_per_trade, self._hard_limits["max_risk_per_trade"]
                ),
            },
        )

    def register_position(self, trade_id: int, position_size: float, symbol: str) -> None:
        from trading_ai.models import OpenPosition

        pos = OpenPosition(
            trade_id=trade_id,
            symbol=symbol,
            side=Side.BUY,
            entry_time=datetime.now(),
            entry_price=0.0,
            stop_loss=0.0,
            take_profit=0.0,
            volume=position_size,
        )
        self.portfolio.register_position(pos)

    def close_position(self, trade_id: int) -> None:
        self.portfolio.close_position(trade_id)

    def update_equity(self, equity: float, timestamp: datetime) -> None:
        self.portfolio.update_equity(equity, timestamp)

    def check_kill_switch(self) -> bool:
        return self.portfolio.check_kill_switch()

    def get_exposure(self):
        return self.portfolio.get_exposure()

    def get_hard_limits(self) -> dict[str, float]:
        return dict(self._hard_limits)

    def validate_strategy_params(self, params: dict) -> tuple[bool, list[str]]:
        violations: list[str] = []

        if "risk_per_trade" in params:
            rp = params["risk_per_trade"]
            if rp > self._hard_limits["max_risk_per_trade"]:
                violations.append(
                    f"Strategy risk_per_trade ({rp}) exceeds hard limit "
                    f"({self._hard_limits['max_risk_per_trade']})"
                )

        if "max_open_positions" in params:
            mp = params["max_open_positions"]
            if mp > self._hard_limits["max_open_positions"]:
                violations.append(
                    f"Strategy max_open_positions ({mp}) exceeds hard limit "
                    f"({self._hard_limits['max_open_positions']})"
                )

        return len(violations) == 0, violations

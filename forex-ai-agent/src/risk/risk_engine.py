"""Risk engine: position sizing + loss limits.

The AI model never decides position size. Sizing is deterministic:

    risk_amount = account_equity * risk_percent / 100
    risk_per_lot = |entry - sl| * pip_value_per_lot   (in account currency)
    lots = risk_amount / risk_per_lot, floored to lot_step

Daily-loss and drawdown limits are enforced by RiskManager.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from src.config import Config


@dataclass
class SizeResult:
    lots: float
    risk_amount: float
    risk_percent: float
    rounded_down: bool


def position_size(
    account_equity: float,
    risk_percent: float,
    entry: float,
    sl: float,
    pip_value_per_lot: float,
    pip_size: float = 0.0001,
    lot_step: float = 0.01,
    min_lots: float = 0.01,
    max_lots: float = 100.0,
) -> SizeResult:
    """Compute position size in lots.

    pip_value_per_lot: P/L in account currency for a 1-pip move on 1.00 lot
    (standard MT5 pip value, e.g. ~10 USD for EURUSD on a USD account).
    pip_size: price size of one pip for the symbol (EURUSD 0.0001, USDJPY 0.01,
    XAUUSD 0.10).
    """
    if account_equity <= 0:
        raise ValueError("account_equity must be positive")
    if risk_percent <= 0:
        raise ValueError("risk_percent must be positive")
    distance = abs(entry - sl)
    if distance <= 0:
        raise ValueError("entry and sl must differ")

    risk_amount = account_equity * risk_percent / 100.0
    risk_per_lot = (distance / pip_size) * pip_value_per_lot
    raw_lots = risk_amount / risk_per_lot

    lots = math.floor(raw_lots / lot_step + 1e-9) * lot_step
    rounded_down = not math.isclose(lots, raw_lots, rel_tol=1e-9)
    lots = max(lots, min_lots)
    lots = min(lots, max_lots)

    return SizeResult(
        lots=round(lots, 4),
        risk_amount=risk_amount,
        risk_percent=risk_percent,
        rounded_down=rounded_down,
    )


class RiskManager:
    """Daily loss + max drawdown guards around an account."""

    def __init__(self, equity: float, max_daily_loss_pct: float = 2.0, max_drawdown_pct: float = 20.0):
        self.start_equity = equity
        self.peak_equity = equity
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_drawdown_pct = max_drawdown_pct
        self._day: str = datetime.now(timezone.utc).date().isoformat()
        self._day_start_equity = equity

    @classmethod
    def from_config(cls, cfg: Config, equity: float) -> "RiskManager":
        """Build from the risk.* config section; unknown keys keep defaults."""
        risk = cfg.get("risk", {}) or {}
        return cls(
            equity=equity,
            max_daily_loss_pct=float(risk.get("max_daily_loss_percent", 2.0)),
            max_drawdown_pct=float(risk.get("max_drawdown_percent", 20.0)),
        )

    @staticmethod
    def default_risk_percent(cfg: Config, symbol: str | None = None) -> float:
        risk = cfg.get("risk", {}) or {}
        if symbol:
            return float(cfg.get_symbol(
                "risk.default_percent", symbol, risk.get("default_percent", 0.5)
            ))
        return float(risk.get("default_percent", 0.5))

    @staticmethod
    def validate_percent(cfg: Config, percent: float) -> None:
        """Raise unless the percent is in risk.supported_percents."""
        risk = cfg.get("risk", {}) or {}
        supported = [float(p) for p in risk.get("supported_percents", [0.25, 0.5, 1.0])]
        if percent not in supported:
            raise ValueError(
                f"risk percent {percent} not in supported_percents {supported}"
            )

    def update(self, equity: float, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        day = now.date().isoformat()
        if day != self._day:
            self._day = day
            self._day_start_equity = equity
        self.peak_equity = max(self.peak_equity, equity)

    def daily_loss_exceeded(self, equity: float) -> bool:
        loss_pct = (self._day_start_equity - equity) / self._day_start_equity * 100
        return loss_pct > self.max_daily_loss_pct

    def drawdown_exceeded(self, equity: float) -> bool:
        if self.peak_equity <= 0:
            return True
        dd_pct = (self.peak_equity - equity) / self.peak_equity * 100
        return dd_pct > self.max_drawdown_pct

    def can_trade(self, equity: float, now: datetime | None = None) -> tuple[bool, str]:
        self.update(equity, now)
        if self.daily_loss_exceeded(equity):
            return False, "daily_loss_limit"
        if self.drawdown_exceeded(equity):
            return False, "max_drawdown_limit"
        return True, "ok"

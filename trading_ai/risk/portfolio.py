"""PortfolioRiskTracker: tracks all open positions and monitors portfolio-level risk."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date

from trading_ai.models import OpenPosition, Side


CORRELATION_GROUPS: dict[str, list[str]] = {
    "eur_group": ["EURUSD", "EURGBP", "EURJPY", "EURAUD", "EURCAD", "EURCHF", "EURNZD"],
    "gbp_group": ["GBPUSD", "GBPJPY", "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD"],
    "usd_group": ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "NZDUSD"],
    "jpy_group": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "NZDJPY", "CADJPY", "CHFJPY"],
    "aud_group": ["AUDUSD", "AUDJPY", "AUDNZD", "AUDCAD", "AUDCHF", "EURAUD", "GBPAUD"],
    "commodity": ["XAUUSD", "XAGUSD", "USDCAD", "AUDUSD", "NZDUSD"],
}


@dataclass
class ExposureSnapshot:
    timestamp: datetime
    total_long_exposure: float = 0.0
    total_short_exposure: float = 0.0
    net_exposure: float = 0.0
    gross_exposure: float = 0.0
    symbol_exposure: dict[str, float] = field(default_factory=dict)
    group_exposure: dict[str, float] = field(default_factory=dict)
    open_count: int = 0
    daily_pnl: float = 0.0
    peak_equity: float = 0.0
    current_equity: float = 0.0
    kill_switch_triggered: bool = False


class PortfolioRiskTracker:
    def __init__(
        self,
        initial_capital: float,
        max_open_positions: int = 5,
        max_correlated_exposure: float = 0.06,
        max_daily_loss: float = 0.03,
        max_drawdown: float = 0.10,
    ) -> None:
        self.initial_capital = initial_capital
        self.max_open_positions = max_open_positions
        self.max_correlated_exposure = max_correlated_exposure
        self.max_daily_loss = max_daily_loss
        self.max_drawdown = max_drawdown

        self.open_positions: list[OpenPosition] = []
        self.peak_equity = initial_capital
        self.current_equity = initial_capital
        self.daily_start_equity = initial_capital
        self.current_date: date | None = None
        self.kill_switch_active = False
        self.daily_pnl_history: list[tuple[date, float]] = []

    def register_position(self, position: OpenPosition) -> None:
        self.open_positions.append(position)

    def close_position(self, trade_id: int) -> OpenPosition | None:
        for i, pos in enumerate(self.open_positions):
            if pos.trade_id == trade_id:
                return self.open_positions.pop(i)
        return None

    def update_equity(self, equity: float, timestamp: datetime) -> None:
        self.current_equity = equity
        if equity > self.peak_equity:
            self.peak_equity = equity

        current_date = timestamp.date() if isinstance(timestamp, datetime) else timestamp
        if self.current_date != current_date:
            self.daily_start_equity = equity
            self.current_date = current_date

    def get_exposure(self) -> ExposureSnapshot:
        long_exposure = 0.0
        short_exposure = 0.0
        symbol_exposure: dict[str, float] = {}
        group_exposure: dict[str, float] = {g: 0.0 for g in CORRELATION_GROUPS}

        for pos in self.open_positions:
            notional = pos.volume * pos.current_price if pos.current_price > 0 else pos.volume * pos.entry_price
            if pos.side == Side.BUY:
                long_exposure += notional
            else:
                short_exposure += notional

            symbol_exposure[pos.symbol] = symbol_exposure.get(pos.symbol, 0.0) + notional

            for group_name, group_symbols in CORRELATION_GROUPS.items():
                if pos.symbol in group_symbols:
                    group_exposure[group_name] += notional

        net = long_exposure - short_exposure
        gross = long_exposure + short_exposure

        daily_pnl = self.current_equity - self.daily_start_equity if self.daily_start_equity > 0 else 0.0

        return ExposureSnapshot(
            timestamp=datetime.now(),
            total_long_exposure=long_exposure,
            total_short_exposure=short_exposure,
            net_exposure=net,
            gross_exposure=gross,
            symbol_exposure=symbol_exposure,
            group_exposure=group_exposure,
            open_count=len(self.open_positions),
            daily_pnl=daily_pnl,
            peak_equity=self.peak_equity,
            current_equity=self.current_equity,
            kill_switch_triggered=self.kill_switch_active,
        )

    def check_position_limit(self) -> tuple[bool, str]:
        if len(self.open_positions) >= self.max_open_positions:
            return False, f"Max open positions ({self.max_open_positions}) reached"
        return True, ""

    def check_correlated_exposure(self, symbol: str) -> tuple[bool, str]:
        exposure = self.get_exposure()
        if self.initial_capital <= 0:
            return False, "Initial capital is zero"

        for group_name, group_symbols in CORRELATION_GROUPS.items():
            if symbol in group_symbols:
                group_exp = exposure.group_exposure.get(group_name, 0.0)
                if group_exp / self.initial_capital > self.max_correlated_exposure:
                    return (
                        False,
                        f"Correlated exposure for {group_name} "
                        f"({group_exp / self.initial_capital:.2%}) exceeds limit "
                        f"({self.max_correlated_exposure:.2%})",
                    )
        return True, ""

    def check_daily_loss(self) -> tuple[bool, str]:
        if self.daily_start_equity <= 0:
            return True, ""
        daily_loss = (self.daily_start_equity - self.current_equity) / self.daily_start_equity
        if daily_loss >= self.max_daily_loss:
            return (
                False,
                f"Daily loss ({daily_loss:.2%}) exceeds limit ({self.max_daily_loss:.2%})",
            )
        return True, ""

    def check_drawdown(self) -> tuple[bool, str]:
        if self.peak_equity <= 0:
            return True, ""
        drawdown = (self.peak_equity - self.current_equity) / self.peak_equity
        if drawdown >= self.max_drawdown:
            return (
                False,
                f"Drawdown ({drawdown:.2%}) exceeds limit ({self.max_drawdown:.2%})",
            )
        return True, ""

    def check_all(self) -> tuple[bool, list[str]]:
        violations: list[str] = []

        ok, msg = self.check_position_limit()
        if not ok:
            violations.append(msg)

        ok, msg = self.check_daily_loss()
        if not ok:
            violations.append(msg)

        ok, msg = self.check_drawdown()
        if not ok:
            violations.append(msg)

        for pos in self.open_positions:
            ok, msg = self.check_correlated_exposure(pos.symbol)
            if not ok:
                violations.append(msg)

        return len(violations) == 0, violations

    def check_kill_switch(self) -> bool:
        if self.peak_equity <= 0:
            return False

        drawdown = (self.peak_equity - self.current_equity) / self.peak_equity
        if drawdown >= self.max_drawdown:
            self.kill_switch_active = True
            return True

        ok, _ = self.check_daily_loss()
        if not ok:
            self.kill_switch_active = True
            return True

        return False

    def get_daily_pnl_history(self) -> list[tuple[date, float]]:
        return self.daily_pnl_history

    def reset_daily(self, equity: float) -> None:
        self.daily_start_equity = equity
        self.kill_switch_active = False

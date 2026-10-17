from __future__ import annotations

import pytest


class RiskEngine:
    """Minimal risk engine for testing."""

    def __init__(
        self,
        account_balance: float = 100_000.0,
        risk_per_trade: float = 0.01,
        max_drawdown_pct: float = 0.10,
        daily_loss_limit_pct: float = 0.03,
        min_rr: float = 3.0,
    ):
        self.account_balance = account_balance
        self.risk_per_trade = risk_per_trade
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.min_rr = min_rr
        self.peak_balance = account_balance
        self.daily_pnl = 0.0
        self.kill_switch_triggered = False

    def calculate_position_size(
        self, entry_price: float, stop_loss: float, pip_value: float = 10.0
    ) -> float:
        risk_amount = self.account_balance * self.risk_per_trade
        pip_risk = abs(entry_price - stop_loss) / 0.0001
        if pip_risk <= 0:
            return 0.0
        lot_size = risk_amount / (pip_risk * pip_value)
        return round(lot_size, 2)

    def validate_rr(self, entry: float, stop_loss: float, take_profit: float) -> bool:
        risk = abs(entry - stop_loss)
        reward = abs(take_profit - entry)
        if risk <= 0:
            return False
        return (reward / risk) >= self.min_rr - 1e-9

    def update_balance(self, new_balance: float) -> None:
        self.account_balance = new_balance
        if new_balance > self.peak_balance:
            self.peak_balance = new_balance

    def track_daily_pnl(self, pnl: float) -> None:
        self.daily_pnl += pnl

    def get_drawdown(self) -> float:
        if self.peak_balance <= 0:
            return 0.0
        return (self.peak_balance - self.account_balance) / self.peak_balance

    def check_kill_switch(self) -> bool:
        dd = self.get_drawdown()
        if dd >= self.max_drawdown_pct:
            self.kill_switch_triggered = True
            return True
        if abs(self.daily_pnl) >= self.account_balance * self.daily_loss_limit_pct:
            self.kill_switch_triggered = True
            return True
        return False


@pytest.fixture
def risk_engine():
    return RiskEngine(
        account_balance=100_000.0,
        risk_per_trade=0.01,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.03,
        min_rr=3.0,
    )


class TestPositionSizing:
    def test_basic_sizing(self, risk_engine):
        lots = risk_engine.calculate_position_size(1.1000, 1.0950)
        assert lots > 0
        assert lots < 100

    def test_wider_stop_smaller_size(self, risk_engine):
        lots_tight = risk_engine.calculate_position_size(1.1000, 1.0990)
        lots_wide = risk_engine.calculate_position_size(1.1000, 1.0900)
        assert lots_tight > lots_wide

    def test_zero_risk_returns_zero(self, risk_engine):
        lots = risk_engine.calculate_position_size(1.1000, 1.1000)
        assert lots == 0.0

    def test_respects_account_balance(self, risk_engine):
        lots = risk_engine.calculate_position_size(1.1000, 1.0950)
        risk_amount = lots * abs(1.1000 - 1.0950) / 0.0001 * 10.0
        assert risk_amount <= risk_engine.account_balance * risk_engine.risk_per_trade * 1.1


class TestRRValidation:
    def test_valid_rr(self, risk_engine):
        assert risk_engine.validate_rr(1.1000, 1.0900, 1.1300) is True

    def test_invalid_rr_too_low(self, risk_engine):
        assert risk_engine.validate_rr(1.1000, 1.0900, 1.1100) is False

    def test_zero_risk_returns_false(self, risk_engine):
        assert risk_engine.validate_rr(1.1000, 1.1000, 1.1300) is False

    def test_exact_min_rr(self, risk_engine):
        # risk=100 pips, reward=300 pips -> rr=3.0 exactly
        assert risk_engine.validate_rr(1.1000, 1.0900, 1.1300) is True

    def test_sells_direction(self, risk_engine):
        assert risk_engine.validate_rr(1.1000, 1.1100, 1.0700) is True


class TestDrawdownTracking:
    def test_initial_drawdown_zero(self, risk_engine):
        assert risk_engine.get_drawdown() == 0.0

    def test_drawdown_after_loss(self, risk_engine):
        risk_engine.update_balance(95_000)
        assert risk_engine.get_drawdown() == pytest.approx(0.05, abs=0.001)

    def test_peak_updates_on_new_high(self, risk_engine):
        risk_engine.update_balance(105_000)
        assert risk_engine.peak_balance == 105_000
        risk_engine.update_balance(100_000)
        assert risk_engine.peak_balance == 105_000
        assert risk_engine.get_drawdown() == pytest.approx(0.05 / 1.05, abs=0.001)


class TestKillSwitch:
    def test_no_trigger_normal(self, risk_engine):
        risk_engine.update_balance(95_000)
        assert risk_engine.check_kill_switch() is False

    def test_trigger_on_max_drawdown(self, risk_engine):
        risk_engine.update_balance(89_000)
        assert risk_engine.check_kill_switch() is True
        assert risk_engine.kill_switch_triggered is True

    def test_trigger_on_daily_loss(self, risk_engine):
        risk_engine.track_daily_pnl(-3_500)
        assert risk_engine.check_kill_switch() is True

    def test_daily_loss_within_limit(self, risk_engine):
        risk_engine.track_daily_pnl(-2_000)
        assert risk_engine.check_kill_switch() is False

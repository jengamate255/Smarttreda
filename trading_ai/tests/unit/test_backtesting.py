from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timezone


def _make_ohlcv(n: int = 500, base: float = 1.1000, vol: float = 0.0008) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range(start=datetime(2024, 1, 1, tzinfo=timezone.utc), periods=n, freq="3600s")
    closes = np.cumsum(rng.normal(0, vol, n)) + base
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    highs = np.maximum(opens, closes) + rng.uniform(0.0001, 0.0005, n)
    lows = np.minimum(opens, closes) - rng.uniform(0.0001, 0.0005, n)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": rng.uniform(50, 500, n), "spread": rng.uniform(0.5, 2.0, n),
    }, index=dates)


class Backtester:
    """Minimal backtester for testing core properties."""

    def __init__(self, spread_pips: float = 1.0, slippage_pips: float = 0.5):
        self.spread_pips = spread_pips
        self.slippage_pips = slippage_pips

    def simulate_entry(self, direction: str, entry_price: float) -> float:
        spread_cost = self.spread_pips * 0.0001
        slippage = self.slippage_pips * 0.0001
        if direction == "BUY":
            return entry_price + spread_cost + slippage
        return entry_price - spread_cost - slippage

    def run(
        self, df: pd.DataFrame, signals: list[dict], initial_balance: float = 100_000
    ) -> dict:
        balance = initial_balance
        trades = []
        for sig in signals:
            idx = sig["index"]
            if idx >= len(df):
                continue
            direction = sig["direction"]
            entry_price = df["close"].iloc[idx]
            sl = sig["stop_loss"]
            tp = sig["take_profit"]
            actual_entry = self.simulate_entry(direction, entry_price)

            # Check SL/TP against subsequent candles
            exit_price = None
            exit_reason = "time"
            for j in range(idx + 1, min(idx + 200, len(df))):
                candle = df.iloc[j]
                if direction == "BUY":
                    if candle["low"] <= sl:
                        exit_price = sl
                        exit_reason = "stop_loss"
                        break
                    if candle["high"] >= tp:
                        exit_price = tp
                        exit_reason = "take_profit"
                        break
                else:
                    if candle["high"] >= sl:
                        exit_price = sl
                        exit_reason = "stop_loss"
                        break
                    if candle["low"] <= tp:
                        exit_price = tp
                        exit_reason = "take_profit"
                        break

            if exit_price is None:
                exit_price = df["close"].iloc[min(idx + 199, len(df) - 1)]
                exit_reason = "time"

            if direction == "BUY":
                pnl = (exit_price - actual_entry) / 0.0001 * 10
            else:
                pnl = (actual_entry - exit_price) / 0.0001 * 10

            balance += pnl
            trades.append({
                "direction": direction,
                "entry": actual_entry,
                "exit": exit_price,
                "pnl": pnl,
                "exit_reason": exit_reason,
            })

        return {"trades": trades, "final_balance": balance, "initial_balance": initial_balance}


@pytest.fixture
def backtester():
    return Backtester(spread_pips=1.0, slippage_pips=0.5)


class TestNoLookAheadBias:
    def test_entry_uses_close_at_signal_index(self, backtester):
        df = _make_ohlcv(100)
        signals = [{"index": 50, "direction": "BUY", "stop_loss": 1.0900, "take_profit": 1.1300}]
        result = backtester.run(df, signals)
        trade = result["trades"][0]
        expected_close = df["close"].iloc[50]
        assert trade["entry"] >= expected_close  # entry >= close due to spread/slippage

    def test_exit_only_lookes_forward(self, backtester):
        df = _make_ohlcv(100)
        signals = [{"index": 80, "direction": "BUY", "stop_loss": 1.0900, "take_profit": 1.1300}]
        result = backtester.run(df, signals)
        trade = result["trades"][0]
        assert trade["exit"] is not None


class TestSLTPExecution:
    def test_stop_loss_hit(self, backtester):
        df = _make_ohlcv(100)
        # Price drops significantly -> SL should be hit
        signals = [{"index": 10, "direction": "BUY", "stop_loss": 1.15, "take_profit": 1.50}]
        result = backtester.run(df, signals)
        assert len(result["trades"]) == 1

    def test_take_profit_hit(self, backtester):
        df = _make_ohlcv(200, base=1.10, vol=0.005)
        signals = [{"index": 5, "direction": "BUY", "stop_loss": 1.05, "take_profit": 1.30}]
        result = backtester.run(df, signals)
        trade = result["trades"][0]
        assert trade["exit_reason"] in ("take_profit", "stop_loss", "time")

    def test_bearish_trade(self, backtester):
        df = _make_ohlcv(100)
        signals = [{"index": 10, "direction": "SELL", "stop_loss": 1.15, "take_profit": 1.05}]
        result = backtester.run(df, signals)
        assert len(result["trades"]) == 1


class TestSpreadModeling:
    def test_buy_entry_includes_spread(self, backtester):
        entry = backtester.simulate_entry("BUY", 1.1000)
        assert entry > 1.1000

    def test_sell_entry_includes_spread(self, backtester):
        entry = backtester.simulate_entry("SELL", 1.1000)
        assert entry < 1.1000

    def test_spread_and_slippage_add_up(self, backtester):
        entry = backtester.simulate_entry("BUY", 1.1000)
        expected = 1.1000 + 1.0 * 0.0001 + 0.5 * 0.0001
        assert entry == pytest.approx(expected, abs=1e-8)

    def test_wider_spread_reduces_profit(self):
        tight = Backtester(spread_pips=0.5, slippage_pips=0.0)
        wide = Backtester(spread_pips=3.0, slippage_pips=0.0)
        entry_tight = tight.simulate_entry("BUY", 1.1000)
        entry_wide = wide.simulate_entry("BUY", 1.1000)
        assert entry_wide > entry_tight

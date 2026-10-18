"""BTC-specific strategy: Volatility-Adjusted Trend Following.

Based on BTC analysis:
- ATR median $462, so use 2.5x ATR SL ($1156) to survive pullbacks
- Pullbacks average 3.3%, so enter on pullback completion (RSI bounce)
- Trends last 8 days, so use trailing stop to let winners run
- 24h moves are $969, so TP = 4x ATR or trailing

Strategy logic:
1. Trend: EMA 50 > EMA 200 = bullish, EMA 50 < EMA 200 = bearish
2. Entry (BUY): Trend bullish + RSI was < 40 and now > 40 (pullback complete)
   Entry (SELL): Trend bearish + RSI was > 60 and now < 60
3. SL: 2.5x ATR from entry
4. TP: 4x ATR or trailing stop (trail at 2x ATR)
5. Position size: Target 1% equity risk, reduce when ATR > 75th percentile
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from mt5_mcp.backtest import load_frames
from mt5_mcp.client import get_client
from mt5_mcp.common import pip_size
from mt5_mcp.indicators import atr as calc_atr, ema, rsi as calc_rsi


START_BALANCE = 10000
RISK_PERCENT = 1.0
SPREAD_POINTS = 50
SLIPPAGE_POINTS = 10
COMMISSION_PER_LOT = 7.0
WARMUP = 250

ATR_SL_MULT = 2.5
ATR_TP_MULT = 4.0
ATR_TRAIL_MULT = 2.0
RSI_ENTRY_LOW = 40
RSI_ENTRY_HIGH = 60


@dataclass
class Trade:
    bar_idx: int
    action: str
    entry: float
    sl: float
    tp: float
    lots: float
    exit_price: float = 0.0
    pnl: float = 0.0
    exit_bar: int = 0
    reason: str = ""


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    trades: list[Trade] = field(default_factory=list)
    equity: float = START_BALANCE
    peak: float = START_BALANCE
    max_dd: float = 0.0

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def win_rate(self) -> float:
        return self.wins / self.n_trades * 100 if self.n_trades else 0.0

    @property
    def gross_profit(self) -> float:
        return sum(t.pnl for t in self.trades if t.pnl > 0)

    @property
    def gross_loss(self) -> float:
        return abs(sum(t.pnl for t in self.trades if t.pnl < 0))

    @property
    def profit_factor(self) -> float:
        gl = self.gross_loss
        return self.gross_profit / gl if gl > 0 else float("inf") if self.gross_profit > 0 else 0.0

    @property
    def net_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def return_pct(self) -> float:
        return (self.equity - START_BALANCE) / START_BALANCE * 100

    @property
    def max_dd_pct(self) -> float:
        return self.max_dd * 100

    @property
    def expectancy(self) -> float:
        return self.net_pnl / self.n_trades if self.n_trades else 0.0


def _calc_lots(equity: float, risk_pct: float, sl_distance: float, info: dict) -> float:
    risk_amount = equity * risk_pct / 100
    contract_size = float(info.get("trade_contract_size", 1.0))
    tick_value = float(info.get("trade_tick_value", 0.01))
    tick_size = float(info.get("trade_tick_size", 0.01))
    point_value = tick_value / tick_size if tick_size > 0 else 0.01

    lots = risk_amount / (sl_distance * point_value) if sl_distance * point_value > 0 else 0
    step = float(info.get("volume_step", 0.01))
    vmin = float(info.get("volume_min", 0.01))
    vmax = float(info.get("volume_max", 100.0))
    lots = max(vmin, min(vmax, round(lots / step) * step))
    return lots


def _pnl(action: str, entry: float, exit_price: float, lots: float,
         point_value: float, spread: float, slippage: float) -> float:
    if action == "BUY":
        raw = (exit_price - slippage - entry) * point_value * lots
    else:
        raw = (entry - (exit_price + spread) - slippage) * point_value * lots
    return raw - COMMISSION_PER_LOT * lots


def _simulate_exit(frame: pd.DataFrame, start_idx: int, action: str,
                   entry: float, sl: float, tp: float, atr_arr: np.ndarray,
                   point_value: float, spread: float, slippage: float,
                   lots: float, timeout: int = 500) -> tuple[float, str, int]:
    limit = min(start_idx + timeout, len(frame))
    trail_sl = sl

    for j in range(start_idx, limit):
        bar = frame.iloc[j]
        hi, lo = float(bar["high"]), float(bar["low"])
        current_atr = atr_arr[j] if j < len(atr_arr) else atr_arr[-1]

        if action == "BUY":
            if lo <= trail_sl:
                return trail_sl, "sl", j
            if hi >= tp:
                trail_sl = max(trail_sl, tp - ATR_TRAIL_MULT * current_atr)
            unrealized = hi - entry
            if unrealized > 2 * current_atr:
                trail_sl = max(trail_sl, hi - ATR_TRAIL_MULT * current_atr)
        else:
            if hi >= trail_sl:
                return trail_sl, "sl", j
            if lo <= tp:
                trail_sl = min(trail_sl, tp + ATR_TRAIL_MULT * current_atr)
            unrealized = entry - lo
            if unrealized > 2 * current_atr:
                trail_sl = min(trail_sl, lo + ATR_TRAIL_MULT * current_atr)

    close_p = float(frame.iloc[limit - 1]["close"])
    return close_p, "timeout", limit - 1


def backtest_btc_trend(
    symbol: str, frame: pd.DataFrame, info: dict,
) -> BacktestResult:
    result = BacktestResult(symbol=symbol, strategy="btc_trend")
    equity = START_BALANCE
    peak = START_BALANCE

    ema50 = ema(frame["close"], 50)
    ema200 = ema(frame["close"], 200)
    rsi_series = calc_rsi(frame["close"], 14)
    atr_series = calc_atr(frame, 14)

    ema50_arr = ema50.values
    ema200_arr = ema200.values
    rsi_arr = rsi_series.values
    atr_arr = atr_series.values
    close_arr = frame["close"].values

    point = float(info.get("point", 0.01))
    spread = SPREAD_POINTS * point
    slippage = SLIPPAGE_POINTS * point
    tick_value = float(info.get("trade_tick_value", 0.01))
    tick_size = float(info.get("trade_tick_size", 0.01))
    point_value = tick_value / tick_size if tick_size > 0 else 0.01

    i = WARMUP
    while i < len(frame) - 1:
        if np.isnan(ema50_arr[i]) or np.isnan(ema200_arr[i]) or np.isnan(rsi_arr[i]):
            i += 1
            continue

        bullish = ema50_arr[i] > ema200_arr[i]
        bearish = ema50_arr[i] < ema200_arr[i]

        action = None
        if bullish and i > 0:
            prev_rsi = rsi_arr[i - 1] if not np.isnan(rsi_arr[i - 1]) else 50
            curr_rsi = rsi_arr[i]
            if prev_rsi < RSI_ENTRY_LOW and curr_rsi >= RSI_ENTRY_LOW:
                action = "BUY"
        elif bearish and i > 0:
            prev_rsi = rsi_arr[i - 1] if not np.isnan(rsi_arr[i - 1]) else 50
            curr_rsi = rsi_arr[i]
            if prev_rsi > RSI_ENTRY_HIGH and curr_rsi <= RSI_ENTRY_HIGH:
                action = "SELL"

        if action is None:
            i += 1
            continue

        current_atr = atr_arr[i]
        next_bar = frame.iloc[i + 1]
        entry_price = float(next_bar["open"])

        if action == "BUY":
            entry_price += spread + slippage
            sl = entry_price - ATR_SL_MULT * current_atr
            tp = entry_price + ATR_TP_MULT * current_atr
        else:
            entry_price -= slippage
            sl = entry_price + ATR_SL_MULT * current_atr
            tp = entry_price - ATR_TP_MULT * current_atr

        sl_distance = abs(entry_price - sl)
        lots = _calc_lots(equity, RISK_PERCENT, sl_distance, info)
        if lots <= 0:
            i += 1
            continue

        exit_price, reason, exit_idx = _simulate_exit(
            frame, i + 2, action, entry_price, sl, tp, atr_arr,
            point_value, spread, slippage, lots,
        )
        trade_pnl = _pnl(action, entry_price, exit_price, lots, point_value, spread, slippage)

        trade = Trade(
            bar_idx=i, action=action, entry=entry_price, sl=sl, tp=tp, lots=lots,
            exit_price=exit_price, pnl=trade_pnl, exit_bar=exit_idx, reason=reason,
        )
        result.trades.append(trade)
        equity += trade_pnl
        result.equity = equity
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0
        if dd > result.max_dd:
            result.max_dd = dd

        i = exit_idx + 1

    return result


def main():
    client = get_client()
    client.ensure_connected()

    print("Fetching BTCUSD data...")
    frames = load_frames("BTCUSD", ["H1"], 5000)
    h1 = frames["H1"]
    info = client.symbol_info("BTCUSD")

    print(f"Data: {len(h1)} bars\n")

    result = backtest_btc_trend("BTCUSD", h1, info)

    print("=" * 80)
    print("BTC TREND FOLLOWING STRATEGY RESULTS")
    print("=" * 80)
    print(f"Trades: {result.n_trades}")
    print(f"Win rate: {result.win_rate:.1f}%")
    print(f"Profit factor: {result.profit_factor:.2f}")
    print(f"Net P&L: ${result.net_pnl:.2f}")
    print(f"Return: {result.return_pct:+.2f}%")
    print(f"Max drawdown: {result.max_dd_pct:.2f}%")
    print(f"Expectancy: ${result.expectancy:.2f} per trade")
    print()

    if result.trades:
        wins = [t for t in result.trades if t.pnl > 0]
        losses = [t for t in result.trades if t.pnl < 0]
        if wins:
            print(f"Average win: ${np.mean([t.pnl for t in wins]):.2f}")
        if losses:
            print(f"Average loss: ${np.mean([t.pnl for t in losses]):.2f}")
        print()

        print("Last 10 trades:")
        for t in result.trades[-10:]:
            print(f"  {t.action} @ ${t.entry:.2f} -> ${t.exit_price:.2f} "
                  f"({t.reason}) P&L: ${t.pnl:+.2f}")


if __name__ == "__main__":
    main()

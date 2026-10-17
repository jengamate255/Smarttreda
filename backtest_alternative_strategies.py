"""Backtest mean reversion and momentum breakout strategies.

Tests whether alternative strategies perform better on pairs where trend_zone fails.
Uses the same data pipeline as backtest.py (load_frames, indicators.py) but with
simpler signal logic matching multi_strategy.py.

Mean Reversion: RSI<30 + lower BB touch → BUY, RSI>70 + upper BB touch → SELL, ADX<25
Momentum Breakout: ADX>25 + close > 20-bar high → BUY, close < 20-bar low → SELL
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from mt5_mcp.backtest import load_frames
from mt5_mcp.client import get_client
from mt5_mcp.common import pip_size
from mt5_mcp.indicators import rsi as calc_rsi


FAILING_PAIRS = ["BTCUSD", "ETHUSD", "XAUUSD"]
TIMEFRAME = "H1"
BARS = 4000
WARMUP = 250

START_BALANCE = 10000
RISK_PERCENT = 0.5
SPREAD_POINTS = 15
SLIPPAGE_POINTS = 1
COMMISSION_PER_LOT = 7.0
TIMEOUT_BARS = 200


@dataclass
class Trade:
    bar_idx: int
    action: str
    entry: float
    sl: float
    tp: float
    sl_pips: float
    tp_pips: float
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


def _adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_val = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr_val)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr_val)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.rolling(period).mean()


def _bollinger(frame: pd.DataFrame, period: int = 20, std_dev: float = 2.0):
    close = frame["close"]
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    return sma + std_dev * std, sma, sma - std_dev * std


def _calc_lots(equity: float, risk_pct: float, sl_pips: float, pip_val: float, info: dict) -> float:
    risk_amount = equity * risk_pct / 100
    lots = risk_amount / (sl_pips * pip_val) if sl_pips * pip_val > 0 else 0
    step = float(info.get("volume_step", 0.01))
    vmin = float(info.get("volume_min", 0.01))
    vmax = float(info.get("volume_max", 100.0))
    lots = max(vmin, min(vmax, round(lots / step) * step))
    return lots


def _pnl(action: str, entry: float, exit_price: float, lots: float,
         per_point: float, spread: float, slippage: float) -> float:
    if action == "BUY":
        raw = (exit_price - slippage - entry) * per_point * lots
    else:
        raw = (entry - (exit_price + spread) - slippage) * per_point * lots
    return raw - COMMISSION_PER_LOT * lots


def _simulate_exit(frame: pd.DataFrame, start_idx: int, action: str,
                   entry: float, sl: float, tp: float, pip: float,
                   spread: float, slippage: float, per_point: float,
                   lots: float, timeout: int = TIMEOUT_BARS) -> tuple[float, str, int]:
    limit = min(start_idx + timeout, len(frame))
    trail_sl = sl
    for j in range(start_idx, limit):
        bar = frame.iloc[j]
        hi, lo = float(bar["high"]), float(bar["low"])

        if action == "BUY":
            if lo <= trail_sl:
                exit_p = trail_sl
                pnl = _pnl(action, entry, exit_p, lots, per_point, spread, slippage)
                return exit_p, "sl", j
            if hi >= tp:
                exit_p = tp
                pnl = _pnl(action, entry, exit_p, lots, per_point, spread, slippage)
                return exit_p, "tp", j
        else:
            if hi >= trail_sl:
                exit_p = trail_sl
                pnl = _pnl(action, entry, exit_p, lots, per_point, spread, slippage)
                return exit_p, "sl", j
            if lo <= tp:
                exit_p = tp
                pnl = _pnl(action, entry, exit_p, lots, per_point, spread, slippage)
                return exit_p, "tp", j

    close_p = float(frame.iloc[limit - 1]["close"])
    return close_p, "timeout", limit - 1


def backtest_mean_reversion(
    symbol: str, frame: pd.DataFrame, info: dict, pip: float, pip_value: float,
) -> BacktestResult:
    result = BacktestResult(symbol=symbol, strategy="mean_reversion")
    equity = START_BALANCE
    peak = START_BALANCE

    rsi_series = calc_rsi(frame["close"], 14)
    bb_upper, bb_mid, bb_lower = _bollinger(frame, 20, 2.0)
    adx_series = _adx(frame, 14)

    rsi_arr = rsi_series.values
    adx_arr = adx_series.values
    bb_upper_arr = bb_upper.values
    bb_lower_arr = bb_lower.values
    close_arr = frame["close"].values

    spread = SPREAD_POINTS * float(info.get("point", 0.00001))
    slippage = SLIPPAGE_POINTS * float(info.get("point", 0.00001))
    per_point = pip_value / pip if pip > 0 else 0

    i = WARMUP
    while i < len(frame) - 1:
        rsi_val = rsi_arr[i]
        adx_val = adx_arr[i]
        bb_u = bb_upper_arr[i]
        bb_l = bb_lower_arr[i]
        c = close_arr[i]

        if np.isnan(rsi_val) or np.isnan(adx_val) or np.isnan(bb_u):
            i += 1
            continue

        action = None
        if adx_val < 25:
            if rsi_val < 30 and c <= bb_l * 1.002:
                action = "BUY"
            elif rsi_val > 70 and c >= bb_u * 0.998:
                action = "SELL"

        if action is None:
            i += 1
            continue

        next_bar = frame.iloc[i + 1]
        entry_price = float(next_bar["open"])
        if action == "BUY":
            entry_price += spread + slippage
            sl = entry_price - 25 * pip
            tp = entry_price + 50 * pip
        else:
            entry_price -= slippage
            sl = entry_price + 25 * pip
            tp = entry_price - 50 * pip

        sl_pips = 25.0
        tp_pips = 50.0
        lots = _calc_lots(equity, RISK_PERCENT, sl_pips, pip_value, info)
        if lots <= 0:
            i += 1
            continue

        exit_price, reason, exit_idx = _simulate_exit(
            frame, i + 2, action, entry_price, sl, tp, pip, spread, slippage, per_point, lots,
        )
        trade_pnl = _pnl(action, entry_price, exit_price, lots, per_point, spread, slippage)

        trade = Trade(
            bar_idx=i, action=action, entry=entry_price, sl=sl, tp=tp,
            sl_pips=sl_pips, tp_pips=tp_pips, lots=lots,
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


def backtest_momentum_breakout(
    symbol: str, frame: pd.DataFrame, info: dict, pip: float, pip_value: float,
) -> BacktestResult:
    result = BacktestResult(symbol=symbol, strategy="momentum_breakout")
    equity = START_BALANCE
    peak = START_BALANCE

    adx_series = _adx(frame, 14)
    adx_arr = adx_series.values
    close_arr = frame["close"].values
    high_arr = frame["high"].values
    low_arr = frame["low"].values

    spread = SPREAD_POINTS * float(info.get("point", 0.00001))
    slippage = SLIPPAGE_POINTS * float(info.get("point", 0.00001))
    per_point = pip_value / pip if pip > 0 else 0

    i = WARMUP
    while i < len(frame) - 1:
        adx_val = adx_arr[i]
        c = close_arr[i]

        if np.isnan(adx_val) or adx_val < 25:
            i += 1
            continue

        lookback_start = max(0, i - 20)
        if lookback_start >= i:
            i += 1
            continue
        recent_high = np.max(high_arr[lookback_start:i])
        recent_low = np.min(low_arr[lookback_start:i])

        action = None
        if c > recent_high:
            action = "BUY"
        elif c < recent_low:
            action = "SELL"

        if action is None:
            i += 1
            continue

        next_bar = frame.iloc[i + 1]
        entry_price = float(next_bar["open"])
        if action == "BUY":
            entry_price += spread + slippage
            sl = entry_price - 30 * pip
            tp = entry_price + 80 * pip
        else:
            entry_price -= slippage
            sl = entry_price + 30 * pip
            tp = entry_price - 80 * pip

        sl_pips = 30.0
        tp_pips = 80.0
        lots = _calc_lots(equity, RISK_PERCENT, sl_pips, pip_value, info)
        if lots <= 0:
            i += 1
            continue

        exit_price, reason, exit_idx = _simulate_exit(
            frame, i + 2, action, entry_price, sl, tp, pip, spread, slippage, per_point, lots,
        )
        trade_pnl = _pnl(action, entry_price, exit_price, lots, per_point, spread, slippage)

        trade = Trade(
            bar_idx=i, action=action, entry=entry_price, sl=sl, tp=tp,
            sl_pips=sl_pips, tp_pips=tp_pips, lots=lots,
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

    print(f"Fetching {BARS} bars of {TIMEFRAME} data for {len(FAILING_PAIRS)} pairs...")
    t0 = time.time()

    all_frames = {}
    all_info = {}
    for sym in FAILING_PAIRS:
        frames = load_frames(sym, [TIMEFRAME], BARS)
        if TIMEFRAME in frames:
            all_frames[sym] = frames[TIMEFRAME]
            all_info[sym] = client.symbol_info(sym)
            print(f"  {sym}: {len(frames[TIMEFRAME])} bars")
        else:
            print(f"  {sym}: NO DATA")

    print(f"Fetched in {time.time() - t0:.1f}s\n")

    trend_results = {
        "BTCUSD": (18, 22.2, 0.527, -3.1, 4.52, -17.22),
        "ETHUSD": (7, 28.6, 1.692, 0.72, 0.69, 10.23),
        "XAUUSD": (5, 40.0, 1.708, 1.07, 1.0, 21.46),
    }

    print("=" * 120)
    print(f"{'Symbol':<8} {'Strategy':<20} {'Trades':>6} {'Win%':>7} {'PF':>7} {'Return%':>9} {'MaxDD%':>8} {'Exp':>9}")
    print("=" * 120)

    for sym in FAILING_PAIRS:
        if sym not in all_frames:
            continue

        frame = all_frames[sym]
        info = all_info[sym]
        pip = pip_size(info)
        from mt5_mcp.common import pip_value_per_lot
        pip_val = pip_value_per_lot(info)

        print(f"\n--- {sym} ---")

        mr = backtest_mean_reversion(sym, frame, info, pip, pip_val)
        print(f"  {'':8} {'mean_reversion':<20} {mr.n_trades:>6} {mr.win_rate:>6.1f}% {mr.profit_factor:>7.2f} {mr.return_pct:>+8.2f}% {mr.max_dd_pct:>7.2f}% {mr.expectancy:>+8.2f}")

        mb = backtest_momentum_breakout(sym, frame, info, pip, pip_val)
        print(f"  {'':8} {'momentum_brk':<20} {mb.n_trades:>6} {mb.win_rate:>6.1f}% {mb.profit_factor:>7.2f} {mb.return_pct:>+8.2f}% {mb.max_dd_pct:>7.2f}% {mb.expectancy:>+8.2f}")

        if sym in trend_results:
            tr_n, tr_wr, tr_pf, tr_ret, tr_dd, tr_exp = trend_results[sym]
            print(f"  {'':8} {'trend_zone':<20} {tr_n:>6} {tr_wr:>6.1f}% {tr_pf:>7.2f} {tr_ret:>+8.2f}% {tr_dd:>7.2f}% {tr_exp:>+8.2f}")

    print("\n" + "=" * 120)
    print("\nSummary: Best strategy per pair (by PF)")
    print("-" * 80)

    for sym in FAILING_PAIRS:
        if sym not in all_frames:
            continue
        frame = all_frames[sym]
        info = all_info[sym]
        pip = pip_size(info)
        from mt5_mcp.common import pip_value_per_lot
        pip_val = pip_value_per_lot(info)

        mr = backtest_mean_reversion(sym, frame, info, pip, pip_val)
        mb = backtest_momentum_breakout(sym, frame, info, pip, pip_val)

        best = "none"
        best_pf = 0.0
        if mr.n_trades >= 3 and mr.profit_factor > best_pf:
            best = "mean_reversion"
            best_pf = mr.profit_factor
        if mb.n_trades >= 3 and mb.profit_factor > best_pf:
            best = "momentum_breakout"
            best_pf = mb.profit_factor

        trend_pf = trend_results.get(sym, (0, 0, 0, 0, 0, 0))[2]
        if trend_pf > best_pf and trend_results[sym][0] >= 3:
            best = "trend_zone"
            best_pf = trend_pf

        print(f"  {sym:<8} best={best:<20} PF={best_pf:.2f}")

    print("\nDone.")


if __name__ == "__main__":
    main()

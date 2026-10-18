"""Analyze and backtest strategies for US index symbols (SPX500, NDX100)."""

import sys
import numpy as np
import pandas as pd

from mt5_mcp.client import get_client
from mt5_mcp.indicators import atr, rsi

SYMBOLS = ["SPX500", "NDX100"]
TIMEFRAME = "H1"
BARS = 2000


def fetch_data(symbol: str) -> pd.DataFrame:
    client = get_client()
    client.ensure_connected()
    try:
        frame = client.rates(symbol, TIMEFRAME, BARS)
        print(f"{symbol}: {len(frame)} bars fetched")
        return frame
    except Exception as e:
        print(f"{symbol}: failed - {e}")
        return pd.DataFrame()


def analyze_characteristics(symbol: str, df: pd.DataFrame):
    """Analyze volatility, trend persistence, mean-reversion tendency."""
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values

    # ATR
    atr_vals = atr(df, 14)
    atr_arr = atr_vals.values if hasattr(atr_vals, 'values') else atr_vals
    atr_median = float(np.nanmedian(atr_arr))
    atr_mean = float(np.nanmean(atr_arr))
    atr_pct = atr_median / close[-1] * 100

    # Typical move (bar-to-bar)
    returns = np.diff(close) / close[:-1] * 100
    move_median = float(np.median(np.abs(returns)))
    move_mean = float(np.mean(np.abs(returns)))

    # Trend persistence: how often does price stay on same side of EMA50?
    ema50 = pd.Series(close).ewm(span=50, adjust=False).mean().values
    above_ema = close > ema50
    streaks = []
    current_streak = 1
    for i in range(1, len(above_ema)):
        if above_ema[i] == above_ema[i - 1]:
            current_streak += 1
        else:
            streaks.append(current_streak)
            current_streak = 1
    streaks.append(current_streak)
    avg_streak = np.mean(streaks)

    # RSI extremes frequency
    rsi_vals = rsi(df["close"], 14).dropna().values
    rsi_below_30 = np.sum(rsi_vals < 30) / len(rsi_vals) * 100
    rsi_above_70 = np.sum(rsi_vals > 70) / len(rsi_vals) * 100
    rsi_40_60 = np.sum((rsi_vals >= 40) & (rsi_vals <= 60)) / len(rsi_vals) * 100

    # ADX (trend strength)
    adx_vals = _adx(df, 14)
    adx_valid = adx_vals[~np.isnan(adx_vals)]
    adx_above_25 = np.sum(adx_valid > 25) / len(adx_valid) * 100 if len(adx_valid) > 0 else 0
    adx_mean = float(np.mean(adx_valid)) if len(adx_valid) > 0 else 0

    # Consecutive up/down bars
    ups = np.sum(np.diff(close) > 0) / len(np.diff(close)) * 100

    print(f"\n{'='*70}")
    print(f"{symbol} CHARACTERISTICS ({TIMEFRAME}, {len(df)} bars)")
    print(f"{'='*70}")
    print(f"  Price range: {close.min():.2f} - {close.max():.2f} (current: {close[-1]:.2f})")
    print(f"  ATR(14): median={atr_median:.2f} ({atr_pct:.3f}%), mean={atr_mean:.2f}")
    print(f"  Bar move: median={move_median:.4f}%, mean={move_mean:.4f}%")
    print(f"  Above EMA50 streak: avg {avg_streak:.1f} bars")
    print(f"  RSI distribution: <30={rsi_below_30:.1f}%, >70={rsi_above_70:.1f}%, 40-60={rsi_40_60:.1f}%")
    print(f"  ADX: mean={adx_mean:.1f}, >25 {adx_above_25:.1f}% of time")
    print(f"  Up bars: {ups:.1f}%")

    return {
        "atr_median": atr_median, "atr_pct": atr_pct,
        "rsi_extreme_pct": rsi_below_30 + rsi_above_70,
        "rsi_mid_pct": rsi_40_60,
        "adx_above_25_pct": adx_above_25,
        "adx_mean": adx_mean,
    }


def _adx(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    high = df["high"]
    low = df["low"]
    close = df["close"]
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
    adx = dx.rolling(period).mean()
    return adx.values  # keep NaN for warmup, aligns with other arrays


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0):
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()
    return sma + std_dev * std, sma, sma - std_dev * std


class EquityCurve:
    def __init__(self, initial=10000.0):
        self.equity = initial
        self.peak = initial
        self.max_dd = 0.0
        self.initial = initial
        self.trades = []

    def record(self, pnl, detail=None):
        self.equity += pnl
        if self.equity > self.peak:
            self.peak = self.equity
        dd = (self.peak - self.equity) / self.peak
        if dd > self.max_dd:
            self.max_dd = dd
        self.trades.append({"pnl": pnl, **(detail or {})})

    @property
    def max_dd_pct(self):
        return self.max_dd * 100

    @property
    def return_pct(self):
        return (self.equity - self.initial) / self.initial * 100


def backtest_strategy(symbol: str, df: pd.DataFrame, strategy_name: str, params: dict) -> dict:
    """Run a backtest for a given strategy on the symbol."""
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    n = len(close)

    atr_vals = atr(df, 14).values
    rsi_vals = rsi(df["close"], 14).values
    adx_vals = _adx(df, 14)

    eq = EquityCurve()
    risk_per_trade = params.get("risk_percent", 0.5) / 100 * eq.initial

    if strategy_name == "trend_zone":
        _backtest_trend_zone(close, high, low, atr_vals, rsi_vals, adx_vals, eq, params)
    elif strategy_name == "mean_reversion":
        _backtest_mean_reversion(close, high, low, atr_vals, rsi_vals, adx_vals, eq, params)
    elif strategy_name == "momentum_breakout":
        _backtest_momentum_breakout(close, high, low, atr_vals, rsi_vals, adx_vals, eq, params)
    elif strategy_name == "gap_and_go":
        _backtest_gap_and_go(close, high, low, atr_vals, rsi_vals, adx_vals, eq, params)
    elif strategy_name == "ema_crossover":
        _backtest_ema_crossover(close, high, low, atr_vals, rsi_vals, adx_vals, eq, params)
    elif strategy_name == "session_momentum":
        _backtest_session_momentum(close, high, low, atr_vals, rsi_vals, adx_vals, eq, params)

    wins = sum(1 for t in eq.trades if t["pnl"] > 0)
    losses = sum(1 for t in eq.trades if t["pnl"] <= 0)
    gross_profit = sum(t["pnl"] for t in eq.trades if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in eq.trades if t["pnl"] <= 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else 999.0
    avg_win = gross_profit / wins if wins else 0
    avg_loss = gross_loss / losses if losses else 0
    expectancy = (gross_profit - gross_loss) / len(eq.trades) if eq.trades else 0

    return {
        "symbol": symbol,
        "strategy": strategy_name,
        "trades": len(eq.trades),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(eq.trades) * 100 if eq.trades else 0,
        "profit_factor": pf,
        "net_pnl": eq.equity - eq.initial,
        "return_pct": eq.return_pct,
        "max_dd_pct": eq.max_dd_pct,
        "expectancy": expectancy,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
    }


def _backtest_trend_zone(close, high, low, atr_vals, rsi_vals, adx_vals, eq, params):
    """Trend + zone + confirmation candle."""
    n = len(close)
    in_trade = False
    for i in range(200, n - 1):
        if in_trade:
            # Check exit
            if direction == "BUY":
                if low[i] <= sl or high[i] >= tp:
                    pnl = (tp - entry) * lot_scale if high[i] >= tp else (sl - entry) * lot_scale
                    eq.record(pnl, {"type": "trend_zone", "dir": direction})
                    in_trade = False
            else:
                if high[i] >= sl or low[i] <= tp:
                    pnl = (entry - tp) * lot_scale if low[i] <= tp else (entry - sl) * lot_scale
                    eq.record(pnl, {"type": "trend_zone", "dir": direction})
                    in_trade = False
            continue

        if np.isnan(atr_vals[i]) or np.isnan(rsi_vals[i]) or np.isnan(adx_vals[i]):
            continue
        if adx_vals[i] < 20:
            continue

        # EMA trend
        ema20 = np.mean(close[i-19:i+1])
        ema50 = np.mean(close[i-49:i+1])

        atr_val = atr_vals[i]
        lot_scale = atr_val * 0.01

        # BUY: uptrend + pullback to zone + bullish candle
        if ema20 > ema50 and rsi_vals[i] < 45 and close[i] > open_approx(close, high, low, i):
            if close[i] > close[i-1]:  # bullish confirmation
                direction = "BUY"
                entry = close[i]
                sl = entry - 2.0 * atr_val
                tp = entry + 3.0 * atr_val
                in_trade = True

        # SELL: downtrend + pullback + bearish candle
        elif ema20 < ema50 and rsi_vals[i] > 55 and close[i] < open_approx(close, high, low, i):
            if close[i] < close[i-1]:
                direction = "SELL"
                entry = close[i]
                sl = entry + 2.0 * atr_val
                tp = entry - 3.0 * atr_val
                in_trade = True


def open_approx(close, high, low, i):
    """Approximate open price."""
    return (high[i] + low[i] + close[i]) / 3


def _backtest_mean_reversion(close, high, low, atr_vals, rsi_vals, adx_vals, eq, params):
    """Mean reversion: RSI extremes + BB touches in ranging markets."""
    n = len(close)
    in_trade = False
    bb_upper, bb_mid, bb_lower = _bollinger_bands(pd.Series(close), 20, 2.0)
    bb_upper = bb_upper.values
    bb_lower = bb_lower.values

    for i in range(200, n - 1):
        if in_trade:
            if direction == "BUY":
                if low[i] <= sl or high[i] >= tp:
                    pnl = (tp - entry) * lot_scale if high[i] >= tp else (sl - entry) * lot_scale
                    eq.record(pnl, {"type": "mean_reversion", "dir": direction})
                    in_trade = False
            else:
                if high[i] >= sl or low[i] <= tp:
                    pnl = (entry - tp) * lot_scale if low[i] <= tp else (entry - sl) * lot_scale
                    eq.record(pnl, {"type": "mean_reversion", "dir": direction})
                    in_trade = False
            continue

        if np.isnan(atr_vals[i]) or np.isnan(rsi_vals[i]) or np.isnan(adx_vals[i]):
            continue
        if np.isnan(bb_upper[i]) or np.isnan(bb_lower[i]):
            continue

        # Only in ranging markets
        if adx_vals[i] >= 25:
            continue

        atr_val = atr_vals[i]
        lot_scale = atr_val * 0.01

        if rsi_vals[i] < 30 and close[i] <= bb_lower[i] * 1.002:
            direction = "BUY"
            entry = close[i]
            sl = entry - 1.5 * atr_val
            tp = entry + 3.0 * atr_val
            in_trade = True
        elif rsi_vals[i] > 70 and close[i] >= bb_upper[i] * 0.998:
            direction = "SELL"
            entry = close[i]
            sl = entry + 1.5 * atr_val
            tp = entry - 3.0 * atr_val
            in_trade = True


def _backtest_momentum_breakout(close, high, low, atr_vals, rsi_vals, adx_vals, eq, params):
    """Momentum breakout: ADX > 25 + price breaking 20-bar high/low."""
    n = len(close)
    in_trade = False

    for i in range(220, n - 1):
        if in_trade:
            if direction == "BUY":
                if low[i] <= sl or high[i] >= tp:
                    pnl = (tp - entry) * lot_scale if high[i] >= tp else (sl - entry) * lot_scale
                    eq.record(pnl, {"type": "momentum_breakout", "dir": direction})
                    in_trade = False
            else:
                if high[i] >= sl or low[i] <= tp:
                    pnl = (entry - tp) * lot_scale if low[i] <= tp else (entry - sl) * lot_scale
                    eq.record(pnl, {"type": "momentum_breakout", "dir": direction})
                    in_trade = False
            continue

        if np.isnan(atr_vals[i]) or np.isnan(adx_vals[i]):
            continue
        if adx_vals[i] < 25:
            continue

        lookback_high = np.max(high[i-20:i])
        lookback_low = np.min(low[i-20:i])
        atr_val = atr_vals[i]
        lot_scale = atr_val * 0.01

        if close[i] > lookback_high:
            direction = "BUY"
            entry = close[i]
            sl = entry - 2.0 * atr_val
            tp = entry + 4.0 * atr_val
            in_trade = True
        elif close[i] < lookback_low:
            direction = "SELL"
            entry = close[i]
            sl = entry + 2.0 * atr_val
            tp = entry - 4.0 * atr_val
            in_trade = True


def _backtest_gap_and_go(close, high, low, atr_vals, rsi_vals, adx_vals, eq, params):
    """Gap and go: opening range breakout for indices.

    Indices tend to gap at open and continue. We simulate this by looking for
    bars with unusually large range (>1.5x ATR) that close in the direction
    of the gap, then enter in that direction.
    """
    n = len(close)
    in_trade = False

    for i in range(200, n - 1):
        if in_trade:
            if direction == "BUY":
                if low[i] <= sl or high[i] >= tp:
                    pnl = (tp - entry) * lot_scale if high[i] >= tp else (sl - entry) * lot_scale
                    eq.record(pnl, {"type": "gap_and_go", "dir": direction})
                    in_trade = False
            else:
                if high[i] >= sl or low[i] <= tp:
                    pnl = (entry - tp) * lot_scale if low[i] <= tp else (entry - sl) * lot_scale
                    eq.record(pnl, {"type": "gap_and_go", "dir": direction})
                    in_trade = False
            continue

        if np.isnan(atr_vals[i]):
            continue

        bar_range = high[i] - low[i]
        atr_val = atr_vals[i]
        lot_scale = atr_val * 0.01
        body = close[i] - (high[i] + low[i] + close[i-1]) / 3  # approx open

        # Large range bar closing strong in one direction
        if bar_range > 1.5 * atr_val:
            if body > 0 and close[i] > close[i-1]:
                direction = "BUY"
                entry = close[i]
                sl = entry - 1.5 * atr_val
                tp = entry + 3.0 * atr_val
                in_trade = True
            elif body < 0 and close[i] < close[i-1]:
                direction = "SELL"
                entry = close[i]
                sl = entry + 1.5 * atr_val
                tp = entry - 3.0 * atr_val
                in_trade = True


def _backtest_ema_crossover(close, high, low, atr_vals, rsi_vals, adx_vals, eq, params):
    """EMA crossover with trend filter.

    BUY when EMA20 crosses above EMA50 and ADX > 20 (trending).
    SELL when EMA20 crosses below EMA50 and ADX > 20.
    """
    n = len(close)
    in_trade = False
    close_series = pd.Series(close)
    ema20 = _ema(close_series, 20).values
    ema50 = _ema(close_series, 50).values

    for i in range(200, n - 1):
        if in_trade:
            if direction == "BUY":
                if low[i] <= sl or high[i] >= tp:
                    pnl = (tp - entry) * lot_scale if high[i] >= tp else (sl - entry) * lot_scale
                    eq.record(pnl, {"type": "ema_crossover", "dir": direction})
                    in_trade = False
            else:
                if high[i] >= sl or low[i] <= tp:
                    pnl = (entry - tp) * lot_scale if low[i] <= tp else (entry - sl) * lot_scale
                    eq.record(pnl, {"type": "ema_crossover", "dir": direction})
                    in_trade = False
            continue

        if np.isnan(atr_vals[i]) or np.isnan(adx_vals[i]):
            continue
        if np.isnan(ema20[i]) or np.isnan(ema50[i]):
            continue

        atr_val = atr_vals[i]
        lot_scale = atr_val * 0.01

        # EMA20 crosses above EMA50
        if ema20[i-1] <= ema50[i-1] and ema20[i] > ema50[i] and adx_vals[i] > 20:
            direction = "BUY"
            entry = close[i]
            sl = entry - 2.0 * atr_val
            tp = entry + 4.0 * atr_val
            in_trade = True
        elif ema20[i-1] >= ema50[i-1] and ema20[i] < ema50[i] and adx_vals[i] > 20:
            direction = "SELL"
            entry = close[i]
            sl = entry + 2.0 * atr_val
            tp = entry - 4.0 * atr_val
            in_trade = True


def _backtest_session_momentum(close, high, low, atr_vals, rsi_vals, adx_vals, eq, params):
    """Session momentum: trade the continuation after a consolidation.

    Look for tight consolidation (range < 0.5 ATR over 5 bars) followed by
    a breakout bar (>1 ATR range) in the direction of the broader trend (EMA50 slope).
    """
    n = len(close)
    in_trade = False
    close_series = pd.Series(close)
    ema50 = _ema(close_series, 50).values

    for i in range(205, n - 1):
        if in_trade:
            if direction == "BUY":
                if low[i] <= sl or high[i] >= tp:
                    pnl = (tp - entry) * lot_scale if high[i] >= tp else (sl - entry) * lot_scale
                    eq.record(pnl, {"type": "session_momentum", "dir": direction})
                    in_trade = False
            else:
                if high[i] >= sl or low[i] <= tp:
                    pnl = (entry - tp) * lot_scale if low[i] <= tp else (entry - sl) * lot_scale
                    eq.record(pnl, {"type": "session_momentum", "dir": direction})
                    in_trade = False
            continue

        if np.isnan(atr_vals[i]) or np.isnan(ema50[i]):
            continue

        atr_val = atr_vals[i]
        lot_scale = atr_val * 0.01

        # Check for consolidation (tight 5-bar range)
        consol_range = np.max(high[i-5:i]) - np.min(low[i-5:i])
        if consol_range > 0.8 * atr_val:
            continue

        # Breakout bar
        bar_range = high[i] - low[i]
        if bar_range < 1.0 * atr_val:
            continue

        # Trend direction from EMA50 slope
        ema_slope = ema50[i] - ema50[i-5] if i >= 5 else 0

        if close[i] > np.max(high[i-5:i]) and ema_slope > 0:
            direction = "BUY"
            entry = close[i]
            sl = entry - 1.5 * atr_val
            tp = entry + 3.5 * atr_val
            in_trade = True
        elif close[i] < np.min(low[i-5:i]) and ema_slope < 0:
            direction = "SELL"
            entry = close[i]
            sl = entry + 1.5 * atr_val
            tp = entry - 3.5 * atr_val
            in_trade = True


def main():
    all_results = []

    for symbol in SYMBOLS:
        df = fetch_data(symbol)
        if df.empty or len(df) < 200:
            print(f"{symbol}: insufficient data ({len(df)} bars), skipping")
            continue

        chars = analyze_characteristics(symbol, df)

        strategies = [
            ("trend_zone", {}),
            ("mean_reversion", {}),
            ("momentum_breakout", {}),
            ("ema_crossover", {}),
            ("gap_and_go", {}),
            ("session_momentum", {}),
        ]

        print(f"\n{'='*70}")
        print(f"BACKTESTING {symbol}")
        print(f"{'='*70}")
        print(f"{'Strategy':<22} {'Trades':>7} {'WinRate':>8} {'PF':>7} {'Return':>9} {'MaxDD':>8} {'Expect':>9}")
        print(f"{'-'*70}")

        for strat_name, params in strategies:
            result = backtest_strategy(symbol, df, strat_name, params)
            all_results.append(result)
            print(f"{strat_name:<22} {result['trades']:>7} {result['win_rate']:>7.1f}% "
                  f"{result['profit_factor']:>7.2f} {result['return_pct']:>+8.2f}% "
                  f"{result['max_dd_pct']:>7.2f}% ${result['expectancy']:>7.2f}")

    # Summary
    print(f"\n{'='*70}")
    print("RANKED BY PROFIT FACTOR")
    print(f"{'='*70}")
    profitable = [r for r in all_results if r["trades"] >= 5]
    profitable.sort(key=lambda r: r["profit_factor"], reverse=True)
    for r in profitable:
        marker = " <--" if r["profit_factor"] >= 1.2 else ""
        print(f"  {r['symbol']:>8} {r['strategy']:<22} PF={r['profit_factor']:.2f}  "
              f"WR={r['win_rate']:.1f}%  Ret={r['return_pct']:+.2f}%  "
              f"Trades={r['trades']}{marker}")


if __name__ == "__main__":
    main()

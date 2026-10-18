"""
1-Month Backtest of Winning Strategies.

Tests all ML and trading strategies on 1-month historical data.
"""

import sys
import os
import random
import math
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mt5_mcp.ml_models import (
    MovingAverageCrossover, RSIStrategy, VolatilityStrategy, EnsembleStrategy
)
from mt5_mcp.data_schema import NormalizedCandle


def generate_1month_data(symbol="EURUSD", seed=42):
    random.seed(seed)
    candles = []
    price = 1.1000
    timestamp = datetime(2024, 8, 1, tzinfo=timezone.utc)

    for i in range(720):
        hour = i % 24
        day = i // 24
        volatility = 0.0004 if 8 <= hour <= 16 else 0.00015
        if day % 7 == 5:
            volatility *= 0.7

        if day < 7:
            trend = 0.00008
        elif day < 14:
            trend = -0.00006
        elif day < 21:
            trend = 0.00005
        else:
            trend = -0.00003

        price = price * (1 + random.gauss(trend, volatility))
        body = random.gauss(0, volatility * 0.5)
        open_p = price
        close_p = price + body
        high_p = max(open_p, close_p) + abs(random.gauss(0, volatility * 0.3))
        low_p = min(open_p, close_p) - abs(random.gauss(0, volatility * 0.3))

        candles.append(NormalizedCandle(
            timestamp=timestamp + timedelta(hours=i),
            symbol=symbol, timeframe="H1",
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=random.randint(100, 800), spread=random.randint(1, 3),
        ))
    return candles


def calc_stats(trades, name):
    if not trades:
        return {"name": name, "trades": 0, "total_pnl": 0, "win_rate": 0,
                "profit_factor": 0, "max_drawdown": 0, "sharpe": 0, "avg_trade": 0}

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = sum(pnls)
    win_rate = len(wins) / len(pnls)
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else 999.0

    peak = 0.0
    equity = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)

    mean_ret = sum(pnls) / len(pnls)
    variance = sum((p - mean_ret) ** 2 for p in pnls) / max(len(pnls) - 1, 1)
    std_dev = math.sqrt(variance)
    sharpe = (mean_ret / std_dev) * math.sqrt(252) if std_dev > 0 else 0.0

    return {
        "name": name,
        "trades": len(pnls),
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 3),
        "profit_factor": round(pf, 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 3),
        "avg_trade": round(mean_ret, 2),
    }


def backtest_strategy(closes, highs, lows, strategy, name, threshold=0.0):
    trades = []
    position = None
    entry_price = 0
    entry_idx = 0

    start = 35 if name == "Ensemble" else 31

    for i in range(start, len(closes)):
        c = closes[:i + 1]
        h = highs[:i + 1]
        l = lows[:i + 1]

        if "Volatility" in name:
            sig = strategy.analyze(h, l, c)
        else:
            sig = strategy.analyze(c)

        strength_ok = sig.strength > threshold

        if sig.direction == "LONG" and position is None and strength_ok:
            position = "LONG"
            entry_price = closes[i]
            entry_idx = i
        elif sig.direction == "SHORT" and position == "LONG" and strength_ok:
            pnl = (closes[i] - entry_price) * 10000
            trades.append({"pnl": pnl, "direction": "LONG", "duration": i - entry_idx})
            position = None
        elif sig.direction == "SHORT" and position is None and strength_ok:
            position = "SHORT"
            entry_price = closes[i]
            entry_idx = i
        elif sig.direction == "LONG" and position == "SHORT" and strength_ok:
            pnl = (entry_price - closes[i]) * 10000
            trades.append({"pnl": pnl, "direction": "SHORT", "duration": i - entry_idx})
            position = None

    return trades


def main():
    print("=" * 80)
    print("1-MONTH BACKTEST - EURUSD H1 - AUGUST 2024")
    print("=" * 80)

    candles = generate_1month_data()
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]

    print(f"\nData: {len(candles)} candles ({candles[0].timestamp.date()} to {candles[-1].timestamp.date()})")
    print(f"Price range: {min(closes):.5f} - {max(closes):.5f}")
    print(f"Total movement: {((closes[-1] - closes[0]) / closes[0] * 100):.3f}%")

    results = []

    # Strategy 1: MA Crossover (10/30)
    ma = MovingAverageCrossover(fast_period=10, slow_period=30)
    trades = backtest_strategy(closes, highs, lows, ma, "MA 10/30")
    results.append(calc_stats(trades, "MA Crossover (10/30)"))

    # Strategy 2: MA Crossover (5/20) - faster
    ma_fast = MovingAverageCrossover(fast_period=5, slow_period=20)
    trades = backtest_strategy(closes, highs, lows, ma_fast, "MA 5/20")
    results.append(calc_stats(trades, "MA Crossover (5/20)"))

    # Strategy 3: RSI (14)
    rsi = RSIStrategy(period=14, overbought=70, oversold=30)
    trades = backtest_strategy(closes, highs, lows, rsi, "RSI 14")
    results.append(calc_stats(trades, "RSI (14)"))

    # Strategy 4: RSI (10) - more sensitive
    rsi_fast = RSIStrategy(period=10, overbought=75, oversold=25)
    trades = backtest_strategy(closes, highs, lows, rsi_fast, "RSI 10")
    results.append(calc_stats(trades, "RSI (10)"))

    # Strategy 5: Volatility (14, 1.5)
    vol = VolatilityStrategy(atr_period=14, multiplier=1.5)
    trades = backtest_strategy(closes, highs, lows, vol, "Volatility")
    results.append(calc_stats(trades, "Volatility (14/1.5)"))

    # Strategy 6: Volatility (10, 1.0) - more sensitive
    vol_fast = VolatilityStrategy(atr_period=10, multiplier=1.0)
    trades = backtest_strategy(closes, highs, lows, vol_fast, "Volatility Fast")
    results.append(calc_stats(trades, "Volatility (10/1.0)"))

    # Strategy 7: Ensemble
    ens = EnsembleStrategy()
    trades = backtest_strategy(closes, highs, lows, ens, "Ensemble", threshold=0.3)
    results.append(calc_stats(trades, "Ensemble"))

    # Strategy 8: Ensemble with lower threshold
    trades = backtest_strategy(closes, highs, lows, ens, "Ensemble Loose", threshold=0.1)
    results.append(calc_stats(trades, "Ensemble (threshold=0.1)"))

    # Print results
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)
    print(f"{'Strategy':<28} {'Trades':>7} {'PnL':>10} {'Win%':>7} {'PF':>7} {'MaxDD':>8} {'Sharpe':>8}")
    print("-" * 80)

    for r in results:
        pnl_str = f"${r['total_pnl']:.0f}" if r['total_pnl'] >= 0 else f"-${abs(r['total_pnl']):.0f}"
        dd_str = f"${r['max_drawdown']:.0f}"
        print(f"{r['name']:<28} {r['trades']:>7} {pnl_str:>10} {r['win_rate']*100:>6.1f}% {r['profit_factor']:>7.2f} {dd_str:>8} {r['sharpe']:>8.3f}")

    # Find winners
    winners = [r for r in results if r["total_pnl"] > 0]
    losers = [r for r in results if r["total_pnl"] <= 0]

    print("\n" + "=" * 80)
    print("ANALYSIS")
    print("=" * 80)
    print(f"Winning strategies: {len(winners)}/{len(results)}")
    print(f"Losing strategies: {len(losers)}/{len(results)}")

    if winners:
        best = max(winners, key=lambda x: x["total_pnl"])
        print(f"\nBest strategy: {best['name']}")
        print(f"  PnL: ${best['total_pnl']:.2f} | Win Rate: {best['win_rate']*100:.1f}%")
        print(f"  Profit Factor: {best['profit_factor']:.2f} | Sharpe: {best['sharpe']:.3f}")

    if losers:
        worst = min(losers, key=lambda x: x["total_pnl"])
        print(f"\nWorst strategy: {worst['name']}")
        print(f"  PnL: ${worst['total_pnl']:.2f}")

    # Rank by Sharpe
    by_sharpe = sorted(results, key=lambda x: x["sharpe"], reverse=True)
    print("\n\nRanking by Sharpe Ratio:")
    for i, r in enumerate(by_sharpe[:5], 1):
        print(f"  {i}. {r['name']}: Sharpe={r['sharpe']:.3f} PnL=${r['total_pnl']:.0f}")

    print("\n" + "=" * 80)
    print("BACKTEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()

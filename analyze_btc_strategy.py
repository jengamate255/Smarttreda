"""Analyze BTC volatility characteristics to design a suitable strategy.

BTC fails with standard strategies because:
1. Extreme volatility (fixed pip SL gets hit too easily)
2. Long trends with violent pullbacks
3. 24/7 market (no session opens)

This script analyzes:
- ATR distribution (what's normal vs extreme volatility)
- Typical move sizes (how far BTC moves in 1, 4, 12, 24 bars)
- Trend persistence (how long trends last)
- Pullback depth (how deep are retracements before continuation)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from mt5_mcp.backtest import load_frames
from mt5_mcp.client import get_client
from mt5_mcp.indicators import atr, ema, rsi


def analyze_btc():
    client = get_client()
    client.ensure_connected()

    print("Fetching BTCUSD data...")
    frames = load_frames("BTCUSD", ["H1", "H4"], 5000)
    h1 = frames["H1"]
    h4 = frames["H4"]

    print(f"H1: {len(h1)} bars, H4: {len(h4)} bars\n")

    # 1. ATR analysis
    atr_h1 = atr(h1, 14)
    atr_h4 = atr(h4, 14)

    print("=" * 80)
    print("ATR ANALYSIS (volatility)")
    print("=" * 80)
    print(f"H1 ATR: mean=${atr_h1.mean():.2f}, median=${atr_h1.median():.2f}, "
          f"std=${atr_h1.std():.2f}")
    print(f"  25th percentile: ${atr_h1.quantile(0.25):.2f}")
    print(f"  75th percentile: ${atr_h1.quantile(0.75):.2f}")
    print(f"  95th percentile: ${atr_h1.quantile(0.95):.2f}")
    print(f"H4 ATR: mean=${atr_h4.mean():.2f}, median=${atr_h4.median():.2f}")
    print()

    # 2. Typical move sizes
    print("=" * 80)
    print("TYPICAL MOVE SIZES (close-to-close)")
    print("=" * 80)
    for n in [1, 4, 12, 24, 48]:
        moves = (h1["close"].shift(-n) - h1["close"]).abs()
        moves = moves.dropna()
        print(f"{n:2d} bars ({n:2d}h): mean=${moves.mean():.2f}, "
              f"median=${moves.median():.2f}, 90th=${moves.quantile(0.90):.2f}")
    print()

    # 3. Trend persistence
    print("=" * 80)
    print("TREND PERSISTENCE (EMA 50/200)")
    print("=" * 80)
    ema50 = ema(h1["close"], 50)
    ema200 = ema(h1["close"], 200)
    bullish = (ema50 > ema200).astype(int)
    changes = bullish.diff().abs()
    trend_changes = changes.sum()
    avg_trend_length = len(h1) / trend_changes if trend_changes > 0 else 0
    print(f"Trend changes (50/200 EMA cross): {trend_changes}")
    print(f"Average trend length: {avg_trend_length:.0f} bars ({avg_trend_length/24:.1f} days)")
    print()

    # 4. Pullback analysis
    print("=" * 80)
    print("PULLBACK DEPTH (during uptrends)")
    print("=" * 80)
    # Find uptrends (EMA50 > EMA200)
    in_uptrend = ema50 > ema200
    pullbacks = []
    current_pullback = 0
    peak = 0

    for i in range(200, len(h1)):
        if in_uptrend.iloc[i]:
            price = h1["close"].iloc[i]
            if price > peak:
                peak = price
                if current_pullback > 0:
                    pullbacks.append(current_pullback)
                current_pullback = 0
            else:
                pb = (peak - price) / peak * 100
                current_pullback = max(current_pullback, pb)

    if pullbacks:
        pullbacks = np.array(pullbacks)
        print(f"Pullbacks during uptrends: {len(pullbacks)}")
        print(f"  Mean: {pullbacks.mean():.2f}%")
        print(f"  Median: {np.median(pullbacks):.2f}%")
        print(f"  75th percentile: {np.percentile(pullbacks, 75):.2f}%")
        print(f"  90th percentile: {np.percentile(pullbacks, 90):.2f}%")
        print(f"  95th percentile: {np.percentile(pullbacks, 95):.2f}%")
    print()

    # 5. RSI extremes and duration
    print("=" * 80)
    print("RSI EXTREMES (overbought/oversold duration)")
    print("=" * 80)
    rsi_series = rsi(h1["close"], 14)
    overbought = rsi_series > 70
    oversold = rsi_series < 30

    ob_durations = []
    os_durations = []
    current = 0

    for i in range(len(rsi_series)):
        if overbought.iloc[i]:
            current += 1
        else:
            if current > 0:
                ob_durations.append(current)
            current = 0

    current = 0
    for i in range(len(rsi_series)):
        if oversold.iloc[i]:
            current += 1
        else:
            if current > 0:
                os_durations.append(current)
            current = 0

    if ob_durations:
        print(f"Overbought (RSI>70) episodes: {len(ob_durations)}")
        print(f"  Mean duration: {np.mean(ob_durations):.1f} bars ({np.mean(ob_durations)/24:.1f} days)")
        print(f"  Max duration: {np.max(ob_durations)} bars ({np.max(ob_durations)/24:.1f} days)")
    if os_durations:
        print(f"Oversold (RSI<30) episodes: {len(os_durations)}")
        print(f"  Mean duration: {np.mean(os_durations):.1f} bars ({np.mean(os_durations)/24:.1f} days)")
        print(f"  Max duration: {np.max(os_durations)} bars ({np.max(os_durations)/24:.1f} days)")
    print()

    # 6. Consecutive bar analysis
    print("=" * 80)
    print("CONSECUTIVE DIRECTIONAL MOVES")
    print("=" * 80)
    returns = h1["close"].pct_change()
    pos = (returns > 0).astype(int)
    neg = (returns < 0).astype(int)

    pos_streaks = []
    neg_streaks = []
    current = 0

    for i in range(len(pos)):
        if pos.iloc[i]:
            current += 1
        else:
            if current > 0:
                pos_streaks.append(current)
            current = 0

    current = 0
    for i in range(len(neg)):
        if neg.iloc[i]:
            current += 1
        else:
            if current > 0:
                neg_streaks.append(current)
            current = 0

    if pos_streaks:
        print(f"Consecutive up moves: {len(pos_streaks)} streaks")
        print(f"  Mean length: {np.mean(pos_streaks):.1f} bars")
        print(f"  Max length: {np.max(pos_streaks)} bars")
    if neg_streaks:
        print(f"Consecutive down moves: {len(neg_streaks)} streaks")
        print(f"  Mean length: {np.mean(neg_streaks):.1f} bars")
        print(f"  Max length: {np.max(neg_streaks)} bars")
    print()

    # 7. Volatility regime
    print("=" * 80)
    print("VOLATILITY REGIMES (ATR percentile)")
    print("=" * 80)
    atr_pct = atr_h1.rolling(200).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1])
    low_vol = (atr_pct < 0.25).sum()
    med_vol = ((atr_pct >= 0.25) & (atr_pct < 0.75)).sum()
    high_vol = (atr_pct >= 0.75).sum()
    print(f"Low volatility (ATR < 25th %ile): {low_vol} bars ({low_vol/len(h1)*100:.1f}%)")
    print(f"Medium volatility: {med_vol} bars ({med_vol/len(h1)*100:.1f}%)")
    print(f"High volatility (ATR > 75th %ile): {high_vol} bars ({high_vol/len(h1)*100:.1f}%)")
    print()

    print("=" * 80)
    print("STRATEGY IMPLICATIONS")
    print("=" * 80)
    print(f"1. Use ATR-based stops: SL = 2-3x ATR (not fixed pips)")
    print(f"   Current H1 ATR median: ${atr_h1.median():.2f}")
    print(f"   Suggested SL: ${atr_h1.median() * 2.5:.2f} (2.5x ATR)")
    print()
    print(f"2. Wider TP targets: BTC moves ${h1['close'].diff(24).abs().median():.2f} in 24h")
    print(f"   Suggested TP: 3-5x ATR or trailing stop")
    print()
    print(f"3. Pullbacks are {np.median(pullbacks):.1f}% on average — need wide stops or")
    print(f"   enter on pullback completion (RSI divergence, volume spike)")
    print()
    print(f"4. Trends last {avg_trend_length/24:.1f} days on average — trend following works")
    print(f"   but requires patience and wide stops")


if __name__ == "__main__":
    analyze_btc()

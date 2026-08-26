"""Real vectorized BTC H4 Donchian breakout backtest.

Long: candle i close breaks above PRIOR 'lookback'-bar channel high AND closes up.
Short: candle i close breaks below PRIOR 'lookback'-bar channel low AND closes down.
Entry at bar i close (signal bar open). SL/TP based on ATR(atr_period)*atr_mult.
Bar-by-bar OHLC simulation (gap-aware) of which of SL/TP is hit first.
No random PnL, no lookahead past the signal bar.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from backtest import load_frames  # noqa: E402


def atr_series(h, l, c, period):
    n = len(c)
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    a = np.empty(n)
    a[0] = tr[0]
    for i in range(1, n):
        a[i] = (a[i - 1] * (period - 1) + tr[i]) / period
    return a


def run(lookback, atr_period, atr_mult, rr, df):
    h = np.asarray(df['high'], dtype=float)
    l = np.asarray(df['low'], dtype=float)
    o = np.asarray(df['open'], dtype=float)
    c = np.asarray(df['close'], dtype=float)

    n = len(c)
    H_prior = np.empty(n)
    L_prior = np.empty(n)
    for i in range(n):
        start = max(0, i - lookback)
        if start < i:
            H_prior[i] = h[start:i].max()
            L_prior[i] = l[start:i].min()
        else:
            H_prior[i] = h[i]
            L_prior[i] = l[i]

    A = atr_series(h, l, c, atr_period)

    equity = 100.0
    peak = 100.0
    max_dd = 0.0
    wins = 0
    gross_win = 0.0
    gross_loss = 0.0
    trades = []
    i = lookback

    while i < n - 1:
        side = 0
        if c[i] > H_prior[i] and c[i] > c[i - 1]:
            side = 1
        elif c[i] < L_prior[i] and c[i] < c[i - 1]:
            side = -1

        if side == 0:
            i += 1
            continue

        entry = c[i]
        sl_dist = A[i] * atr_mult
        sl = entry - sl_dist if side == 1 else entry + sl_dist
        tp = entry + sl_dist * rr if side == 1 else entry - sl_dist * rr

        pnl_pct = None
        exit_i = None
        for j in range(i + 1, n):
            bar_o, bar_h, bar_l = o[j], h[j], l[j]
            if side == 1:
                if bar_h >= tp:
                    pnl_pct = (tp - entry) / entry * 100.0
                    exit_i = j
                    break
                if bar_l <= sl:
                    pnl_pct = (sl - entry) / entry * 100.0
                    exit_i = j
                    break
                if bar_o >= tp:
                    pnl_pct = (bar_o - entry) / entry * 100.0
                    exit_i = j
                    break
                if bar_o <= sl:
                    pnl_pct = (bar_o - entry) / entry * 100.0
                    exit_i = j
                    break
            else:
                if bar_l <= tp:
                    pnl_pct = (tp - entry) / entry * 100.0
                    exit_i = j
                    break
                if bar_h >= sl:
                    pnl_pct = (sl - entry) / entry * 100.0
                    exit_i = j
                    break
                if bar_o <= tp:
                    pnl_pct = (bar_o - entry) / entry * 100.0
                    exit_i = j
                    break
                if bar_o >= sl:
                    pnl_pct = (bar_o - entry) / entry * 100.0
                    exit_i = j
                    break

        if pnl_pct is None or exit_i is None:
            i += 1
            continue

        equity *= (1 + pnl_pct / 100.0)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100.0)
        if pnl_pct > 0:
            wins += 1
            gross_win += pnl_pct
        else:
            gross_loss += pnl_pct
        trades.append(pnl_pct)

        # skip forward to the exit so bars are not double-traded while inside a trade
        i = exit_i + 1

    total = len(trades)
    win_rate = wins / total * 100.0 if total else 0.0
    pf = gross_win / abs(gross_loss) if gross_loss < 0 else (0.0 if gross_win == 0 else 9e9)
    exp = sum(trades) / total if total else 0.0
    return {
        'n_trades': total,
        'wins': wins,
        'win_rate': win_rate,
        'profit_factor': pf,
        'net_return_pct': (equity - 100.0),
        'max_drawdown_pct': max_dd,
        'expectancy': exp,
    }


if __name__ == '__main__':
    import numpy as np

    print('BTCUSD H4 Donchian vectorized backtest (real SL/TP fills)')
    print('=' * 78)
    frames = load_frames('BTCUSD', ['H4'], 4000)
    df = frames['H4']
    print(f'data: {len(df)} H4 bars  {df["time"].iloc[0]} -> {df["time"].iloc[-1]}')

    tests = [
        ('D12 ATR2.0 RR3', 12, 14, 2.0, 3.0),
        ('D24 ATR2.0 RR3', 24, 14, 2.0, 3.0),
        ('D48 ATR2.0 RR3', 48, 14, 2.0, 3.0),
        ('D48 ATR2.5 RR3', 48, 14, 2.5, 3.0),
        ('D72 ATR2.0 RR3', 72, 14, 2.0, 3.0),
        ('D24 ATR1.5 RR2', 24, 14, 1.5, 2.0),
    ]
    results = []
    for label, lb, ap, mult, rr in tests:
        t0 = time.time()
        r = run(lb, ap, mult, rr, df)
        et = time.time() - t0
        print(f'{label:20s} n={r["n_trades"]:3d} win={r["win_rate"]:5.1f}% '
              f'PF={r["profit_factor"]:6.2f} ret={r["net_return_pct"]:+.2f}% '
              f'maxDD={r["max_drawdown_pct"]:5.2f}% exp={r["expectancy"]:+.2f} [{et:.1f}s]')
        r['name'] = label
        results.append(r)

    out = Path('backtest/results/btc_h4_donchian_vector.json')
    with open(out, 'w') as f:
        json.dump(results, f, indent=2)
    print(f'\nSaved to {out}')
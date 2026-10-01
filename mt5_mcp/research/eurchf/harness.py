"""Lookahead-safe bar-by-bar research harness for EURCHF strategy variants.

Replays strategies on H1 (and H1-only) data with the same discipline as
``mt5_mcp/backtest.py``:

  * signal evaluated at the close of bar i using ONLY data through bar i
    (all indicators are causal: ewm/rolling — prefix values == full-frame values)
  * entry at bar i+1 open
  * SL checked before TP inside a bar (SL wins if both touched)
  * one position at a time; resume at the bar after the exit bar
  * timeout exit at the close of the Nth held bar

Cost models (both are reported for every variant):

  canonical  — mirrors backtest.py defaults:
               spread = spread_points(15) * pip, slippage = 1 * pip per side,
               commission $7/lot round trip, vol-adjusted spread multiplier
               (ATR ratio, clamp 1..3). This is the model under which the
               canonical EURCHF trend_zone scored PF 0.28 — deliberately
               punitive (EURCHF live spread is ~0.7 pip).
  realistic  — actual per-bar broker spread (bar["spread"] * point),
               slippage = 1 * point, commission $7/lot round trip.

Signals implemented (see VARIANTS):
  mr     — multi_strategy.mean_reversion logic (RSI+BB touch, ADX gate, 25/50 pip)
  mb     — momentum_breakout INTENDED logic (prior 20-bar Donchian break, ADX gate,
           30/80 pip). NOTE: the as-written code compares close against a window
           that includes the current bar's high, which can never be exceeded —
           it can never fire. We test the intended prior-20-bar version.
  emac   — ema_crossover logic (EMA20/50 cross, ADX gate, 2 ATR SL / 4 ATR TP)
  bbmr   — NEW: Bollinger reversion to the middle band (ATR stop, band-mid target)
  donch  — NEW: ATR-scaled Donchian (prior 20-bar) breakout
  pull   — NEW: EMA200-trend pullback: reclaim of EMA20 after a dip
  sqz    — NEW: Bollinger squeeze → band-edge breakout
  daybrk — NEW: prior-server-day range breakout

Run:
    $env:PYTHONPATH='E:\Project\Mt5 Ea'
    python -X utf8 mt5_mcp\research\eurchf\harness.py
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mt5_mcp.backtest import load_frames  # noqa: E402
from mt5_mcp.client import get_client  # noqa: E402
from mt5_mcp.common import pip_size, pip_value_per_lot  # noqa: E402
from mt5_mcp.indicators import atr, rsi  # noqa: E402
from mt5_mcp.strategy import _calc_lots  # noqa: E402

SYMBOL = "EURCHF"
OUT_DIR = Path(__file__).resolve().parent / "results"

RISK_PCT = 0.5
START_BALANCE = 10000.0
WARMUP = 300           # bars skipped before the first evaluable bar
TIMEOUT_BARS = 80      # backtest.py adaptive-timeout default
COMMISSION_PER_LOT = 7.0


# --------------------------------------------------------------------------- #
# indicators (exact copies of multi_strategy helpers so logic matches live)
# --------------------------------------------------------------------------- #
def bbands(close: pd.Series, period: int = 20, std_dev: float = 2.0):
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()          # ddof=1, min_periods=period
    return sma + std_dev * std, sma, sma - std_dev * std


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr_val = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr_val)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr_val)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.rolling(period).mean()


@dataclass
class Prepared:
    tf: str
    t: np.ndarray                  # datetime64[s]
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    bar_spread: np.ndarray         # broker spread per bar, in PRICE units
    atr: np.ndarray
    rsi: np.ndarray
    adx: np.ndarray
    ema20: np.ndarray
    ema50: np.ndarray
    ema200: np.ndarray
    bb_up: np.ndarray
    bb_mid: np.ndarray
    bb_low: np.ndarray
    prior20h: np.ndarray           # max(high[i-20..i-1])
    prior20l: np.ndarray
    squeeze_pct: np.ndarray        # pct of last `win` bars with narrower BB
    prev_day_high: np.ndarray      # prior server-day high/low
    prev_day_low: np.ndarray
    hour: np.ndarray


def prepare(df: pd.DataFrame, point: float) -> Prepared:
    t = df["time"].values.astype("datetime64[s]")
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    bar_spread = df["spread"].to_numpy(float) * point
    bb_up, bb_mid, bb_low = bbands(df["close"], 20, 2.0)
    width = (bb_up - bb_low)
    # squeeze percentile: fraction of the last 100 bars with a narrower band
    squeeze = width.rolling(100).apply(
        lambda x: float(np.sum(x < x[-1]) / len(x)), raw=True
    )
    dser = pd.Series(pd.to_datetime(df["time"]).dt.date)
    dmax = pd.Series(h, index=dser).groupby(level=0).max()
    dmin = pd.Series(l, index=dser).groupby(level=0).min()
    prev_high = dser.map(dmax.shift(1)).to_numpy(float)
    prev_low = dser.map(dmin.shift(1)).to_numpy(float)
    return Prepared(
        tf="H1",
        t=t, o=o, h=h, l=l, c=c,
        bar_spread=bar_spread,
        atr=atr(df, 14).to_numpy(float),
        rsi=rsi(df["close"], 14).to_numpy(float),
        adx=adx(df, 14).to_numpy(float),
        ema20=df["close"].ewm(span=20, adjust=False).mean().to_numpy(float),
        ema50=df["close"].ewm(span=50, adjust=False).mean().to_numpy(float),
        ema200=df["close"].ewm(span=200, adjust=False).mean().to_numpy(float),
        bb_up=bb_up.to_numpy(float), bb_mid=bb_mid.to_numpy(float),
        bb_low=bb_low.to_numpy(float),
        prior20h=df["high"].shift(1).rolling(20).max().to_numpy(float),
        prior20l=df["low"].shift(1).rolling(20).min().to_numpy(float),
        squeeze_pct=squeeze.to_numpy(float),
        prev_day_high=prev_high, prev_day_low=prev_low,
        hour=pd.to_datetime(df["time"]).dt.hour.to_numpy(int),
    )


@dataclass
class Signal:
    action: str                   # BUY / SELL
    sl_dist: float                # price distance from entry
    tp_dist: float
    note: str = ""
    target_price: float | None = None   # absolute TP price (resolved at entry)


# --------------------------------------------------------------------------- #
# signal functions: (P, i, params) -> Signal | None ; only data through bar i
# --------------------------------------------------------------------------- #
def sig_mr(P: Prepared, i: int, q: dict) -> Signal | None:
    """mean_reversion_strategy logic (multi_strategy.py)."""
    r, a, c = P.rsi[i], P.adx[i], P.c[i]
    if np.isnan(r) or np.isnan(a):
        return None
    if a >= q.get("adx_max", 25.0):
        return None
    pip = q["_pip"]
    sl, tp = q.get("sl_pips", 25.0) * pip, q.get("tp_pips", 50.0) * pip
    gate = q.get("validity_gate", True)
    action = None
    if r < q.get("rsi_th", 30.0) and c <= P.bb_low[i] * (1 + q.get("touch", 0.002)):
        action = "BUY"
        conf = min(80.0, (q.get("rsi_th", 30.0) - r) * 2 + 50)
    elif r > 100 - q.get("rsi_th", 30.0) and c >= P.bb_up[i] * (1 - q.get("touch", 0.002)):
        action = "SELL"
        conf = min(80.0, (r - (100 - q.get("rsi_th", 30.0))) * 2 + 50)
    if action is None:
        return None
    if gate and (conf * 0.6 + 24.0) < 60.0:   # trade_score = 0.6*conf + 0.4*zone(60), B3-fixed
        return None
    return Signal(action, sl, tp, f"rsi{r:.0f} adx{a:.0f} conf{conf:.0f}")


def sig_mb(P: Prepared, i: int, q: dict) -> Signal | None:
    """momentum_breakout INTENDED logic (prior 20-bar Donchian break)."""
    a = P.adx[i]
    if np.isnan(a) or a < q.get("adx_min", 25.0):
        return None
    ph, pl = P.prior20h[i], P.prior20l[i]
    if np.isnan(ph) or np.isnan(pl):
        return None
    pip = q["_pip"]
    sl, tp = q.get("sl_pips", 30.0) * pip, q.get("tp_pips", 80.0) * pip
    gate = q.get("validity_gate", True)
    action = None
    if P.c[i] > ph:
        action = "BUY"
    elif P.c[i] < pl:
        action = "SELL"
    if action is None:
        return None
    if gate:
        conf = min(85.0, a * 0.8 + 30.0)
        if (conf * 0.5 + 29.5) < 60.0:        # trade_score >= 60 as written
            return None
    return Signal(action, sl, tp, f"adx{a:.0f}")


def sig_emac(P: Prepared, i: int, q: dict) -> Signal | None:
    """ema_crossover_strategy logic (multi_strategy.py)."""
    a = P.adx[i]
    at = P.atr[i]
    if np.isnan(a) or np.isnan(at) or at <= 0 or a < q.get("adx_min", 20.0):
        return None
    f_now, s_now = P.ema20[i], P.ema50[i]
    f_prev, s_prev = P.ema20[i - 1], P.ema50[i - 1]
    up = f_prev <= s_prev and f_now > s_now
    down = f_prev >= s_prev and f_now < s_now
    if not (up or down):
        return None
    conf = min(80.0, 50 + a * 0.5)
    if q.get("validity_gate", True) and (conf * 0.5 + 29.5) < 60.0:
        return None
    action = "BUY" if up else "SELL"
    return Signal(action, q.get("sl_atr", 2.0) * at, q.get("tp_atr", 4.0) * at,
                  f"adx{a:.0f}")


def sig_bbmr2(P: Prepared, i: int, q: dict) -> Signal | None:
    """Bollinger reversion — fade a penetration of the band, target mean.

    Params:
      z        — entry threshold in band std-units (2.0 = band touch, >2 deeper)
      target   — "mid" (absolute BB middle price) or "atr" (tp_atr * ATR)
      rsi_th   — RSI extreme threshold (symmetric 100-rsi_th)
      adx_max  — optional ranging gate
      sl_atr   — stop distance in ATR(14)
    """
    r, a, c = P.rsi[i], P.adx[i], P.c[i]
    if np.isnan(r) or np.isnan(a) or np.isnan(P.bb_mid[i]):
        return None
    if a > q.get("adx_max", 1e9):
        return None
    at = P.atr[i]
    if np.isnan(at) or at <= 0:
        return None
    th = q.get("rsi_th", 30.0)
    z = q.get("z", 2.0)
    std_est = (P.bb_mid[i] - P.bb_low[i]) / 2.0   # band = mid ± 2 std
    lo_thr = P.bb_mid[i] - z * std_est
    hi_thr = P.bb_mid[i] + z * std_est
    target = None
    if r < th and c < lo_thr:
        action = "BUY"
        target = P.bb_mid[i]
    elif r > 100 - th and c > hi_thr:
        action = "SELL"
        target = P.bb_mid[i]
    else:
        return None
    if q.get("target", "mid") == "atr":
        return Signal(action, q.get("sl_atr", 1.5) * at,
                      q.get("tp_atr", 2.0) * at, f"rsi{r:.0f}")
    sig = Signal(action, q.get("sl_atr", 1.5) * at, 0.0, f"rsi{r:.0f}")
    sig.target_price = target   # engine converts to a distance at entry
    return sig


def sig_donch(P: Prepared, i: int, q: dict) -> Signal | None:
    """NEW: ATR-scaled Donchian breakout (prior N-bar high/low)."""
    a, at = P.adx[i], P.atr[i]
    if np.isnan(at) or at <= 0:
        return None
    lb = int(q.get("lookback", 20))
    ph = P.prior20h[i] if lb == 20 else np.nan
    pl = P.prior20l[i] if lb == 20 else np.nan
    if lb != 20:
        return None
    if np.isnan(ph) or np.isnan(pl):
        return None
    adx_min = q.get("adx_min", None)
    if adx_min is not None and (np.isnan(a) or a < adx_min):
        return None
    if P.c[i] > ph:
        action = "BUY"
    elif P.c[i] < pl:
        action = "SELL"
    else:
        return None
    return Signal(action, q.get("sl_atr", 1.5) * at, q.get("tp_atr", 3.0) * at,
                  f"adx{a:.0f}" if not np.isnan(a) else "")


def sig_pull(P: Prepared, i: int, q: dict) -> Signal | None:
    """NEW: EMA200-trend pullback — reclaim of EMA20 after a dip below it."""
    at = P.atr[i]
    if np.isnan(at) or at <= 0 or np.isnan(P.ema200[i]):
        return None
    if i < 5:
        return None
    e50, e200, e20 = P.ema50[i], P.ema200[i], P.ema20[i]
    if e50 > e200 and P.c[i - 1] < P.ema20[i - 1] and P.c[i] > e20 and P.c[i] > P.c[i - 1]:
        action = "BUY"
    elif e50 < e200 and P.c[i - 1] > P.ema20[i - 1] and P.c[i] < e20 and P.c[i] < P.c[i - 1]:
        action = "SELL"
    else:
        return None
    return Signal(action, q.get("sl_atr", 1.5) * at, q.get("tp_atr", 3.0) * at, "")


def sig_sqz(P: Prepared, i: int, q: dict) -> Signal | None:
    """NEW: Bollinger squeeze → breakout through the band edge."""
    pct, at = P.squeeze_pct[i], P.atr[i]
    if np.isnan(pct) or np.isnan(at) or at <= 0:
        return None
    th = q.get("pct_th", 0.25)
    # squeeze on the PREVIOUS bar, price breaks the CURRENT band edge
    prev_pct = P.squeeze_pct[i - 1] if i > 0 else np.nan
    if np.isnan(prev_pct) or prev_pct > th:
        return None
    if P.c[i] > P.bb_up[i]:
        action = "BUY"
    elif P.c[i] < P.bb_low[i]:
        action = "SELL"
    else:
        return None
    return Signal(action, q.get("sl_atr", 1.5) * at, q.get("tp_atr", 3.0) * at,
                  f"pct{prev_pct:.2f}")


def sig_daybrk(P: Prepared, i: int, q: dict) -> Signal | None:
    """NEW: prior-server-day range breakout."""
    at, ph, pl = P.atr[i], P.prev_day_high[i], P.prev_day_low[i]
    if np.isnan(at) or at <= 0 or np.isnan(ph) or np.isnan(pl):
        return None
    h0, h1 = q.get("hour_from", 0), q.get("hour_to", 23)
    if not (h0 <= P.hour[i] <= h1):
        return None
    if P.c[i] > ph:
        action = "BUY"
    elif P.c[i] < pl:
        action = "SELL"
    else:
        return None
    return Signal(action, q.get("sl_atr", 1.5) * at, q.get("tp_atr", 2.5) * at, "")


def sig_btct(P: Prepared, i: int, q: dict) -> Signal | None:
    """btc_trend_following_strategy logic (multi_strategy.py), run on H4.

    EMA50/200 trend alignment + RSI(14) pullback completion (cross >=40 for
    BUY, <=60 for SELL); SL 2.5xATR / TP 4xATR; live validity gate 0.6*conf+24 >= 60.
    """
    if i < 1:
        return None
    at, r_now, r_prev = P.atr[i], P.rsi[i], P.rsi[i - 1]
    if np.isnan(at) or at <= 0 or np.isnan(r_now) or np.isnan(r_prev):
        return None
    f, s = P.ema50[i], P.ema200[i]
    if np.isnan(f) or np.isnan(s):
        return None
    action, conf = None, 0.0
    if f > s and r_prev < 40.0 and r_now >= 40.0:
        action = "BUY"
        conf = min(80.0, 50 + (r_now - 40.0) * 1.5)
    elif f < s and r_prev > 60.0 and r_now <= 60.0:
        action = "SELL"
        conf = min(80.0, 50 + (60.0 - r_now) * 1.5)
    if action is None:
        return None
    if q.get("validity_gate", True) and (conf * 0.6 + 24.0) < 60.0:
        return None
    return Signal(action, q.get("sl_atr", 2.5) * at, q.get("tp_atr", 4.0) * at,
                  f"rsi{r_now:.0f}")


SIGNALS: dict[str, Callable[[Prepared, int, dict], Signal | None]] = {
    "mr": sig_mr,
    "mb": sig_mb,
    "emac": sig_emac,
    "bbmr": sig_bbmr2,
    "donch": sig_donch,
    "pull": sig_pull,
    "sqz": sig_sqz,
    "daybrk": sig_daybrk,
    "btct": sig_btct,
}


# --------------------------------------------------------------------------- #
# engine
# --------------------------------------------------------------------------- #
@dataclass
class CostModel:
    name: str
    use_bar_spread: bool          # False -> canonical fixed spread_points * pip
    spread_points: float = 15.0   # canonical only
    slippage_price: float = 0.0   # per side, price units
    vol_spread: bool = False      # canonical vol-adjusted spread multiplier


@dataclass
class Variant:
    name: str
    sig: str
    params: dict = field(default_factory=dict)


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    action: str
    entry: float
    sl: float
    tp: float
    lots: float
    pnl: float
    reason: str
    bars_held: int
    note: str
    r_multiple: float


def run_variant(
    P: Prepared,
    variant: Variant,
    cost: CostModel,
    info: dict,
    pip: float,
    pip_value: float,
    start: int = WARMUP,
    end: int | None = None,
) -> dict[str, Any]:
    q = dict(variant.params)
    q["_pip"] = pip
    sig_fn = SIGNALS[variant.sig]
    n = len(P.t)
    if end is None:
        end = n - 1

    # engine-level filters (all optional, default off)
    hour_from = int(q.get("hour_from", 0))
    hour_to = int(q.get("hour_to", 23))
    rej = bool(q.get("rej", False))
    range_ratio_max = q.get("range_ratio_max", None)
    be_r = q.get("be_r", None)

    equity = START_BALANCE
    peak = equity
    max_dd = 0.0
    trades: list[Trade] = []
    i = start
    while i < end:
        if not (hour_from <= P.hour[i] <= hour_to):
            i += 1
            continue
        if range_ratio_max is not None:
            pr = P.prev_day_high[i] - P.prev_day_low[i]
            at = P.atr[i]
            if np.isnan(pr) or np.isnan(at) or at <= 0:
                i += 1
                continue
            # quiet-day gate: prior day range vs random-walk expectation
            if pr / (at * 4.9) > float(range_ratio_max):
                i += 1
                continue
        sig = sig_fn(P, i, q)
        if sig is None:
            i += 1
            continue
        if rej:
            if (sig.action == "BUY" and P.c[i] <= P.o[i]) or (
                sig.action == "SELL" and P.c[i] >= P.o[i]
            ):
                i += 1
                continue
        if i + 1 >= n:
            break
        # --- entry at next open -------------------------------------------
        raw_spread = (
            P.bar_spread[i + 1]
            if cost.use_bar_spread
            else cost.spread_points * pip
        )
        if cost.vol_spread:
            w = P.atr[max(0, i - 200): i + 1]
            w = w[~np.isnan(w)]
            if len(w) >= 20:
                atr_now = P.atr[i]
                avg = float(np.mean(w))
                ratio = atr_now / max(avg, 1e-12)
                mult = 1.0 + min(max(ratio - 1.0, 0.0), 2.0)
                raw_spread *= mult
        slippage = cost.slippage_price
        if sig.action == "BUY":
            entry = float(P.o[i + 1]) + raw_spread + slippage
        else:
            entry = float(P.o[i + 1]) - slippage
        # sl/tp distances; absolute targets resolve against the entry price
        sl_dist = sig.sl_dist
        if sig.target_price is not None:
            target = sig.target_price
            tp_dist = (target - entry) if sig.action == "BUY" else (entry - target)
            if tp_dist <= 0:
                i += 1
                continue
        else:
            tp_dist = sig.tp_dist
        sl = entry - sl_dist if sig.action == "BUY" else entry + sl_dist
        tp = entry + tp_dist if sig.action == "BUY" else entry - tp_dist
        sl_pips = sl_dist / pip
        lots, _, _ = _calc_lots(SYMBOL, equity, RISK_PCT, sl_pips, pip_value, info)
        if lots is None or lots <= 0:
            i += 1
            continue

        # --- exits ---------------------------------------------------------
        exit_price = None
        reason = "timeout"
        exit_idx = i + 1
        be_active = False
        for j in range(i + 1, min(n, i + 1 + TIMEOUT_BARS)):
            if sig.action == "BUY":
                hit_sl = P.l[j] <= sl
                hit_tp = P.h[j] >= tp
            else:
                hit_sl = P.h[j] >= sl
                hit_tp = P.l[j] <= tp
            if hit_sl:
                exit_price, reason, exit_idx = sl, "SL", j
                break
            if hit_tp:
                exit_price, reason, exit_idx = tp, "TP", j
                break
            # breakeven: after +be_r R, park SL at entry (checked after SL/TP,
            # mirroring backtest.py's trailing-stop ordering)
            if be_r is not None and not be_active:
                if sig.action == "BUY":
                    if (P.h[j] - entry) >= float(be_r) * sl_dist:
                        sl = max(sl, entry)
                        be_active = True
                else:
                    if (entry - P.l[j]) >= float(be_r) * sl_dist:
                        sl = min(sl, entry)
                        be_active = True
            exit_price, exit_idx = float(P.c[j]), j
        else:
            # loop exhausted without break: timeout at last examined bar
            exit_idx = min(n - 1, i + TIMEOUT_BARS)
            exit_price = float(P.c[exit_idx])
        if exit_price is None:
            i += 1
            continue

        # --- pnl (mirrors backtest.py _pnl) --------------------------------
        per_point = pip_value / pip
        if sig.action == "BUY":
            raw = (exit_price - slippage - entry) * per_point * lots
        else:
            exit_used = exit_price + raw_spread + slippage
            raw = (entry - exit_used) * per_point * lots
        pnl = raw - COMMISSION_PER_LOT * lots

        r_val = sl_dist if sl_dist > 0 else 1e-12
        r_mult = ((exit_price - entry) if sig.action == "BUY" else (entry - exit_price)) / r_val

        trades.append(
            Trade(
                entry_time=str(P.t[i + 1]), exit_time=str(P.t[exit_idx]),
                action=sig.action, entry=round(entry, 6), sl=round(sl, 6),
                tp=round(tp, 6), lots=round(lots, 2), pnl=round(pnl, 2),
                reason=reason, bars_held=exit_idx - i, note=sig.note,
                r_multiple=round(r_mult, 3),
            )
        )
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100.0 if peak else 0.0)
        i = exit_idx + 1

    return _stats(trades, equity, max_dd, start, end, P)


def _stats(trades: list[Trade], equity: float, max_dd: float,
           start: int, end: int, P: Prepared) -> dict[str, Any]:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gp = sum(t.pnl for t in wins)
    gl = abs(sum(t.pnl for t in losses))
    mid = (start + end) // 2
    mid_time = P.t[mid] if mid < len(P.t) else None
    h1 = [t for t in trades if np.datetime64(t.entry_time, "s") < mid_time] if mid_time is not None else []
    h2 = [t for t in trades if np.datetime64(t.entry_time, "s") >= mid_time] if mid_time is not None else []

    def _half(xs: list[Trade]) -> dict[str, Any]:
        w = [t for t in xs if t.pnl > 0]
        g1 = sum(t.pnl for t in w)
        g2 = abs(sum(t.pnl for t in xs if t.pnl <= 0))
        return {
            "trades": len(xs),
            "win_rate": round(len(w) / len(xs) * 100, 1) if xs else 0.0,
            "pf": round(g1 / g2, 2) if g2 else (999.0 if g1 > 0 else 0.0),
            "net": round(sum(t.pnl for t in xs), 2),
        }

    return {
        "trades": len(trades),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "pf": round(gp / gl, 3) if gl else (999.0 if gp > 0 else 0.0),
        "net_pnl": round(sum(t.pnl for t in trades), 2),
        "expectancy": round(sum(t.pnl for t in trades) / len(trades), 2) if trades else 0.0,
        "max_dd_pct": round(max_dd, 2),
        "avg_r": round(np.mean([t.r_multiple for t in trades]), 2) if trades else 0.0,
        "avg_bars_held": round(np.mean([t.bars_held for t in trades]), 1) if trades else 0,
        "final_equity": round(equity, 2),
        "tp_hits": sum(1 for t in trades if t.reason == "TP"),
        "sl_hits": sum(1 for t in trades if t.reason == "SL"),
        "timeouts": sum(1 for t in trades if t.reason == "timeout"),
        "half1": _half(h1),
        "half2": _half(h2),
        "window": [str(P.t[start]), str(P.t[min(end, len(P.t) - 1)])],
        "trade_list": [asdict(t) for t in trades],
    }


# --------------------------------------------------------------------------- #
# variant grid
# --------------------------------------------------------------------------- #
def build_variants() -> list[Variant]:
    V: list[Variant] = []
    # --- carried-over strategies (multi_strategy.py logic) ---
    V.append(Variant("mr_aswritten", "mr",
                     {"validity_gate": True}))                  # RSI<30 gate→ effectively RSI<=18
    V.append(Variant("mr_signal_only", "mr",
                     {"validity_gate": False}))
    V.append(Variant("mr_looser", "mr",
                     {"validity_gate": False, "rsi_th": 35.0, "adx_max": 30.0}))
    V.append(Variant("mb_intended", "mb", {"validity_gate": False}))
    V.append(Variant("mb_intended_gated", "mb", {"validity_gate": True}))
    V.append(Variant("emac_aswritten", "emac", {"validity_gate": True}))
    V.append(Variant("emac_nogate", "emac", {"validity_gate": False, "adx_min": 20.0}))
    # --- new ideas ---
    V.append(Variant("bbmr_base", "bbmr",
                     {"rsi_th": 30.0, "adx_max": 25.0, "sl_atr": 1.5}))
    V.append(Variant("bbmr_noadx", "bbmr",
                     {"rsi_th": 30.0, "sl_atr": 1.5}))
    V.append(Variant("bbmr_rsi35", "bbmr",
                     {"rsi_th": 35.0, "adx_max": 30.0, "sl_atr": 1.5}))
    V.append(Variant("donch_base", "donch",
                     {"adx_min": None, "sl_atr": 1.5, "tp_atr": 3.0}))
    V.append(Variant("donch_adx20", "donch",
                     {"adx_min": 20.0, "sl_atr": 1.5, "tp_atr": 3.0}))
    V.append(Variant("pull_base", "pull", {"sl_atr": 1.5, "tp_atr": 3.0}))
    V.append(Variant("sqz_base", "sqz", {"pct_th": 0.25, "sl_atr": 1.5, "tp_atr": 3.0}))
    V.append(Variant("daybrk_base", "daybrk",
                     {"sl_atr": 1.5, "tp_atr": 2.5, "hour_from": 0, "hour_to": 23}))
    # --- round 2: parameter grids on the near-miss families ---------------- #
    # bbmr: stop width, band depth, rejection candle, session, quiet regime
    V.append(Variant("bbmr_z2_sl10", "bbmr",
                     {"z": 2.0, "rsi_th": 30.0, "sl_atr": 1.0}))
    V.append(Variant("bbmr_z2_sl20", "bbmr",
                     {"z": 2.0, "rsi_th": 30.0, "sl_atr": 2.0}))
    V.append(Variant("bbmr_z25_sl15", "bbmr",
                     {"z": 2.5, "rsi_th": 30.0, "sl_atr": 1.5}))
    V.append(Variant("bbmr_z30_sl15", "bbmr",
                     {"z": 3.0, "rsi_th": 30.0, "sl_atr": 1.5}))
    V.append(Variant("bbmr_rej", "bbmr",
                     {"z": 2.0, "rsi_th": 30.0, "sl_atr": 1.5, "rej": True}))
    V.append(Variant("bbmr_rej_z25", "bbmr",
                     {"z": 2.5, "rsi_th": 30.0, "sl_atr": 1.5, "rej": True}))
    V.append(Variant("bbmr_asia_h0-9", "bbmr",
                     {"z": 2.0, "rsi_th": 30.0, "sl_atr": 1.5,
                      "hour_from": 0, "hour_to": 9}))
    V.append(Variant("bbmr_eu_h10-16", "bbmr",
                     {"z": 2.0, "rsi_th": 30.0, "sl_atr": 1.5,
                      "hour_from": 10, "hour_to": 16}))
    V.append(Variant("bbmr_us_h17-23", "bbmr",
                     {"z": 2.0, "rsi_th": 30.0, "sl_atr": 1.5,
                      "hour_from": 17, "hour_to": 23}))
    V.append(Variant("bbmr_quiet", "bbmr",
                     {"z": 2.0, "rsi_th": 30.0, "sl_atr": 1.5,
                      "range_ratio_max": 0.8}))
    V.append(Variant("bbmr_quiet_rej", "bbmr",
                     {"z": 2.0, "rsi_th": 30.0, "sl_atr": 1.5,
                      "range_ratio_max": 0.8, "rej": True}))
    V.append(Variant("bbmr_atrTP", "bbmr",
                     {"z": 2.0, "rsi_th": 30.0, "sl_atr": 1.5,
                      "target": "atr", "tp_atr": 2.0}))
    # mr: tighter fixed pip targets for EURCHF's 7-pip H1 ATR
    V.append(Variant("mr_tight15_30", "mr",
                     {"validity_gate": False, "sl_pips": 15.0, "tp_pips": 30.0}))
    V.append(Variant("mr_tight15_30_rsi35", "mr",
                     {"validity_gate": False, "sl_pips": 15.0, "tp_pips": 30.0,
                      "rsi_th": 35.0, "adx_max": 30.0}))
    # emac: tighter RR grid + breakeven
    V.append(Variant("emac_sl15tp2", "emac",
                     {"validity_gate": False, "sl_atr": 1.5, "tp_atr": 2.0}))
    V.append(Variant("emac_sl15tp3", "emac",
                     {"validity_gate": False, "sl_atr": 1.5, "tp_atr": 3.0}))
    V.append(Variant("emac_sl2tp4_be", "emac",
                     {"validity_gate": False, "sl_atr": 2.0, "tp_atr": 4.0,
                      "be_r": 1.0}))
    V.append(Variant("emac_sl15tp3_be", "emac",
                     {"validity_gate": False, "sl_atr": 1.5, "tp_atr": 3.0,
                      "be_r": 1.0}))
    # donch / sqz with breakeven (rescue attempts)
    V.append(Variant("donch_be", "donch",
                     {"sl_atr": 1.5, "tp_atr": 3.0, "be_r": 1.0}))
    V.append(Variant("donch_adx25_be", "donch",
                     {"adx_min": 25.0, "sl_atr": 1.5, "tp_atr": 3.0,
                      "be_r": 1.0}))
    return V


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    only = None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1].split(",")

    t0 = time.perf_counter()
    client = get_client()
    client.ensure_connected()
    info = client.symbol_info(SYMBOL)
    pip = pip_size(info)
    pip_value = pip_value_per_lot(info)
    frames = load_frames(SYMBOL, ["H1"], 6000)
    if "H1" not in frames:
        print("FATAL: no H1 data")
        return 1
    df = frames["H1"].reset_index(drop=True)
    P = prepare(df, float(info.get("point", 1e-5)))
    print(f"data: {len(df)} H1 bars {df['time'].iloc[0]} .. {df['time'].iloc[-1]} "
          f"(warmup {WARMUP} -> {len(df) - WARMUP - 1} evaluable bars) "
          f"[{time.perf_counter() - t0:.1f}s]")

    costs = [
        CostModel("canonical", use_bar_spread=False, spread_points=15.0,
                  slippage_price=1.0 * pip, vol_spread=True),
        CostModel("realistic", use_bar_spread=True,
                  slippage_price=1.0 * float(info.get("point", 1e-5)),
                  vol_spread=False),
    ]

    variants = build_variants()
    if only:
        variants = [v for v in variants if v.name in only]

    results: dict[str, Any] = {
        "symbol": SYMBOL,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bars": len(df),
        "warmup": WARMUP,
        "timeout_bars": TIMEOUT_BARS,
        "risk_pct": RISK_PCT,
        "start_balance": START_BALANCE,
        "variants": {},
    }

    hdr = (f"{'variant':<24} {'cost':<10} {'tr':>4} {'win%':>5} {'PF':>7} "
           f"{'net$':>9} {'exp$':>7} {'DD%':>6} {'h1 PF':>6} {'h2 PF':>6} "
           f"{'TP/SL/TO':>10}")
    print(hdr)
    print("-" * len(hdr))
    for v in variants:
        for cost in costs:
            r = run_variant(P, v, cost, info, pip, pip_value)
            key = f"{v.name}|{cost.name}"
            results["variants"][key] = {
                "variant": asdict(v), "cost": cost.name, **r,
            }
            print(f"{v.name:<24} {cost.name:<10} {r['trades']:>4} "
                  f"{r['win_rate']:>5.1f} {r['pf']:>7.2f} {r['net_pnl']:>9.2f} "
                  f"{r['expectancy']:>7.2f} {r['max_dd_pct']:>6.2f} "
                  f"{r['half1']['pf']:>6.2f} {r['half2']['pf']:>6.2f} "
                  f"{r['tp_hits']:>3}/{r['sl_hits']:<3}/{r['timeouts']:<3}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"eurchf_results_{time.strftime('%Y-%m-%d')}.json"
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out}  [{time.perf_counter() - t0:.1f}s total]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

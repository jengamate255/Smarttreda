"""Position sizing methods for the forex RL trading system.

All functions are pure (no side-effects) and return a lot size as a float.
The caller is responsible for clamping to the broker's allowed lot step.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np


# ── Fixed Fractional ────────────────────────────────────────────────────


def fixed_fractional(
    balance: float,
    risk_pct: float,
    sl_distance: float,
    tick_value: float,
) -> float:
    """Risk a fixed percentage of balance per trade.

    Parameters
    ----------
    balance : Current account balance in account currency.
    risk_pct : Fraction of balance to risk (e.g. 0.005 for 0.5 %).
    sl_distance : Distance from entry to stop-loss in price units.
    tick_value : Monetary value of one tick (or pip) movement for one lot.

    Returns
    -------
    Number of lots to trade.  Returns 0.0 when inputs are invalid.

    Formula
    -------
    risk_amount = balance × risk_pct
    lots = risk_amount / (sl_distance × tick_value_per_lot)
    """
    if balance <= 0 or risk_pct <= 0 or sl_distance <= 0 or tick_value <= 0:
        return 0.0

    risk_amount = balance * risk_pct
    lots = risk_amount / (sl_distance * tick_value)

    # Clamp to sane bounds
    lots = max(lots, 0.0)
    lots = min(lots, 100.0)  # hard ceiling — caller should override

    return round(lots, 2)


# ── Kelly Criterion ─────────────────────────────────────────────────────


def kelly_criterion(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    fraction: float = 0.5,
) -> float:
    """Kelly-optimal fraction of bankroll to risk.

    Parameters
    ----------
    win_rate : Historical win probability (0–1).
    avg_win : Average winning trade size (positive).
    avg_loss : Average losing trade size (positive, i.e. |loss|).
    fraction : Kelly fraction scaling (0.5 = half-Kelly, the conservative
               default used by most practitioners).

    Returns
    -------
    Fraction of bankroll to risk, clamped to [0, 0.25].

    Formula
    -------
    f* = (p × b − q) / b
    where p = win_rate, q = 1 − p, b = avg_win / avg_loss
    """
    if win_rate <= 0 or win_rate >= 1 or avg_win <= 0 or avg_loss <= 0:
        return 0.0

    p = win_rate
    q = 1.0 - p
    b = avg_win / avg_loss

    kelly_full = (p * b - q) / b

    # Scale down and clamp
    kelly_scaled = kelly_full * fraction
    kelly_scaled = max(kelly_scaled, 0.0)
    kelly_scaled = min(kelly_scaled, 0.25)  # never risk more than 25 %

    return round(kelly_scaled, 4)


# ── Anti-Martingale ─────────────────────────────────────────────────────


def anti_martingale(
    balance: float,
    risk_pct: float,
    consecutive_wins: int,
    max_scale: int = 4,
) -> float:
    """Increase position size after consecutive wins, decrease after losses.

    Parameters
    ----------
    balance : Current account balance.
    risk_pct : Base risk percentage per trade (e.g. 0.005).
    consecutive_wins : Number of consecutive winning trades.
    max_scale : Maximum multiplier applied to risk_pct.

    Returns
    -------
    Scaled risk amount in account currency (not lots — caller must divide
    by SL distance × tick_value).

    Formula
    -------
    scale = min(consecutive_wins + 1, max_scale)
    risk_amount = balance × risk_pct × scale
    """
    if balance <= 0 or risk_pct <= 0:
        return 0.0

    scale = min(max(consecutive_wins + 1, 1), max_scale)
    risk_amount = balance * risk_pct * scale
    return round(risk_amount, 2)


# ── Volatility-Adjusted ─────────────────────────────────────────────────


def volatility_adjusted(
    atr: float,
    target_vol: float,
    current_vol: float,
    base_lots: float = 1.0,
) -> float:
    """Scale position size inversely with realised volatility.

    When volatility is above the target, reduce size; when below, increase.
    This keeps the portfolio's risk contribution roughly constant.

    Parameters
    ----------
    atr : Current Average True Range value.
    target_vol : Desired volatility level (e.g. rolling median ATR).
    current_vol : Same as *atr* in most usages — provided for symmetry.
    base_lots : Baseline lot size when current_vol == target_vol.

    Returns
    -------
    Adjusted lot size, clamped to [0.01, 100].

    Formula
    -------
    lots = base_lots × (target_vol / current_vol)
    """
    if atr <= 0 or target_vol <= 0 or current_vol <= 0 or base_lots <= 0:
        return 0.0

    ratio = target_vol / current_vol
    lots = base_lots * ratio

    # Clamp to reasonable bounds
    lots = max(lots, 0.01)
    lots = min(lots, 100.0)

    return round(lots, 2)


# ── ATR-based helper (bonus) ────────────────────────────────────────────


def atr_position_size(
    balance: float,
    risk_pct: float,
    atr: float,
    atr_multiplier: float,
    tick_value: float,
) -> float:
    """Compute lot size using ATR as the SL distance proxy.

    Convenience wrapper around ``fixed_fractional`` where the stop-loss
    distance is set to ATR × multiplier.

    Parameters
    ----------
    balance : Account balance.
    risk_pct : Risk fraction per trade.
    atr : Current ATR value.
    atr_multiplier : How many ATR units for the SL (typical: 1.5–2.0).
    tick_value : Monetary value per tick per lot.

    Returns
    -------
    Lot size.
    """
    sl_distance = atr * atr_multiplier
    return fixed_fractional(balance, risk_pct, sl_distance, tick_value)

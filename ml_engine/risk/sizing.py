"""Position sizing: fixed-fractional and probability-weighted, always capped.

ML never controls unrestricted size. Sizing maps (probability, expected R)
-> risk fraction within [min_risk, max_risk]; the cap is absolute.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SizingPolicy:
    base_risk_percent: float = 0.5
    max_risk_percent: float = 1.0
    min_risk_percent: float = 0.1
    # Probability weighting: risk scales linearly between p_lo (min risk)
    # and p_hi (max risk).
    p_lo: float = 0.5
    p_hi: float = 0.7


def size_for_probability(p: float, policy: SizingPolicy | None = None) -> float:
    """Risk percent for a calibrated probability, within hard caps."""
    pol = policy or SizingPolicy()
    if not np.isfinite(p):
        return pol.min_risk_percent
    if p <= pol.p_lo:
        frac = 0.0
    elif p >= pol.p_hi:
        frac = 1.0
    else:
        frac = (p - pol.p_lo) / (pol.p_hi - pol.p_lo)
    risk = pol.min_risk_percent + frac * (pol.max_risk_percent - pol.min_risk_percent)
    return float(min(pol.max_risk_percent, max(pol.min_risk_percent, risk)))


def lots_for_risk(
    equity: float,
    risk_percent: float,
    stop_distance_price: float,
    pip_value_per_lot: float = 10.0,
    pip_size: float = 0.0001,
    min_lot: float = 0.01,
    max_lot: float = 100.0,
) -> float:
    """Lots such that a stop-out loses equity * risk%."""
    if stop_distance_price <= 0:
        return min_lot
    risk_amount = equity * risk_percent / 100.0
    stop_pips = stop_distance_price / pip_size
    if stop_pips <= 0 or pip_value_per_lot <= 0:
        return min_lot
    lots = risk_amount / (stop_pips * pip_value_per_lot)
    lots = min(max_lot, max(min_lot, lots))
    # Round down to 0.01 steps to never exceed intended risk
    return float(np.floor(lots * 100) / 100.0)

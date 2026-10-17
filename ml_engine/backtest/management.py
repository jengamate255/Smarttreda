"""Trade management simulator: hold-to-target vs trailing-stop comparison.

Runs from the bar AFTER entry to exit, tracking equity under different
management rules. Used to evaluate whether a setup's MFE was capturable
in practice, and to compare management strategies.

Contract M1 (see architecture.md): simulation runs on future bars only,
causally — no look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ml_engine.setups.composer import Setup


@dataclass
class ManagementResult:
    """Outcome of one management rule applied to one setup."""

    setup_id: str
    rule: str                 # "hold_to_target" | "trailing_atr" | "partial_tp" | "time_exit"
    r_multiple: float
    exit_bar: int
    exit_price: float
    max_favorable_r: float
    max_adverse_r: float
    bars_held: int

    def to_dict(self) -> dict:
        return {
            "setup_id": self.setup_id,
            "rule": self.rule,
            "r_multiple": self.r_multiple,
            "exit_bar": self.exit_bar,
            "exit_price": self.exit_price,
            "max_favorable_r": self.max_favorable_r,
            "max_adverse_r": self.max_adverse_r,
            "bars_held": self.bars_held,
        }


def _trailing_atr_stop(
    high: np.ndarray,
    low: np.ndarray,
    entry: float,
    stop: float,
    direction: str,
    trail_atr: float,
    atr: np.ndarray,
    max_hold: int,
    start_bar: int,
) -> tuple[float, int, float]:
    """Simulate a trailing ATR stop. Returns (r_multiple, exit_bar, exit_price)."""
    n = len(high)
    risk = abs(entry - stop)
    if risk <= 0:
        return -1.0, start_bar, entry

    long = direction == "long"
    current_stop = stop
    best = entry

    for b in range(start_bar, min(n, start_bar + max_hold)):
        h, l = high[b], low[b]
        a = atr[b] if np.isfinite(atr[b]) else 0.0

        if long:
            best = max(best, h)
            # Trail stop up
            if a > 0:
                new_stop = best - trail_atr * a
                current_stop = max(current_stop, new_stop)
            # Check stop hit
            if l <= current_stop:
                exit_price = current_stop
                r = (exit_price - entry) / risk
                return float(r), b, float(exit_price)
        else:
            best = min(best, l)
            if a > 0:
                new_stop = best + trail_atr * a
                current_stop = min(current_stop, new_stop)
            if h >= current_stop:
                exit_price = current_stop
                r = (entry - exit_price) / risk
                return float(r), b, float(exit_price)

    # Timeout: exit at last close
    exit_price = high[min(n - 1, start_bar + max_hold - 1)] if long else low[min(n - 1, start_bar + max_hold - 1)]
    r = ((exit_price - entry) / risk) if long else ((entry - exit_price) / risk)
    return float(r), min(n - 1, start_bar + max_hold - 1), float(exit_price)


def _hold_to_target(
    high: np.ndarray,
    low: np.ndarray,
    entry: float,
    stop: float,
    target: float,
    direction: str,
    max_hold: int,
    start_bar: int,
) -> tuple[float, int, float]:
    """Hold until TP or SL is hit, or timeout."""
    n = len(high)
    risk = abs(entry - stop)
    if risk <= 0:
        return -1.0, start_bar, entry

    long = direction == "long"

    for b in range(start_bar, min(n, start_bar + max_hold)):
        h, l = high[b], low[b]
        hit_sl = (l <= stop) if long else (h >= stop)
        hit_tp = (h >= target) if long else (l <= target)

        if hit_sl and hit_tp:
            return -1.0, b, stop
        if hit_sl:
            return -1.0, b, stop
        if hit_tp:
            r = abs(target - entry) / risk
            return float(r), b, target

    # Timeout
    exit_price = low[min(n - 1, start_bar + max_hold - 1)] if long else high[min(n - 1, start_bar + max_hold - 1)]
    r = ((exit_price - entry) / risk) if long else ((entry - exit_price) / risk)
    return float(r), min(n - 1, start_bar + max_hold - 1), float(exit_price)


def simulate_management(
    setup: Setup,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray,
    max_hold: int = 50,
    trail_atr: float = 2.0,
) -> list[ManagementResult]:
    """Run multiple management rules on one setup's future bars.

    Returns one ManagementResult per rule for comparison.
    """
    start = setup.index + 1
    n = len(close)
    if start >= n:
        return []

    risk = abs(setup.entry - setup.stop)
    if risk <= 0:
        return []

    results = []

    # Rule 1: hold to target
    r, exit_b, exit_p = _hold_to_target(
        high, low, setup.entry, setup.stop, setup.target,
        setup.direction, max_hold, start,
    )
    mfe = _mfe_r(high, low, setup.entry, setup.direction, risk, start, min(n, start + max_hold))
    mae = _mae_r(high, low, setup.entry, setup.direction, risk, start, min(n, start + max_hold))
    results.append(ManagementResult(
        setup_id=setup.setup_id, rule="hold_to_target",
        r_multiple=r, exit_bar=exit_b, exit_price=exit_p,
        max_favorable_r=mfe, max_adverse_r=mae,
        bars_held=exit_b - setup.index,
    ))

    # Rule 2: trailing ATR stop
    r2, exit_b2, exit_p2 = _trailing_atr_stop(
        high, low, setup.entry, setup.stop, setup.direction,
        trail_atr, atr, max_hold, start,
    )
    results.append(ManagementResult(
        setup_id=setup.setup_id, rule=f"trailing_{trail_atr}atr",
        r_multiple=r2, exit_bar=exit_b2, exit_price=exit_p2,
        max_favorable_r=mfe, max_adverse_r=mae,
        bars_held=exit_b2 - setup.index,
    ))

    # Rule 3: time exit (max_hold)
    b = min(n - 1, start + max_hold - 1)
    if setup.direction == "long":
        time_r = (close[b] - setup.entry) / risk
    else:
        time_r = (setup.entry - close[b]) / risk
    results.append(ManagementResult(
        setup_id=setup.setup_id, rule=f"time_{max_hold}bars",
        r_multiple=float(time_r), exit_bar=b, exit_price=float(close[b]),
        max_favorable_r=mfe, max_adverse_r=mae,
        bars_held=max_hold,
    ))

    return results


def _mfe_r(
    high: np.ndarray, low: np.ndarray,
    entry: float, direction: str, risk: float,
    start: int, end: int,
) -> float:
    """Max favorable excursion in R."""
    if risk <= 0:
        return 0.0
    if direction == "long":
        peak = high[start:end].max() if start < end else entry
        return float((peak - entry) / risk)
    else:
        trough = low[start:end].min() if start < end else entry
        return float((entry - trough) / risk)


def _mae_r(
    high: np.ndarray, low: np.ndarray,
    entry: float, direction: str, risk: float,
    start: int, end: int,
) -> float:
    """Max adverse excursion in R (always positive)."""
    if risk <= 0:
        return 0.0
    if direction == "long":
        trough = low[start:end].min() if start < end else entry
        return float((entry - trough) / risk)
    else:
        peak = high[start:end].max() if start < end else entry
        return float((peak - entry) / risk)


def management_summary(results: list[ManagementResult]) -> dict:
    """Aggregate management results across multiple setups."""
    by_rule: dict[str, list[float]] = {}
    for r in results:
        by_rule.setdefault(r.rule, []).append(r.r_multiple)

    summary = {}
    for rule, rs in by_rule.items():
        arr = np.array(rs)
        wins = arr[arr > 0]
        losses = arr[arr <= 0]
        summary[rule] = {
            "n": len(arr),
            "mean_r": float(arr.mean()),
            "median_r": float(np.median(arr)),
            "win_rate": float(np.mean(arr > 0)),
            "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) > 0 and wins.sum() > 0 else (
                float("inf") if len(wins) > 0 else 0.0
            ),
            "max_consec_loss": _max_consec(arr <= 0),
        }
    return summary


def _max_consec(mask: np.ndarray) -> int:
    mx = cur = 0
    for v in mask:
        if v:
            cur += 1
            mx = max(mx, cur)
        else:
            cur = 0
    return mx

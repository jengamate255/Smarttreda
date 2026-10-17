"""Calibration and evaluation: classification + trading metrics.

Primary question is never "is AUC high" but "does trading this signal at
this probability threshold produce positive expectancy out-of-sample".
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)


def classification_metrics(y_true: np.ndarray, p: np.ndarray) -> dict:
    """Probability-focused metrics (threshold chosen by caller)."""
    y = y_true.astype(int)
    if len(np.unique(y)) < 2:
        auc = float("nan")
        ap = float("nan")
    else:
        auc = float(roc_auc_score(y, p))
        ap = float(average_precision_score(y, p))
    pred = (p >= 0.5).astype(int)
    return {
        "n": len(y),
        "base_rate": float(np.mean(y)) if len(y) else 0.0,
        "auc": auc,
        "average_precision": ap,
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])) if len(np.unique(y)) > 1 else float("nan"),
        "precision@0.5": float(precision_score(y, pred, zero_division=0)),
        "recall@0.5": float(recall_score(y, pred, zero_division=0)),
    }


def calibration_buckets(y_true: np.ndarray, p: np.ndarray, n_bins: int = 10) -> list[dict]:
    """Empirical calibration table: predicted bin vs realized rate."""
    y = y_true.astype(int)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    out = []
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (p >= lo) & (p < hi) if i < n_bins - 1 else (p >= lo) & (p <= hi)
        if mask.sum() == 0:
            continue
        out.append(
            {
                "bin": i,
                "pred_low": float(lo),
                "pred_high": float(hi),
                "n": int(mask.sum()),
                "avg_pred": float(p[mask].mean()),
                "realized": float(y[mask].mean()),
            }
        )
    return out


def trading_metrics(r_multiples: np.ndarray, p: np.ndarray | None = None,
                    threshold: float = 0.5) -> dict:
    """Expectancy-first trading evaluation on R multiples."""
    if p is not None:
        keep = p >= threshold
        r = r_multiples[keep]
        selected = int(keep.sum())
    else:
        r = r_multiples
        selected = len(r)
    if len(r) == 0:
        return {
            "trades": 0, "selected": selected, "win_rate": float("nan"),
            "avg_r": float("nan"), "expectancy_r": float("nan"),
            "profit_factor": float("nan"), "max_consec_loss": 0,
        }
    wins = r[r > 0]
    losses = r[r <= 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    # Max consecutive losses
    max_consec = cur = 0
    for v in r:
        if v <= 0:
            cur += 1
            max_consec = max(max_consec, cur)
        else:
            cur = 0
    return {
        "trades": len(r),
        "selected": selected,
        "win_rate": float(np.mean(r > 0)),
        "avg_r": float(r.mean()),
        "expectancy_r": float(r.mean()),  # expectancy in R units
        "total_r": float(r.sum()),
        "profit_factor": float(pf),
        "avg_win_r": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "max_consec_loss": int(max_consec),
    }


def threshold_sweep(
    r_multiples: np.ndarray,
    p: np.ndarray,
    thresholds: tuple[float, ...] = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60),
    min_trades: int = 30,
) -> list[dict]:
    """Expectancy at each probability threshold (with trade-count floor).

    Guards against multiple-testing hallucination: thresholds with fewer
    than ``min_trades`` are reported but flagged not_evaluable.
    """
    out = []
    for t in thresholds:
        keep = p >= t
        n = int(keep.sum())
        row = {
            "threshold": float(t),
            "trades": n,
            "not_evaluable": n < min_trades,
        }
        if n >= min_trades:
            r = r_multiples[keep]
            row.update(
                {
                    "win_rate": float(np.mean(r > 0)),
                    "expectancy_r": float(r.mean()),
                    "total_r": float(r.sum()),
                    "profit_factor": float(
                        r[r > 0].sum() / -r[r <= 0].sum()
                        if (r <= 0).any() and r[r > 0].sum() > 0
                        else (np.inf if r[r > 0].sum() > 0 else 0.0)
                    ),
                }
            )
        out.append(row)
    return out


def top_decile_edge(r_multiples: np.ndarray, p: np.ndarray) -> dict:
    """Expectancy of the top-10% highest-probability setups (rank-based,
    avoids arbitrary threshold choice; needs >= 10 samples) with a
    bootstrap significance test."""
    n = len(p)
    if n < 10:
        return {"not_evaluable": True, "n": n}
    k = max(1, n // 10)
    order = np.argsort(p)[::-1][:k]
    r = r_multiples[order]
    out = {
        "not_evaluable": False,
        "n": int(k),
        "win_rate": float(np.mean(r > 0)),
        "expectancy_r": float(r.mean()),
    }
    out["bootstrap"] = bootstrap_expectancy(r)
    return out


def bootstrap_expectancy(
    r_multiples: np.ndarray,
    n_boot: int = 2000,
    seed: int = 42,
    confidence: float = 0.95,
) -> dict:
    """Bootstrap CI for mean R (expectancy). Reports whether the CI excludes 0
    — the honest significance test for a selected subset."""
    r = np.asarray(r_multiples, dtype=float)
    n = len(r)
    if n < 10:
        return {"not_evaluable": True, "n": n}
    rng = np.random.default_rng(seed)
    means = np.array(
        [r[rng.integers(0, n, n)].mean() for _ in range(n_boot)]
    )
    alpha = (1 - confidence) / 2
    lo, hi = float(np.quantile(means, alpha)), float(np.quantile(means, 1 - alpha))
    return {
        "not_evaluable": False,
        "n": int(n),
        "mean_r": float(r.mean()),
        "ci_low": lo,
        "ci_high": hi,
        "excludes_zero": bool(lo > 0 or hi < 0),
    }


def equity_curve_r(r_multiples: np.ndarray, risk_percent: float = 0.5) -> np.ndarray:
    """Equity in % (starting 100) compounding R at fixed fractional risk."""
    equity = np.ones(len(r_multiples) + 1) * 100.0
    for i, r in enumerate(r_multiples):
        equity[i + 1] = equity[i] * (1.0 + (risk_percent / 100.0) * r)
    return equity


def max_drawdown_r(r_multiples: np.ndarray, risk_percent: float = 0.5) -> float:
    eq = equity_curve_r(r_multiples, risk_percent)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak
    return float(dd.max() * 100.0) if len(dd) else 0.0


def sortino_ratio(r_multiples: np.ndarray, target_r: float = 0.0) -> float:
    """Sortino ratio: (mean - target) / downside deviation.

    Uses only negative deviations (below target) for denominator,
    per the standard Sortino definition. Returns float('inf') if no
    downside deviation exists.
    """
    r = np.asarray(r_multiples, dtype=float)
    if len(r) < 2:
        return float("nan")
    excess = r - target_r
    downside = excess[excess < 0]
    if len(downside) == 0:
        return float("inf") if excess.mean() > 0 else float("nan")
    dd = np.sqrt(np.mean(downside ** 2))
    if dd <= 0:
        return float("inf") if excess.mean() > 0 else float("nan")
    return float(excess.mean() / dd)


def recovery_factor(r_multiples: np.ndarray, risk_percent: float = 0.5) -> float:
    """Recovery factor: total R / max drawdown (in R units).

    Higher is better; measures how quickly the strategy recovers from
    drawdowns. Returns float('inf') if max drawdown is zero.
    """
    eq = equity_curve_r(r_multiples, risk_percent)
    peak = np.maximum.accumulate(eq)
    dd_abs = peak - eq
    max_dd = float(dd_abs.max())
    total_r = float(r_multiples.sum())
    if max_dd <= 0:
        return float("inf") if total_r > 0 else 0.0
    return total_r / max_dd

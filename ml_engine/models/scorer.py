"""Multi-target scorer: separate calibrated models for P(1R), P(2R), P(3R).

Each target is trained independently (different class distributions require
separate calibration). The combined output produces:
  - P(1R): probability of reaching +1R before hitting stop
  - P(2R): probability of reaching +2R before hitting stop
  - P(3R): probability of reaching +3R before hitting stop
  - expected_R: P(1R)*1 + P(2R)*1 + P(3R)*1 + P(no_R)*(-1)  [approx]

Design notes:
  - Labels come from labels.py (win_1r / win_2r / win_3r)
  - Features come from build.py (feature_vector columns)
  - Walk-forward: each fold trains 3 models from scratch
  - calibration: isotonic regression on train-fold predictions
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.pipeline import Pipeline

from ml_engine.models.train import make_pipeline

TARGET_NAMES = ("win_1r", "win_2r", "win_3r")


@dataclass
class ScorerResult:
    """Single-row prediction for one setup."""

    p_1r: float
    p_2r: float
    p_3r: float
    expected_r: float
    base_rate_1r: float
    base_rate_2r: float
    base_rate_3r: float

    def to_dict(self) -> dict:
        return {
            "p_1r": self.p_1r,
            "p_2r": self.p_2r,
            "p_3r": self.p_3r,
            "expected_r": self.expected_r,
            "base_rate_1r": self.base_rate_1r,
            "base_rate_2r": self.base_rate_2r,
            "base_rate_3r": self.base_rate_3r,
        }


@dataclass
class MultiTargetScorer:
    """Trains 3 calibrated classifiers, one per R-target.

    Usage:
        scorer = MultiTargetScorer(model_type="lgbm")
        scorer.fit(X_train, y_train_df)   # y_train_df has win_1r/2r/3r columns
        result = scorer.predict(X_test)
    """

    model_type: str = "lgbm"
    calibrate: bool = True
    seed: int = 42
    models: dict[str, Pipeline] = field(default_factory=dict)
    base_rates: dict[str, float] = field(default_factory=dict)

    def fit(
        self,
        X: np.ndarray,
        y_df: np.ndarray,
        target_names: tuple[str, ...] = TARGET_NAMES,
    ) -> None:
        """Fit one calibrated pipeline per target.

        Parameters
        ----------
        X : (n_samples, n_features) feature matrix
        y_df : DataFrame with columns win_1r, win_2r, win_3r
        """
        if hasattr(y_df, "values"):
            y_arr = y_df.values
            cols = list(y_df.columns)
        else:
            y_arr = y_df
            cols = list(target_names)

        for idx, name in enumerate(cols):
            if name not in target_names:
                continue
            y = y_arr[:, idx].astype(int) if y_arr.ndim == 2 else y_arr.astype(int)
            self.base_rates[name] = float(np.mean(y)) if len(y) else 0.5

            base_pipe = make_pipeline(self.model_type, seed=self.seed, calibrate=False)
            # Wrap with calibration on top of the base pipeline
            if self.calibrate and len(np.unique(y)) > 1:
                pipe = Pipeline([
                    ("pre", Pipeline(base_pipe.steps[:-1])),
                    ("clf", CalibratedClassifierCV(
                        base_pipe.steps[-1][1],
                        method="isotonic",
                        cv=3,
                    )),
                ])
            else:
                pipe = base_pipe

            pipe.fit(X, y)
            self.models[name] = pipe

    def predict(self, X: np.ndarray) -> list[ScorerResult]:
        """Predict P(1R), P(2R), P(3R) and compute expected_R."""
        results = []
        for i in range(len(X)):
            row = X[i : i + 1]
            probs = {}
            for name, model in self.models.items():
                probs[name] = float(model.predict_proba(row)[0, 1])

            p1 = probs.get("win_1r", 0.5)
            p2 = probs.get("win_2r", 0.2)
            p3 = probs.get("win_3r", 0.1)

            # expected_R: weighted sum over outcome buckets
            # P(stop) = 1-p1 -> R=-1; P(1R only) = p1-p2 -> R=+1;
            # P(2R only) = p2-p3 -> R=+2; P(≥3R) = p3 -> R=+3
            # E[R] = (1-p1)*(-1) + (p1-p2)*1 + (p2-p3)*2 + p3*3
            #      = -1 + 2*p1 + p2 + p3
            er = float(2 * p1 + p2 + p3 - 1.0)

            results.append(
                ScorerResult(
                    p_1r=p1,
                    p_2r=p2,
                    p_3r=p3,
                    expected_r=er,
                    base_rate_1r=self.base_rates.get("win_1r", 0.0),
                    base_rate_2r=self.base_rates.get("win_2r", 0.0),
                    base_rate_3r=self.base_rates.get("win_3r", 0.0),
                )
            )
        return results


def expected_R_from_probs(p1: float, p2: float, p3: float) -> float:
    """Theoretical E[R] given P(≥1R), P(≥2R), P(≥3R).

    Derivation:
        P(stop)         = 1 - p1       -> R = -1
        P(1R only)      = p1 - p2      -> R = +1
        P(2R only)      = p2 - p3      -> R = +2
        P(≥3R)          = p3           -> R = +3
        E[R] = (1-p1)*(-1) + (p1-p2)*1 + (p2-p3)*2 + p3*3
             = -1 + 2*p1 + p2 + p3
    """
    return float(2 * p1 + p2 + p3 - 1.0)

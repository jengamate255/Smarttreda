"""Model training: LR / RF / LGBM baselines with temporal CV + calibration.

Pipeline per model type:
  StandardScaler (fit on train fold only) -> classifier -> calibration layer
  (isotonic by default, fit on train fold only).

Walk-forward evaluation lives in evaluate.py; this module defines the
estimators and the fit/serialize API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def _lightgbm_available() -> bool:
    try:
        import lightgbm  # noqa: F401
        return True
    except ImportError:
        return False


def _xgboost_available() -> bool:
    try:
        import xgboost  # noqa: F401
        return True
    except ImportError:
        return False


def make_pipeline(model_type: str, seed: int = 42, calibrate: bool = True) -> Pipeline:
    """Build a fresh estimator pipeline. model_type: lr|rf|lgbm|xgb."""
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()

    if model_type == "lr":
        clf = LogisticRegression(C=1.0, max_iter=2000, random_state=seed)
    elif model_type == "rf":
        clf = RandomForestClassifier(
            n_estimators=400,
            max_depth=8,
            min_samples_leaf=10,
            n_jobs=-1,
            random_state=seed,
            class_weight="balanced",
        )
    elif model_type == "lgbm":
        if not _lightgbm_available():
            raise ImportError("lightgbm not installed")
        from lightgbm import LGBMClassifier

        clf = LGBMClassifier(
            n_estimators=300,
            learning_rate=0.05,
            num_leaves=31,
            min_child_samples=20,
            subsample=0.9,
            colsample_bytree=0.9,
            random_state=seed,
            verbosity=-1,
            class_weight="balanced",
        )
    elif model_type == "xgb":
        if not _xgboost_available():
            raise ImportError("xgboost not installed")
        from xgboost import XGBClassifier

        clf = XGBClassifier(
            n_estimators=300,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.9,
            colsample_bytree=0.9,
            random_state=seed,
            eval_metric="logloss",
            n_jobs=-1,
        )
    else:
        raise ValueError(f"unknown model type {model_type}")

    if calibrate and model_type != "lr":
        # cv=3 prefitted pattern: wrap a clone chain; CalibratedClassifierCV
        # with cv=3 uses internal folds of the TRAIN data only.
        clf = CalibratedClassifierCV(clf, method="isotonic", cv=3)

    return Pipeline(
        [("impute", imputer), ("scale", scaler), ("clf", clf)]
    )


AVAILABLE_MODELS = ["lr", "rf", "lgbm", "xgb"]


@dataclass
class TrainedModel:
    model_type: str
    pipeline: Pipeline
    feature_cols: list[str]
    target: str
    n_train: int
    train_period: tuple[str, str]
    seed: int = 42
    metadata: dict = field(default_factory=dict)
    calibrator: object | None = None  # optional post-hoc calibrator

    def predict_proba(self, X) -> np.ndarray:
        raw = self.pipeline.predict_proba(X)[:, 1]
        if self.calibrator is not None:
            return np.clip(
                np.asarray(self.calibrator.predict(raw), dtype=float), 0.0, 1.0
            )
        return raw


def fit_calibrator_on_train(
    model: TrainedModel, X: np.ndarray, y: np.ndarray, seed: int = 42
) -> TrainedModel:
    """Fit an isotonic calibrator via temporal split of the TRAIN fold only:
    the last 30% of the training period calibrates probabilities produced by
    a model refit on the first 70%. Test data is never touched."""
    n = len(y)
    cut = int(n * 0.7)
    if cut < 50 or n - cut < 50:
        return model  # too small to calibrate honestly
    if len(np.unique(y[cut:])) < 2 or len(np.unique(y[:cut])) < 2:
        return model
    base = fit_model(
        X[:cut], y[:cut], model.feature_cols, model.target, model.model_type,
        train_period=model.train_period, seed=seed,
    )
    p_val = base.predict_proba(X[cut:])
    from sklearn.isotonic import IsotonicRegression

    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(p_val, y[cut:])
    model.calibrator = iso
    return model


def fit_model(
    X: np.ndarray,
    y: np.ndarray,
    feature_cols: list[str],
    target: str,
    model_type: str = "lgbm",
    train_period: tuple[str, str] = ("", ""),
    seed: int = 42,
    calibrate: bool = True,
) -> TrainedModel:
    pipe = make_pipeline(model_type, seed=seed, calibrate=calibrate)
    pipe.fit(X, y)
    return TrainedModel(
        model_type=model_type,
        pipeline=pipe,
        feature_cols=feature_cols,
        target=target,
        n_train=len(y),
        train_period=train_period,
        seed=seed,
    )

"""Tests for multi-target scorer, sortino_ratio, recovery_factor."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml_engine.models.evaluate import recovery_factor, sortino_ratio
from ml_engine.models.scorer import MultiTargetScorer, expected_R_from_probs


class TestExpectedR:
    def test_all_win(self):
        # p1=1, p2=1, p3=1 -> every trade reaches 3R
        # P(stop)=0, P(1R only)=0, P(2R only)=0, P(3R)=1
        # E = 0*(-1) + 0*1 + 0*2 + 1*3 = 3.0
        assert expected_R_from_probs(1.0, 1.0, 1.0) == 3.0

    def test_all_stop(self):
        # p1=0 -> nobody reaches 1R
        assert expected_R_from_probs(0.0, 0.0, 0.0) == -1.0

    def test_half_reach_1r(self):
        # p1=0.5, p2=0, p3=0 -> 50% stop (-1), 50% get 1R (+1)
        assert expected_R_from_probs(0.5, 0.0, 0.0) == 0.0

    def test_monotone_decreasing_probs(self):
        # p1 >= p2 >= p3 is required; formula handles any valid input
        er = expected_R_from_probs(0.6, 0.3, 0.1)
        # P(stop)=0.4, P(1R)=0.3, P(2R)=0.2, P(3R)=0.1
        # E = 0.4*(-1) + 0.3*1 + 0.2*2 + 0.1*3 = -0.4+0.3+0.4+0.3 = 0.6
        assert abs(er - 0.6) < 1e-9


class TestSortinoRatio:
    def test_all_positive(self):
        r = np.array([1.0, 2.0, 3.0])
        s = sortino_ratio(r)
        assert s > 0

    def test_all_negative(self):
        r = np.array([-1.0, -2.0, -3.0])
        s = sortino_ratio(r)
        assert s < 0

    def test_mixed_positive_mean(self):
        r = np.array([2.0, -0.5, 1.0, -0.5])
        s = sortino_ratio(r)
        assert s > 0

    def test_no_downside(self):
        r = np.array([1.0, 2.0, 3.0])
        s = sortino_ratio(r)
        assert s == float("inf")

    def test_too_few(self):
        s = sortino_ratio(np.array([1.0]))
        assert np.isnan(s)


class TestRecoveryFactor:
    def test_straight_up(self):
        r = np.array([1.0, 1.0, 1.0])
        rf = recovery_factor(r)
        assert rf == float("inf")  # no drawdown

    def test_drawdown_recovery(self):
        r = np.array([-1.0, 2.0, -1.0, 2.0])
        rf = recovery_factor(r)
        assert rf > 0

    def test_all_loss(self):
        r = np.array([-1.0, -2.0])
        rf = recovery_factor(r)
        assert rf < 0


class TestMultiTargetScorer:
    @pytest.fixture
    def sample_data(self):
        rng = np.random.default_rng(42)
        n = 200
        X = rng.normal(size=(n, 10))
        # synthetic targets: p(1r) > p(2r) > p(3r)
        y1 = (X[:, 0] + X[:, 1] > 0).astype(int)
        y2 = (X[:, 0] + X[:, 1] > 1.0).astype(int)
        y3 = (X[:, 0] + X[:, 1] > 2.0).astype(int)
        y_df = pd.DataFrame({"win_1r": y1, "win_2r": y2, "win_3r": y3})
        return X, y_df

    def test_fit_predict(self, sample_data):
        X, y_df = sample_data
        scorer = MultiTargetScorer(model_type="rf", calibrate=False)
        scorer.fit(X[:160], y_df.iloc[:160])
        results = scorer.predict(X[160:])
        assert len(results) == 40
        for r in results:
            assert 0.0 <= r.p_1r <= 1.0
            assert 0.0 <= r.p_2r <= 1.0
            assert 0.0 <= r.p_3r <= 1.0
            assert isinstance(r.expected_r, float)

    def test_base_rates(self, sample_data):
        X, y_df = sample_data
        scorer = MultiTargetScorer(model_type="rf", calibrate=False)
        scorer.fit(X, y_df)
        assert abs(scorer.base_rates["win_1r"] - y_df["win_1r"].mean()) < 1e-6
        assert abs(scorer.base_rates["win_2r"] - y_df["win_2r"].mean()) < 1e-6

    def test_expected_r_range(self, sample_data):
        X, y_df = sample_data
        scorer = MultiTargetScorer(model_type="rf", calibrate=False)
        scorer.fit(X[:160], y_df.iloc[:160])
        results = scorer.predict(X[160:])
        ers = [r.expected_r for r in results]
        # E[R] is in [-1, 3]
        assert all(-1.0 <= er <= 3.0 for er in ers)

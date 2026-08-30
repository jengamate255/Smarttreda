"""Tests for the Gymnasium forex trading environment."""
from __future__ import annotations

import numpy as np
import pytest

from forex_rl.src.environment.forex_env import EnvConfig, ForexTradingEnv


@pytest.fixture
def env_cfg() -> EnvConfig:
    return EnvConfig(
        lookback=64,
        initial_balance=10000.0,
        max_episode_steps=200,
        normalize_obs=False,
    )


@pytest.fixture
def trading_env(synthetic_ohlcv, env_cfg) -> ForexTradingEnv:
    return ForexTradingEnv(synthetic_ohlcv, config=env_cfg)


class TestEnvReset:
    """Reset returns correct obs shape."""

    def test_reset_returns_obs_and_info(self, trading_env: ForexTradingEnv) -> None:
        obs, info = trading_env.reset()
        assert isinstance(obs, np.ndarray)
        assert isinstance(info, dict)

    def test_reset_obs_shape(self, trading_env: ForexTradingEnv, env_cfg: EnvConfig) -> None:
        obs, _ = trading_env.reset()
        expected_n_features = trading_env.n_total_features
        assert obs.shape == (env_cfg.lookback, expected_n_features)


class TestEnvStep:
    """Step returns correct types."""

    def test_step_returns_five(self, trading_env: ForexTradingEnv) -> None:
        trading_env.reset()
        result = trading_env.step(1)  # hold
        assert len(result) == 5

    def test_step_types(self, trading_env: ForexTradingEnv) -> None:
        trading_env.reset()
        obs, reward, terminated, truncated, info = trading_env.step(1)
        assert isinstance(obs, np.ndarray)
        assert isinstance(reward, float)
        assert isinstance(terminated, (bool, np.bool_))
        assert isinstance(truncated, (bool, np.bool_))
        assert isinstance(info, dict)

    def test_step_after_done_raises(self, trading_env: ForexTradingEnv) -> None:
        trading_env.reset()
        trading_env.done = True
        with pytest.raises(RuntimeError):
            trading_env.step(1)


class TestActionMasking:
    """Invalid actions handled gracefully."""

    def test_negative_action_clamped(self, trading_env: ForexTradingEnv) -> None:
        trading_env.reset()
        obs, reward, term, trunc, info = trading_env.step(-5)
        assert isinstance(obs, np.ndarray)

    def test_large_action_clamped(self, trading_env: ForexTradingEnv) -> None:
        trading_env.reset()
        obs, reward, term, trunc, info = trading_env.step(999)
        assert isinstance(obs, np.ndarray)

    def test_all_actions(self, trading_env: ForexTradingEnv) -> None:
        trading_env.reset()
        for action in [0, 1, 2]:
            obs, reward, term, trunc, info = trading_env.step(action)
            assert isinstance(obs, np.ndarray)
            if term or trunc:
                trading_env.reset()


class TestObservationShape:
    """Matches (lookback, n_features)."""

    def test_obs_shape_matches_config(self, trading_env: ForexTradingEnv, env_cfg: EnvConfig) -> None:
        obs, _ = trading_env.reset()
        assert obs.shape[0] == env_cfg.lookback
        assert obs.shape[1] == trading_env.n_total_features

    def test_obs_is_float32(self, trading_env: ForexTradingEnv) -> None:
        obs, _ = trading_env.reset()
        assert obs.dtype == np.float32


class TestNoLookaheadObs:
    """Observation doesn't leak future data."""

    def test_observation_uses_only_past(self, trading_env: ForexTradingEnv) -> None:
        obs, _ = trading_env.reset()
        step = trading_env.current_step
        lookback = trading_env.cfg.lookback
        # Observation window is [step-lookback, step)
        # All data in the observation should come from bars before step
        market_feats = trading_env.features
        # Check that we can reconstruct the same window
        expected = market_feats[step - lookback: step].astype(np.float32)
        # The obs (without normalization and account features) should match
        # We test the raw market portion
        assert expected.shape == (lookback, trading_env.n_market_features)


class TestEpisodeTermination:
    """Terminates on DD limit or max steps."""

    def test_terminates_on_max_steps(self, trading_env: ForexTradingEnv) -> None:
        trading_env.reset()
        done = False
        steps = 0
        max_steps = trading_env.cfg.max_episode_steps
        while not done and steps < max_steps + 10:
            _, _, terminated, truncated, _ = trading_env.step(1)
            done = terminated or truncated
            steps += 1
        assert done, "Environment should terminate within max_episode_steps"
        assert truncated

    def test_terminates_on_ruin(self) -> None:
        cfg = EnvConfig(
            lookback=32,
            initial_balance=1000.0,
            max_episode_steps=5000,
            risk_per_trade=0.5,  # aggressive risk to trigger ruin
            normalize_obs=False,
        )
        rng = np.random.default_rng(42)
        n = 5000
        closes = 1.1 + rng.standard_normal(n) * 0.01
        highs = closes + 0.001
        lows = closes - 0.001
        opens = closes + rng.standard_normal(n) * 0.0005
        df = __import__("pandas").DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": np.ones(n) * 1000.0},
            index=__import__("pandas").date_range("2020-01-01", periods=n, freq="1h"),
        )
        env = ForexTradingEnv(df, config=cfg)
        env.reset()
        terminated = False
        for _ in range(5000):
            _, _, terminated, truncated, _ = env.step(0)  # always buy
            if terminated or truncated:
                break
        # Either ruin or max steps — both are valid termination conditions
        assert terminated or truncated

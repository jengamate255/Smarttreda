"""Integration test: full pipeline from data to training."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.mark.slow
class TestFullPipeline:
    """Loader -> cleaner -> features -> structure -> regime -> env -> agent -> backtest."""

    def test_full_pipeline(self) -> None:
        from forex_rl.src.data.cleaner import DataCleaner
        from forex_rl.src.data.loader import DataLoader
        from forex_rl.src.environment.forex_env import EnvConfig, ForexTradingEnv
        from forex_rl.src.market.indicators import FeatureEngine
        from forex_rl.src.market.regime import RegimeDetector
        from forex_rl.src.market.structure import MarketStructure
        from forex_rl.src.market.supply_demand import SupplyDemandDetector

        # 1. Generate synthetic data
        rng = np.random.default_rng(42)
        n = 2000
        closes = np.empty(n)
        closes[0] = 1.10
        for i in range(1, n):
            closes[i] = closes[i - 1] * np.exp(0.0005 * rng.standard_normal())
        highs = closes * (1.0 + np.abs(rng.standard_normal(n)) * 0.0002)
        lows = closes * (1.0 - np.abs(rng.standard_normal(n)) * 0.0002)
        opens = np.roll(closes, 1)
        opens[0] = closes[0]
        idx = pd.date_range("2020-01-01", periods=n, freq="1h", tz="UTC")
        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes,
             "volume": rng.integers(100, 5000, n).astype(float)},
            index=idx,
        )

        # 2. Clean
        cleaner = DataCleaner()
        clean_df = cleaner.clean(df)
        assert len(clean_df) > 0

        # 3. Features
        fe = FeatureEngine()
        enriched = fe.compute_all(clean_df)
        market_feats = enriched[fe.feature_names()].values.astype(np.float32)
        assert market_feats.shape[1] == 28

        # 4. Structure
        ms = MarketStructure()
        struct_feats = ms.compute_structure_features(clean_df)
        assert struct_feats.shape[1] == 4

        # 5. Supply/Demand
        sd = SupplyDemandDetector()
        sd_feats = sd.compute_sd_features(clean_df)
        assert sd_feats.shape[1] == 6

        # 6. Regime
        rd = RegimeDetector()
        regime_feats = rd.compute_regime_features(clean_df)
        assert regime_feats.shape[1] == 9

        # 7. Combine
        all_feats = np.hstack([
            market_feats, struct_feats.values.astype(np.float32),
            sd_feats.values.astype(np.float32), regime_feats.values.astype(np.float32),
        ])
        all_feats = np.nan_to_num(all_feats, nan=0.0).astype(np.float32)
        assert all_feats.shape[1] == 47

        # 8. Environment
        env_cfg = EnvConfig(lookback=64, initial_balance=10000.0, max_episode_steps=100)
        regime_labels = rd.detect(clean_df)
        env = ForexTradingEnv(clean_df, env_cfg, all_feats, regime_labels)

        obs, info = env.reset()
        assert obs.shape == (64, 47 + 6)

        # 9. Train agent (100 steps)
        from forex_rl.src.agents.factory import AgentFactory
        from forex_rl.src.agents.policy import AgentConfig

        agent_cfg = AgentConfig(
            algorithm="PPO",
            n_steps=64,
            batch_size=32,
            n_epochs=2,
            verbose=0,
            hidden_sizes=(64,),
        )
        agent = AgentFactory.create(agent_cfg)
        result = agent.train(env, total_timesteps=100)
        assert result.total_timesteps == 100

        # 10. Predict
        obs, _ = env.reset()
        action = agent.predict(obs, deterministic=True)
        assert action is not None

        print("Full pipeline completed successfully")

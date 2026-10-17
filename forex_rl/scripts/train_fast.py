"""Fast training pipeline: reduced data + MTF + PPO.

Optimized for speed: uses 5000 bars, 20k steps, smaller network.
"""
import sys
import os
import time
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd

from forex_rl.src.data.loader import DataLoader
from forex_rl.src.data.cleaner import DataCleaner
from forex_rl.src.market.indicators import FeatureEngine
from forex_rl.src.market.structure import MarketStructure
from forex_rl.src.market.supply_demand import SupplyDemandDetector
from forex_rl.src.market.regime import RegimeDetector
from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig
from forex_rl.src.agents.ppo_agent import PPOAgent
from forex_rl.src.agents.policy import AgentConfig
from forex_rl.src.backtest.engine import BacktestEngine, BacktestConfig
from forex_rl.src.backtest.metrics import BacktestMetrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("fast_train")


def prepare_features_fast(symbol: str, tf: str = "H1", n_bars: int = 5000) -> tuple:
    """Load data, compute features, return (df, features, names)."""
    data_path = Path(f"forex_rl/data/raw/{symbol}_{tf}.parquet")
    df = pd.read_parquet(data_path)

    # Use last n_bars — keep DatetimeIndex for session features
    if len(df) > n_bars:
        df = df.iloc[-n_bars:].copy()

    logger.info("Data: %d bars", len(df))

    # Compute all features (needs DatetimeIndex for sessions)
    engine = FeatureEngine()
    df = engine.compute_all(df)

    ms = MarketStructure()
    struct = ms.compute_structure_features(df)
    for col in struct.columns:
        df[col] = struct[col].values

    sd = SupplyDemandDetector()
    sd_feats = sd.compute_sd_features(df)
    for col in sd_feats.columns:
        df[col] = sd_feats[col].values

    rd = RegimeDetector()
    regime_feats = rd.compute_regime_features(df)
    for col in regime_feats.columns:
        df[col] = regime_feats[col].values

    # Load H4 for MTF context
    h4_path = Path(f"forex_rl/data/raw/{symbol}_H4.parquet")
    if h4_path.exists():
        h4_df = pd.read_parquet(h4_path)
        h4_engine = FeatureEngine()
        h4_feats = h4_engine.compute_all(h4_df)
        h4_feats.index = h4_feats.index.tz_localize(None) if h4_feats.index.tz else h4_feats.index
        h4_sub = h4_feats[["ema_20", "ema_50", "rsi_14", "atr_14", "macd_histogram"]].copy()
        h4_sub.columns = ["h4_ema20", "h4_ema50", "h4_rsi14", "h4_atr14", "h4_macd_hist"]
        h4_aligned = h4_sub.reindex(df.index, method="ffill").fillna(0)
        for col in h4_aligned.columns:
            df[col] = h4_aligned[col].values
        logger.info("Added 5 H4 MTF features")

    # Now reset index for the env
    df = df.reset_index(drop=True)

    # Build feature array
    exclude = {"time", "open", "high", "low", "close", "volume"}
    feature_cols = [c for c in df.columns if c not in exclude]
    features = df[feature_cols].values.astype(np.float32)
    features = np.nan_to_num(features, nan=0.0)

    logger.info("Features: %d bars x %d columns", *features.shape)
    return df, features, feature_cols


def train_and_evaluate(
    symbol: str,
    algorithm: str = "PPO",
    n_bars: int = 5000,
    n_steps: int = 20000,
    eval_episodes: int = 10,
    seed: int = 42,
) -> dict:
    """Fast train + eval pipeline."""
    t0 = time.time()

    # 1. Prepare features
    logger.info("=" * 50)
    logger.info("TRAINING %s %s (%d bars, %d steps)", symbol, algorithm, n_bars, n_steps)
    logger.info("=" * 50)

    df, features, feature_names = prepare_features_fast(symbol, n_bars=n_bars)

    # 2. Split 70/15/15
    n = len(features)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)

    train_feat = features[:train_end]
    val_feat = features[train_end:val_end]
    test_feat = features[val_end:]
    train_df = df.iloc[:train_end].reset_index(drop=True)
    val_df = df.iloc[train_end:val_end].reset_index(drop=True)
    test_df = df.iloc[val_end:].reset_index(drop=True)

    logger.info("Split: train=%d val=%d test=%d", len(train_feat), len(val_feat), len(test_feat))

    # 3. Create envs
    env_config = EnvConfig(
        lookback=64,
        action_mode="discrete",
        reward_type="risk_adjusted",
        max_episode_steps=min(500, train_end - 64),
        normalize_obs=True,
    )

    train_env = ForexTradingEnv(train_df, env_config, features=train_feat)
    val_env = ForexTradingEnv(val_df, env_config, features=val_feat)
    test_env = ForexTradingEnv(test_df, env_config, features=test_feat)

    logger.info("Obs shape: %s", train_env.observation_space.shape)

    # 4. Train
    agent_config = AgentConfig(
        algorithm=algorithm,
        learning_rate=1e-4,
        n_steps=min(2048, len(train_feat) // 2),
        batch_size=128,
        n_epochs=10,
        hidden_sizes=(64, 64),
        verbose=0,
        seed=seed,
    )
    agent = PPOAgent(agent_config) if algorithm == "PPO" else None
    if agent is None:
        raise ValueError(f"Only PPO supported in fast_train")

    logger.info("Training PPO...")
    train_result = agent.train(train_env, total_timesteps=n_steps)
    logger.info("Done: %.1fs, reward=%.4f", train_result.training_time, train_result.mean_reward)

    # 5. Evaluate
    logger.info("Evaluating on val set...")
    val_result = agent.evaluate(val_env, n_episodes=eval_episodes)
    logger.info("Val: reward=%.4f±%.4f, sharpe=%.2f, dd=%.2f%%",
                val_result.mean_reward, val_result.std_reward,
                val_result.mean_sharpe, val_result.mean_max_dd)

    # 6. Backtest
    logger.info("Backtesting on test set...")
    bt_config = BacktestConfig()
    bt_engine = BacktestEngine(bt_config)
    bt_result = bt_engine.run(agent, test_env)
    m = bt_result.metrics

    logger.info("─── TEST RESULTS ───")
    logger.info("  Trades: %d", m.get("total_trades", 0))
    logger.info("  Net P&L: $%.2f", m.get("net_profit", 0))
    logger.info("  Sharpe: %.3f", m.get("sharpe_ratio", 0))
    logger.info("  Sortino: %.3f", m.get("sortino_ratio", 0))
    logger.info("  Max DD: %.2f%%", m.get("max_drawdown", 0))
    logger.info("  PF: %.3f", m.get("profit_factor", 0))
    logger.info("  Win Rate: %.1f%%", m.get("win_rate", 0))

    # 7. Save
    out = Path("forex_rl/models/checkpoints")
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / f"ppo_{symbol.lower()}_h1_fast"
    agent.save(str(model_path))

    results = {
        "symbol": symbol,
        "features": len(feature_names),
        "train_bars": len(train_feat),
        "val_reward": val_result.mean_reward,
        "val_sharpe": val_result.mean_sharpe,
        "val_max_dd": val_result.mean_max_dd,
        "test_metrics": {k: v for k, v in m.items() if isinstance(v, (int, float))},
        "elapsed": time.time() - t0,
    }

    results_path = out / f"ppo_{symbol.lower()}_h1_fast_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    elapsed = time.time() - t0
    logger.info("=" * 50)
    logger.info("DONE: %s — Sharpe=%.2f, PF=%.2f, DD=%.2f%% — %.0fs",
                symbol, m.get("sharpe_ratio", 0), m.get("profit_factor", 0),
                m.get("max_drawdown", 0), elapsed)
    logger.info("=" * 50)

    return results


if __name__ == "__main__":
    results = train_and_evaluate("EURUSD", n_bars=5000, n_steps=20000, eval_episodes=10)
    print(json.dumps(results, indent=2, default=str))

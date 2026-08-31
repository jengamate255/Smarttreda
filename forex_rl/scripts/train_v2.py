"""Fast training pipeline v2: fixed reward + high exploration + feature selection.

Changes from v1:
- Raw P&L reward (no clipping, no complex scaling)
- Higher entropy coefficient (0.05) for more exploration
- Top-20 feature selection by variance
- Synthetic trending market warmup phase
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


def select_top_features(features: np.ndarray, feature_names: list, top_n: int = 20) -> tuple:
    """Select top N features by variance (drop low-variance noise)."""
    variances = np.nanvar(features, axis=0)
    # Replace nan variances with 0
    variances = np.nan_to_num(variances, nan=0.0)
    top_indices = np.argsort(variances)[-top_n:]
    top_indices = np.sort(top_indices)  # keep original order
    selected_names = [feature_names[i] for i in top_indices]
    selected_features = features[:, top_indices]
    return selected_features, selected_names


def prepare_features_fast(symbol: str, tf: str = "H1", n_bars: int = 5000,
                          top_n_features: int = 20) -> tuple:
    """Load data, compute features, select top N."""
    data_path = Path(f"forex_rl/data/raw/{symbol}_{tf}.parquet")
    df = pd.read_parquet(data_path)

    if len(df) > n_bars:
        df = df.iloc[-n_bars:].copy()

    logger.info("Data: %d bars", len(df))

    # Compute all features
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

    df = df.reset_index(drop=True)

    exclude = {"time", "open", "high", "low", "close", "volume"}
    feature_cols = [c for c in df.columns if c not in exclude]
    features = df[feature_cols].values.astype(np.float32)
    features = np.nan_to_num(features, nan=0.0)

    logger.info("Raw features: %d bars x %d columns", *features.shape)

    # Feature selection: top N by variance
    if features.shape[1] > top_n_features:
        features, feature_cols = select_top_features(features, feature_cols, top_n_features)
        logger.info("Selected top %d features: %s", top_n_features, feature_cols)

    return df, features, feature_cols


def create_synthetic_trending_env(n_bars: int = 2000) -> ForexTradingEnv:
    """Create a synthetic trending market for warmup training.
    
    The agent should learn: in a clear uptrend, BUY is profitable.
    In a clear downtrend, SELL is profitable. This is the simplest possible signal.
    """
    np.random.seed(42)
    
    # Generate trending prices with noise
    t = np.arange(n_bars)
    # Uptrend for first half, downtrend for second half
    trend = np.concatenate([
        np.linspace(1.1000, 1.1200, n_bars // 2),  # uptrend
        np.linspace(1.1200, 1.1000, n_bars // 2),  # downtrend
    ])
    noise = np.random.normal(0, 0.0003, n_bars)
    close = trend + noise
    
    # Build OHLCV
    high = close + np.abs(np.random.normal(0, 0.0002, n_bars))
    low = close - np.abs(np.random.normal(0, 0.0002, n_bars))
    open_ = close + np.random.normal(0, 0.0001, n_bars)
    volume = np.random.randint(100, 1000, n_bars).astype(float)
    
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="h")
    df = pd.DataFrame({
        "time": idx, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }, index=idx)
    
    # Compute features
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
    
    df = df.reset_index(drop=True)
    
    exclude = {"time", "open", "high", "low", "close", "volume"}
    feature_cols = [c for c in df.columns if c not in exclude]
    features = df[feature_cols].values.astype(np.float32)
    features = np.nan_to_num(features, nan=0.0)
    
    # Select top 20 features by variance
    features, feature_cols = select_top_features(features, feature_cols, 20)
    
    config = EnvConfig(
        lookback=64,
        action_mode="discrete",
        reward_type="simple",  # Raw P&L
        max_episode_steps=500,
        normalize_obs=True,
    )
    
    return ForexTradingEnv(df, config, features=features)


def train_and_evaluate(
    symbol: str,
    algorithm: str = "PPO",
    n_bars: int = 5000,
    n_steps: int = 100000,
    eval_episodes: int = 10,
    seed: int = 42,
) -> dict:
    """Fast train + eval pipeline v2."""
    t0 = time.time()

    # 1. Prepare features (top 20)
    logger.info("=" * 50)
    logger.info("TRAINING %s %s (%d bars, %d steps)", symbol, algorithm, n_bars, n_steps)
    logger.info("=" * 50)

    df, features, feature_names = prepare_features_fast(symbol, n_bars=n_bars, top_n_features=20)

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

    # 3. Create envs with SIMPLE reward (raw P&L, no clipping)
    env_config = EnvConfig(
        lookback=64,
        action_mode="discrete",
        reward_type="simple",  # Raw P&L reward
        max_episode_steps=min(500, train_end - 64),
        normalize_obs=True,
    )

    train_env = ForexTradingEnv(train_df, env_config, features=train_feat)
    val_env = ForexTradingEnv(val_df, env_config, features=val_feat)
    test_env = ForexTradingEnv(test_df, env_config, features=test_feat)

    logger.info("Obs shape: %s", train_env.observation_space.shape)

    # 4. SYNTHETIC WARMUP: pre-train on easy trending market
    logger.info("Phase 1: Synthetic warmup (5k steps)...")
    synth_env = create_synthetic_trending_env(2000)
    agent_config_warmup = AgentConfig(
        algorithm=algorithm,
        learning_rate=3e-4,
        n_steps=512,
        batch_size=64,
        n_epochs=4,
        hidden_sizes=(64, 64),
        ent_coef=0.1,  # High exploration for warmup
        verbose=0,
        seed=seed,
    )
    warmup_agent = PPOAgent(agent_config_warmup)
    warmup_agent.train(synth_env, total_timesteps=5000)
    logger.info("Warmup done. Agent explored %d steps on synthetic data.", 5000)

    # 5. FINE-TUNE on real data with sustained high exploration
    logger.info("Phase 2: Fine-tuning on real data (%d steps)...", n_steps)
    agent_config = AgentConfig(
        algorithm=algorithm,
        learning_rate=3e-4,
        n_steps=min(512, len(train_feat) // 4),
        batch_size=64,
        n_epochs=4,
        hidden_sizes=(64, 64),
        ent_coef=0.15,             # HIGH exploration — keeps agent trading
        gamma=0.995,               # Longer horizon
        verbose=0,
        seed=seed,
    )
    agent = PPOAgent(agent_config)
    
    # Initialize with warmup weights
    agent.model = agent._build_model(train_env)
    # Copy warmup policy weights
    agent.model.policy.load_state_dict(warmup_agent.model.policy.state_dict(), strict=False)
    
    # Use higher total steps but keep entropy high
    train_result = agent.train(train_env, total_timesteps=n_steps)
    logger.info("Done: %.1fs, reward=%.4f", train_result.training_time, train_result.mean_reward)

    # 6. Evaluate
    logger.info("Evaluating on val set...")
    val_result = agent.evaluate(val_env, n_episodes=eval_episodes)
    logger.info("Val: reward=%.4f±%.4f, sharpe=%.2f, dd=%.2f%%",
                val_result.mean_reward, val_result.std_reward,
                val_result.mean_sharpe, val_result.mean_max_dd)

    # 7. Backtest
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

    # 8. Save
    out = Path("forex_rl/models/checkpoints")
    out.mkdir(parents=True, exist_ok=True)
    model_path = out / f"ppo_{symbol.lower()}_h1_v2"
    agent.save(str(model_path))

    results = {
        "symbol": symbol,
        "features": len(feature_names),
        "selected_features": feature_names[:20],
        "train_bars": len(train_feat),
        "reward_type": "simple",
        "ent_coef": 0.05,
        "val_reward": val_result.mean_reward,
        "val_sharpe": val_result.mean_sharpe,
        "val_max_dd": val_result.mean_max_dd,
        "test_metrics": {k: v for k, v in m.items() if isinstance(v, (int, float))},
        "elapsed": time.time() - t0,
    }

    results_path = out / f"ppo_{symbol.lower()}_h1_v2_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    elapsed = time.time() - t0
    logger.info("=" * 50)
    logger.info("DONE: %s — Sharpe=%.2f, PF=%.2f, DD=%.2f%%, Trades=%d — %.0fs",
                symbol, m.get("sharpe_ratio", 0), m.get("profit_factor", 0),
                m.get("max_drawdown", 0), m.get("total_trades", 0), elapsed)
    logger.info("=" * 50)

    return results


if __name__ == "__main__":
    results = train_and_evaluate("EURUSD", n_bars=5000, n_steps=100000, eval_episodes=10)
    print(json.dumps(results, indent=2, default=str))

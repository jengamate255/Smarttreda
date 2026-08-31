"""Quick diagnostic: what actions does the trained agent take?"""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

import numpy as np
import pandas as pd
from pathlib import Path

from forex_rl.src.market.indicators import FeatureEngine
from forex_rl.src.market.structure import MarketStructure
from forex_rl.src.market.supply_demand import SupplyDemandDetector
from forex_rl.src.market.regime import RegimeDetector
from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig
from forex_rl.src.agents.ppo_agent import PPOAgent
from forex_rl.src.agents.policy import AgentConfig

# Load EURUSD
df = pd.read_parquet("forex_rl/data/raw/EURUSD_H1.parquet")
df = df.iloc[-5000:].copy()

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

# H4 MTF
h4_df = pd.read_parquet("forex_rl/data/raw/EURUSD_H4.parquet")
h4_engine = FeatureEngine()
h4_feats = h4_engine.compute_all(h4_df)
h4_feats.index = h4_feats.index.tz_localize(None) if h4_feats.index.tz else h4_feats.index
h4_sub = h4_feats[["ema_20", "ema_50", "rsi_14", "atr_14", "macd_histogram"]].copy()
h4_sub.columns = ["h4_ema20", "h4_ema50", "h4_rsi14", "h4_atr14", "h4_macd_hist"]
h4_aligned = h4_sub.reindex(df.index, method="ffill").fillna(0)
for col in h4_aligned.columns:
    df[col] = h4_aligned[col].values

df = df.reset_index(drop=True)
exclude = {"time", "open", "high", "low", "close", "volume"}
feature_cols = [c for c in df.columns if c not in exclude]
features = df[feature_cols].values.astype(np.float32)
features = np.nan_to_num(features, nan=0.0)

# Select top 20
variances = np.nanvar(features, axis=0)
variances = np.nan_to_num(variances, nan=0.0)
top_idx = np.argsort(variances)[-20:]
top_idx = np.sort(top_idx)
features = features[:, top_idx]

# Create env
config = EnvConfig(
    lookback=64,
    action_mode="discrete",
    reward_type="simple",
    max_episode_steps=500,
    normalize_obs=True,
)
env = ForexTradingEnv(df, config, features=features)

# Train a small agent from scratch to see if it trades
agent_config = AgentConfig(
    algorithm="PPO",
    learning_rate=3e-4,
    n_steps=512,
    batch_size=64,
    n_epochs=4,
    hidden_sizes=(64, 64),
    ent_coef=0.15,  # VERY high exploration
    verbose=0,
    seed=42,
)
agent = PPOAgent(agent_config)
agent.train(env, total_timesteps=20000)

# Check action distribution during evaluation
print("\n=== ACTION DISTRIBUTION (deterministic) ===")
action_counts = {0: 0, 1: 0, 2: 0}
obs, _ = env.reset()
done = False
for step in range(500):
    action = agent.predict(obs, deterministic=True)
    idx = int(action) if isinstance(action, (int, np.integer)) else int(action.item())
    action_counts[idx] = action_counts.get(idx, 0) + 1
    obs, reward, terminated, truncated, info = env.step(action)
    if terminated or truncated:
        break
print(f"Steps: {sum(action_counts.values())}")
print(f"HOLD(0): {action_counts.get(0, 0)}  BUY(1): {action_counts.get(1, 0)}  SELL(2): {action_counts.get(2, 0)}")
print(f"Trades: {info.get('trades_taken', 0)}")
print(f"Total P&L: ${info.get('total_pnl', 0):.2f}")

# Check action probabilities
print("\n=== ACTION PROBABILITIES (first 5 obs) ===")
obs, _ = env.reset()
for i in range(5):
    probs = agent.get_action_probabilities(obs)
    if probs.get("action_probs") is not None:
        print(f"Step {i}: probs={probs['action_probs']}")
    obs, _, term, trunc, _ = env.step(agent.predict(obs, deterministic=True))
    if term or trunc:
        break

"""End-to-end validation of forex_rl pipeline."""
import sys
import time
import numpy as np
import pandas as pd

print("=" * 60)
print("FOREX_RL END-TO-END VALIDATION")
print("=" * 60)

# Phase 1: Data Pipeline
print("\n[1/8] Data Pipeline...")
from forex_rl.src.data.loader import DataLoader
from forex_rl.src.data.cleaner import DataCleaner
from forex_rl.src.data.splitter import DataSplitter

loader = DataLoader()
df = loader.load_synthetic(symbol="EURUSD", n_bars=2000, seed=42)
print(f"  Synthetic data: {len(df)} bars, columns={list(df.columns)}")

cleaner = DataCleaner()
df = cleaner.clean(df)
print(f"  Cleaned: {len(df)} bars")

splitter = DataSplitter()
# Ratio-based split (synthetic data is only ~83 days)
n = len(df)
train_end = int(n * 0.7)
val_end = int(n * 0.85)
train_df = df.iloc[:train_end].copy()
val_df = df.iloc[train_end:val_end].copy()
test_df = df.iloc[val_end:].copy()
splits = {"train": train_df, "val": val_df, "test": test_df}
print(f"  Splits: train={len(splits['train'])}, val={len(splits['val'])}, test={len(splits['test'])}")
print("  ✓ Data Pipeline OK")

# Phase 2: Feature Engineering
print("\n[2/8] Feature Engineering...")
from forex_rl.src.market.indicators import FeatureEngine

engine = FeatureEngine()
train_df = splits["train"].copy()
train_df = engine.compute_all(train_df)
feature_names = engine.feature_names()
print(f"  Features computed: {len(feature_names)} columns")
print(f"  Feature names: {feature_names[:5]}...")
nan_ratio = train_df[feature_names].isna().mean().mean()
print(f"  NaN ratio: {nan_ratio:.3f}")
print("  ✓ Feature Engineering OK")

# Phase 3: Market Structure + Supply/Demand
print("\n[3/8] Market Structure + Supply/Demand...")
from forex_rl.src.market.structure import MarketStructure
from forex_rl.src.market.supply_demand import SupplyDemandDetector

ms = MarketStructure()
struct_features = ms.compute_structure_features(train_df)
print(f"  Structure features: {list(struct_features.columns)}")

sd = SupplyDemandDetector()
sd_features = sd.compute_sd_features(train_df)
print(f"  S/D features: {list(sd_features.columns)}")
print("  ✓ Market Structure + S/D OK")

# Phase 4: Regime Detection
print("\n[4/8] Regime Detection...")
from forex_rl.src.market.regime import RegimeDetector

rd = RegimeDetector()
regimes = rd.detect(train_df)
print(f"  Regimes detected: {regimes.value_counts().to_dict()}")
regime_features = rd.compute_regime_features(train_df)
print(f"  Regime features: {list(regime_features.columns)}")
print("  ✓ Regime Detection OK")

# Phase 5: Trading Environment
print("\n[5/8] Trading Environment...")
from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig

# Merge all features into one DataFrame
full_df = train_df.copy()
for col in struct_features.columns:
    full_df[col] = struct_features[col].values
for col in sd_features.columns:
    full_df[col] = sd_features[col].values
for col in regime_features.columns:
    full_df[col] = regime_features[col].values

# Fill NaN
full_df = full_df.ffill().fillna(0)

# Get feature columns (exclude OHLCV + time)
exclude = {"time", "open", "high", "low", "close", "volume"}
feature_cols = [c for c in full_df.columns if c not in exclude]
features_array = full_df[feature_cols].values.astype(np.float32)

config = EnvConfig(lookback=64, action_mode="discrete")
env = ForexTradingEnv(full_df, config, features=features_array, regime_labels=regimes)
print(f"  Observation space: {env.observation_space.shape}")
print(f"  Action space: {env.action_space}")

obs, info = env.reset()
print(f"  Reset OK: obs shape={obs.shape}, info keys={list(info.keys())}")

# Test 10 steps
total_reward = 0
for i in range(10):
    action = env.action_space.sample()
    obs, reward, terminated, truncated, info = env.step(action)
    total_reward += reward
    if terminated or truncated:
        obs, info = env.reset()
print(f"  10 steps: total_reward={total_reward:.4f}")
print("  ✓ Trading Environment OK")

# Phase 6: Reward Functions
print("\n[6/8] Reward Functions...")
from forex_rl.src.environment.reward import (
    SimpleProfitReward, RiskAdjustedReward, SharpeReward, Portfolio
)

portfolio = Portfolio(initial_balance=10000)
simple = SimpleProfitReward()
risk_adj = RiskAdjustedReward()
sharpe_r = SharpeReward()
print(f"  SimpleProfitReward: {type(simple).__name__}")
print(f"  RiskAdjustedReward: {type(risk_adj).__name__}")
print(f"  SharpeReward: {type(sharpe_r).__name__}")
print(f"  Portfolio stats: {portfolio.stats()}")
print("  ✓ Reward Functions OK")

# Phase 7: RL Agent (PPO)
print("\n[7/8] PPO Agent Training (100 steps)...")
from forex_rl.src.agents.ppo_agent import PPOAgent
from forex_rl.src.agents.policy import AgentConfig

agent_config = AgentConfig(
    algorithm="PPO",
    learning_rate=3e-4,
    n_steps=64,
    batch_size=32,
    n_epochs=2,
    verbose=0,
)
agent = PPOAgent(agent_config)

t0 = time.time()
result = agent.train(env, total_timesteps=100)
elapsed = time.time() - t0
print(f"  Training: {result.total_timesteps} steps in {elapsed:.1f}s")
print(f"  Mean reward: {result.mean_reward:.4f}")

# Test predict
obs, _ = env.reset()
action = agent.predict(obs, deterministic=True)
print(f"  Predict: action={action}")
print("  ✓ PPO Agent OK")

# Phase 8: Backtest
print("\n[8/8] Backtest...")
from forex_rl.src.backtest.engine import BacktestEngine, BacktestConfig
from forex_rl.src.backtest.metrics import BacktestMetrics
from forex_rl.src.backtest.monte_carlo import MonteCarloSimulator

bt_config = BacktestConfig()
bt = BacktestEngine(bt_config)
bt_result = bt.run(agent, env)
metrics = bt_result.metrics
print(f"  Trades: {metrics.get('total_trades', 0)}")
print(f"  Sharpe: {metrics.get('sharpe_ratio', 0):.2f}")
print(f"  Max DD: {metrics.get('max_drawdown', 0):.2f}%")
print(f"  Win Rate: {metrics.get('win_rate', 0):.1f}%")

mc = MonteCarloSimulator(n_simulations=500)
mc_result = mc.simulate(bt_result.trades)
print(f"  Monte Carlo median return: {mc_result.median_return:.2f}%")
print(f"  Ruin probability: {mc_result.probability_of_ruin:.3f}")
print("  ✓ Backtest OK")

print("\n" + "=" * 60)
print("ALL 8 PHASES VALIDATED SUCCESSFULLY")
print("=" * 60)

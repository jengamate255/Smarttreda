"""v3 training: 20k bars, per-symbol tuned configs, fixed trade recording.

Per-symbol settings from prior experiments:
- USDJPY: spread=1.0, sl=1.2, tp=2.5 (Variant B — PF 2.76)
- XAUUSD: ent=0.172, lr=2.4e-4, sl=2.01, inact=0.051 (Optuna best)
- EURUSD/GBPUSD: baseline + 20k steps to avoid inaction collapse
"""
import sys
import time
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd

from forex_rl.src.market.indicators import FeatureEngine
from forex_rl.src.market.structure import MarketStructure
from forex_rl.src.market.supply_demand import SupplyDemandDetector
from forex_rl.src.market.regime import RegimeDetector
from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig
from forex_rl.src.agents.ppo_agent import PPOAgent
from forex_rl.src.agents.policy import AgentConfig
from forex_rl.src.backtest.engine import BacktestEngine, BacktestConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("v3")

# Per-symbol tuning from prior validated experiments
SYMBOL_CONFIGS = {
    "USDJPY": dict(spread_pips=1.0, sl_atr_multiplier=1.2, tp_atr_multiplier=2.5,
                   inaction_penalty=0.05, ent_coef=0.15, learning_rate=3e-4, n_steps=20000),
    "XAUUSD": dict(spread_pips=1.5, sl_atr_multiplier=2.0, tp_atr_multiplier=3.0,
                   inaction_penalty=0.051, ent_coef=0.172, learning_rate=2.4e-4, n_steps=20000),
    "EURUSD": dict(spread_pips=1.5, sl_atr_multiplier=2.0, tp_atr_multiplier=3.0,
                   inaction_penalty=0.05, ent_coef=0.15, learning_rate=3e-4, n_steps=20000),
    "GBPUSD": dict(spread_pips=1.5, sl_atr_multiplier=2.0, tp_atr_multiplier=3.0,
                   inaction_penalty=0.05, ent_coef=0.15, learning_rate=3e-4, n_steps=20000),
}


def select_top_features(features, feature_names, top_n=20):
    variances = np.nan_to_num(np.nanvar(features, axis=0), nan=0.0)
    top_indices = np.sort(np.argsort(variances)[-top_n:])
    return features[:, top_indices], [feature_names[i] for i in top_indices]


def prepare_features(symbol, n_bars=20000):
    df = pd.read_parquet(f"forex_rl/data/raw/{symbol}_H1.parquet")
    if len(df) > n_bars:
        df = df.iloc[-n_bars:].copy()

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
    h4 = pd.read_parquet(f"forex_rl/data/raw/{symbol}_H4.parquet")
    h4e = FeatureEngine()
    h4f = h4e.compute_all(h4)
    h4f.index = h4f.index.tz_localize(None) if h4f.index.tz else h4f.index
    h4_sub = h4f[["ema_20", "ema_50", "rsi_14", "atr_14", "macd_histogram"]].copy()
    h4_sub.columns = ["h4_ema20", "h4_ema50", "h4_rsi14", "h4_atr14", "h4_macd_hist"]
    h4_aligned = h4_sub.reindex(df.index, method="ffill").fillna(0)
    for col in h4_aligned.columns:
        df[col] = h4_aligned[col].values

    df = df.reset_index(drop=True)
    exclude = {"time", "open", "high", "low", "close", "volume"}
    feature_cols = [c for c in df.columns if c not in exclude]
    features = np.nan_to_num(df[feature_cols].values.astype(np.float32), nan=0.0)
    features, feature_cols = select_top_features(features, feature_cols, 20)
    logger.info("%s: %d bars, %d features", symbol, len(features), features.shape[1])
    return df, features, feature_cols


def train_symbol(symbol, n_bars=20000):
    cfg = SYMBOL_CONFIGS[symbol]
    t0 = time.time()

    df, features, names = prepare_features(symbol, n_bars)

    n = len(features)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)

    env_config = EnvConfig(
        lookback=64,
        action_mode="discrete",
        reward_type="simple",
        max_episode_steps=1000,  # longer episodes with more data
        normalize_obs=True,
        spread_pips=cfg["spread_pips"],
        sl_atr_multiplier=cfg["sl_atr_multiplier"],
        tp_atr_multiplier=cfg["tp_atr_multiplier"],
        inaction_penalty=cfg["inaction_penalty"],
    )

    train_env = ForexTradingEnv(df.iloc[:train_end].reset_index(drop=True), env_config, features=features[:train_end])
    val_env = ForexTradingEnv(df.iloc[train_end:val_end].reset_index(drop=True), env_config, features=features[train_end:val_end])
    test_env = ForexTradingEnv(df.iloc[val_end:].reset_index(drop=True), env_config, features=features[val_end:])

    agent_config = AgentConfig(
        algorithm="PPO",
        learning_rate=cfg["learning_rate"],
        n_steps=512,
        batch_size=64,
        n_epochs=4,
        hidden_sizes=(64, 64),
        ent_coef=cfg["ent_coef"],
        gamma=0.995,
        verbose=0,
        seed=42,
    )

    # Synthetic warmup
    from forex_rl.scripts.train_v2 import create_synthetic_trending_env
    warmup_env = create_synthetic_trending_env(2000)
    warmup_agent = PPOAgent(agent_config)
    warmup_agent.train(warmup_env, total_timesteps=5000)

    # Fine-tune
    agent = PPOAgent(agent_config)
    agent.model = agent._build_model(train_env)
    agent.model.policy.load_state_dict(warmup_agent.model.policy.state_dict(), strict=False)
    result = agent.train(train_env, total_timesteps=cfg["n_steps"])
    logger.info("%s train done: reward=%.4f (%.0fs)", symbol, result.mean_reward, result.training_time)

    # Evaluate + backtest
    val_result = agent.evaluate(val_env, n_episodes=10)
    bt = BacktestEngine(BacktestConfig()).run(agent, test_env)
    m = bt.metrics

    logger.info("─── %s TEST (20k bars) ───", symbol)
    for k in ["num_trades", "net_profit", "sharpe_ratio", "profit_factor", "max_drawdown", "win_rate"]:
        logger.info("  %s: %s", k, m.get(k, 0))

    out = Path("forex_rl/models/checkpoints")
    agent.save(str(out / f"ppo_{symbol.lower()}_h1_v3"))
    res = {
        "symbol": symbol, "bars": n, "train_bars": train_end,
        "val_reward": val_result.mean_reward, "val_sharpe": val_result.mean_sharpe,
        "test_metrics": {k: (v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))
                         for k, v in m.items() if not isinstance(v, (list, dict))},
        "elapsed": time.time() - t0,
    }
    with open(out / f"ppo_{symbol.lower()}_h1_v3_results.json", "w") as f:
        json.dump(res, f, indent=2, default=str)
    return res


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
    n_bars = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    r = train_symbol(symbol, n_bars)
    print(json.dumps(r, indent=2, default=str))

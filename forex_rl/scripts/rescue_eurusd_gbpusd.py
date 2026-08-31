"""Rescue training for EURUSD/GBPUSD (collapse to inaction on 20k bars).

Diagnosis: on 20k bars with ent_coef=0.15 and 20k train steps, the policy
converges to hold. v2 evidence: 5k bars + 20k steps worked (trades > 0).

Rescue variants per symbol (each 5k bars — the regime that worked):
- R1: 10k steps, inaction_penalty=0.05 (v2 config, more data)
- R2: 10k steps, inaction_penalty=0.08 (harsher anti-inaction)

Success: trades >= 5 on the 750-bar test window (vs 0-6 before).
"""
import sys
import time
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np

from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig
from forex_rl.src.agents.ppo_agent import PPOAgent
from forex_rl.src.agents.policy import AgentConfig
from forex_rl.src.backtest.engine import BacktestEngine, BacktestConfig
from forex_rl.scripts.train_v3 import prepare_features
from forex_rl.scripts.train_v2 import create_synthetic_trending_env

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rescue")

VARIANTS = {
    "R1": dict(inaction_penalty=0.05, train_steps=10000, ent_coef=0.15),
    "R2": dict(inaction_penalty=0.08, train_steps=10000, ent_coef=0.15),
}


def run_variant(symbol: str, tag: str, n_bars: int = 5000, seed: int = 42) -> dict:
    v = VARIANTS[tag]
    t0 = time.time()

    df, feats, names = prepare_features(symbol, n_bars)

    n = len(feats)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)

    env_config = EnvConfig(
        lookback=64,
        action_mode="discrete",
        reward_type="simple",
        max_episode_steps=500,
        normalize_obs=True,
        spread_pips=1.5,
        sl_atr_multiplier=2.0,
        tp_atr_multiplier=3.0,
        inaction_penalty=v["inaction_penalty"],
    )

    train_env = ForexTradingEnv(df.iloc[:train_end].reset_index(drop=True), env_config, features=feats[:train_end])
    val_env = ForexTradingEnv(df.iloc[train_end:val_end].reset_index(drop=True), env_config, features=feats[train_end:val_end])
    test_env = ForexTradingEnv(df.iloc[val_end:].reset_index(drop=True), env_config, features=feats[val_end:])

    agent_config = AgentConfig(
        algorithm="PPO",
        learning_rate=3e-4,
        n_steps=512,
        batch_size=64,
        n_epochs=4,
        hidden_sizes=(64, 64),
        ent_coef=v["ent_coef"],
        gamma=0.995,
        verbose=0,
        seed=seed,
    )

    warmup_env = create_synthetic_trending_env(2000)
    warmup_agent = PPOAgent(agent_config)
    warmup_agent.train(warmup_env, total_timesteps=5000)

    agent = PPOAgent(agent_config)
    agent.model = agent._build_model(train_env)
    agent.model.policy.load_state_dict(warmup_agent.model.policy.state_dict(), strict=False)
    agent.train(train_env, total_timesteps=v["train_steps"])

    bt = BacktestEngine(BacktestConfig()).run(agent, test_env)
    m = bt.metrics

    res = {
        "symbol": symbol, "variant": tag, "bars": n,
        "inaction_penalty": v["inaction_penalty"],
        "trades": m.get("num_trades", 0),
        "net_profit": m.get("net_profit", 0.0),
        "sharpe": m.get("sharpe_ratio", 0.0),
        "pf": m.get("profit_factor", 0.0),
        "max_dd": m.get("max_drawdown", 0.0),
        "win_rate": m.get("win_rate", 0.0),
        "elapsed": time.time() - t0,
    }
    logger.info("%s %s: trades=%d net=$%.2f sharpe=%.3f pf=%.3f dd=%.2f%% wr=%.1f%%",
                symbol, tag, res["trades"], res["net_profit"], res["sharpe"],
                res["pf"], res["max_dd"], res["win_rate"])
    return res


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
    results = []
    for tag in VARIANTS:
        results.append(run_variant(symbol, tag))
    out = Path("forex_rl/models/checkpoints") / f"{symbol.lower()}_rescue_results.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(json.dumps(results, indent=2, default=str))

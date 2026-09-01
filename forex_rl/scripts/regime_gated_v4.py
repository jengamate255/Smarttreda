"""Regime-gated entry experiment (v4).

Hypothesis: the flat inaction penalty forces trading in ALL regimes
(including ranges where the agent has no edge) — manufacturing activity,
not alpha. Gating by regime should:
- trade MORE in trends (where P&L lives)
- sit out ranges (patience rewarded instead of punished)
- avoid counter-trend positions

Design: identical to train_v3 except reward_type="regime_gated" and
feature_names passed to the env. Each symbol runs BOTH baseline (simple)
and gated arms on the same splits/seed for a paired comparison.

Success bar (same as promotion bar): trades >= 8, PF >= 1.3, net >= $0,
and gated arm must beat its paired baseline on PF.
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
from forex_rl.scripts.train_v3 import prepare_features, SYMBOL_CONFIGS
from forex_rl.scripts.train_v2 import create_synthetic_trending_env

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("regime_v4")

TRAIN_STEPS = 20000
WARMUP_STEPS = 5000


def run_arm(symbol: str, df, feats, names, reward_type: str, cfg: dict, seed: int = 42) -> dict:
    n = len(feats)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)

    env_config = EnvConfig(
        lookback=64,
        action_mode="discrete",
        reward_type=reward_type,
        max_episode_steps=1000,
        normalize_obs=True,
        spread_pips=cfg["spread_pips"],
        sl_atr_multiplier=cfg["sl_atr_multiplier"],
        tp_atr_multiplier=cfg["tp_atr_multiplier"],
        inaction_penalty=cfg["inaction_penalty"],
        pip_size=cfg.get("pip_size", 0.0001),
        contract_size=cfg.get("contract_size", 100000.0),
        point_value=cfg.get("point_value", 10.0),
    )

    train_env = ForexTradingEnv(
        df.iloc[:train_end].reset_index(drop=True), env_config,
        features=feats[:train_end], feature_names=names,
    )
    test_env = ForexTradingEnv(
        df.iloc[val_end:].reset_index(drop=True), env_config,
        features=feats[val_end:], feature_names=names,
    )
    test_env.set_eval_mode(start_step=env_config.lookback)

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
        seed=seed,
    )

    # Synthetic warmup
    warmup_env = create_synthetic_trending_env(2000)
    warmup_agent = PPOAgent(agent_config)
    warmup_agent.train(warmup_env, total_timesteps=WARMUP_STEPS)

    agent = PPOAgent(agent_config)
    agent.model = agent._build_model(train_env)
    agent.model.policy.load_state_dict(warmup_agent.model.policy.state_dict(), strict=False)
    agent.train(train_env, total_timesteps=TRAIN_STEPS)

    bt = BacktestEngine(BacktestConfig()).run(agent, test_env)
    m = bt.metrics
    return {
        "trades": m.get("num_trades", 0),
        "net_profit": m.get("net_profit", 0.0),
        "sharpe": m.get("sharpe_ratio", 0.0),
        "pf": m.get("profit_factor", 0.0),
        "max_dd": m.get("max_drawdown", 0.0),
        "win_rate": m.get("win_rate", 0.0),
    }


def run_experiment(symbol: str, n_bars: int = 20000) -> dict:
    cfg = SYMBOL_CONFIGS[symbol]
    df, feats, names = prepare_features(symbol, n_bars)

    logger.info("── %s BASELINE arm (simple reward) ──", symbol)
    t0 = time.time()
    base = run_arm(symbol, df, feats, names, "simple", cfg)
    logger.info("%s baseline: %s", symbol, base)

    logger.info("── %s GATED arm (regime_gated reward) ──", symbol)
    gated = run_arm(symbol, df, feats, names, "regime_gated", cfg)
    logger.info("%s gated: %s", symbol, gated)

    result = {
        "symbol": symbol,
        "baseline": base,
        "regime_gated": gated,
        "pf_improvement": round(gated["pf"] - base["pf"], 3),
        "net_improvement": round(gated["net_profit"] - base["net_profit"], 2),
        "gated_beats_baseline": bool(gated["pf"] > base["pf"] and gated["net_profit"] > base["net_profit"]),
        "passes_bar": bool(gated["trades"] >= 8 and gated["pf"] >= 1.3 and gated["net_profit"] >= 0),
        "elapsed": round(time.time() - t0, 1),
    }
    logger.info("=" * 60)
    logger.info("%s: baseline PF=%.3f net=$%.2f | gated PF=%.3f net=$%.2f | beats=%s passes=%s",
                symbol, base["pf"], base["net_profit"], gated["pf"], gated["net_profit"],
                result["gated_beats_baseline"], result["passes_bar"])
    return result


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
    r = run_experiment(symbol)
    out = Path("forex_rl/models/checkpoints") / f"{symbol.lower()}_regime_gated_v4.json"
    with open(out, "w") as f:
        json.dump(r, f, indent=2, default=str)
    print(json.dumps(r, indent=2, default=str))

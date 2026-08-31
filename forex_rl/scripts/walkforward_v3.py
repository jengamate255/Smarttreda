"""v3 walk-forward validation with FIXED trade recording.

Expanding-window folds over 20k bars:
  Fold 0: train 0:10000   test 10000:14000
  Fold 1: train 0:14000   test 14000:17000
  Fold 2: train 0:17000   test 17000:20000

Per-symbol tuned EnvConfigs from v3 (USDJPY Variant B, XAUUSD Optuna best).
Aggregates OOS: mean Sharpe/PF, stability, worst DD, total trades, net P&L.
Promotion bar (research standard): trades >= 8, PF >= 1.3, net >= $0.
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wf_v3")

FOLDS = [
    (0, 10000, 14000),
    (0, 14000, 17000),
    (0, 17000, 20000),
]

WARMUP_STEPS = 5000
TRAIN_STEPS = 15000


def run_walk_forward(symbol: str, n_bars: int = 20000, seed: int = 42) -> dict:
    cfg = SYMBOL_CONFIGS[symbol]
    t0 = time.time()

    df, feats, names = prepare_features(symbol, n_bars)

    env_config = EnvConfig(
        lookback=64,
        action_mode="discrete",
        reward_type="simple",
        max_episode_steps=1000,
        normalize_obs=True,
        spread_pips=cfg["spread_pips"],
        sl_atr_multiplier=cfg["sl_atr_multiplier"],
        tp_atr_multiplier=cfg["tp_atr_multiplier"],
        inaction_penalty=cfg["inaction_penalty"],
    )
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

    # Synthetic warmup once (shared weights across folds)
    from forex_rl.scripts.train_v2 import create_synthetic_trending_env
    warmup_env = create_synthetic_trending_env(2000)
    warmup_agent = PPOAgent(agent_config)
    warmup_agent.train(warmup_env, total_timesteps=WARMUP_STEPS)
    warmup_state = warmup_agent.model.policy.state_dict()

    fold_metrics = []
    for i, (tr_s, tr_e, te_e) in enumerate(FOLDS):
        logger.info("── %s Fold %d: train [%d:%d] test [%d:%d] ──", symbol, i, tr_s, tr_e, tr_e, te_e)
        train_env = ForexTradingEnv(
            df.iloc[tr_s:tr_e].reset_index(drop=True), env_config, features=feats[tr_s:tr_e]
        )
        test_env = ForexTradingEnv(
            df.iloc[tr_e:te_e].reset_index(drop=True), env_config, features=feats[tr_e:te_e]
        )

        agent = PPOAgent(agent_config)
        agent.model = agent._build_model(train_env)
        agent.model.policy.load_state_dict(warmup_state, strict=False)
        agent.train(train_env, total_timesteps=TRAIN_STEPS)

        bt = BacktestEngine(BacktestConfig()).run(agent, test_env)
        m = bt.metrics
        fold_metrics.append({
            "fold": i,
            "trades": m.get("num_trades", 0),
            "net_profit": m.get("net_profit", 0.0),
            "sharpe": m.get("sharpe_ratio", 0.0),
            "pf": m.get("profit_factor", 0.0),
            "max_dd": m.get("max_drawdown", 0.0),
            "win_rate": m.get("win_rate", 0.0),
        })
        logger.info("Fold %d: trades=%d net=$%.2f sharpe=%.3f pf=%.3f dd=%.2f%%",
                    i, m.get("num_trades", 0), m.get("net_profit", 0.0),
                    m.get("sharpe_ratio", 0.0), m.get("profit_factor", 0.0),
                    m.get("max_drawdown", 0.0))

    sharpes = [f["sharpe"] for f in fold_metrics]
    aggregate = {
        "symbol": symbol,
        "folds": fold_metrics,
        "mean_sharpe": float(np.mean(sharpes)),
        "stability_std_sharpe": float(np.std(sharpes)),
        "mean_pf": float(np.mean([f["pf"] for f in fold_metrics])),
        "worst_dd_pct": float(max(f["max_dd"] for f in fold_metrics)),
        "total_trades": int(sum(f["trades"] for f in fold_metrics)),
        "total_net": float(sum(f["net_profit"] for f in fold_metrics)),
        "mean_win_rate": float(np.mean([f["win_rate"] for f in fold_metrics])),
        "elapsed": time.time() - t0,
    }
    aggregate["passes_bar"] = bool(
        aggregate["total_trades"] >= 8
        and aggregate["mean_pf"] >= 1.3
        and aggregate["total_net"] >= 0
    )

    out = Path("forex_rl/models/checkpoints") / f"{symbol.lower()}_walkforward_v3.json"
    with open(out, "w") as f:
        json.dump(aggregate, f, indent=2, default=str)

    logger.info("=" * 60)
    logger.info("%s WALK-FORWARD AGGREGATE", symbol)
    logger.info("  Mean Sharpe: %.3f  (std %.3f)", aggregate["mean_sharpe"], aggregate["stability_std_sharpe"])
    logger.info("  Mean PF: %.3f   Worst DD: %.2f%%", aggregate["mean_pf"], aggregate["worst_dd_pct"])
    logger.info("  Total trades: %d  Net: $%.2f", aggregate["total_trades"], aggregate["total_net"])
    logger.info("  PASSES PROMOTION BAR: %s", aggregate["passes_bar"])
    return aggregate


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "USDJPY"
    r = run_walk_forward(symbol)
    print(json.dumps(r, indent=2, default=str))

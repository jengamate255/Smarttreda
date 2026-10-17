"""Full training pipeline: real MT5 data + MTF features + PPO + walk-forward.

Usage:
    python forex_rl/scripts/train_real.py --symbol EURUSD --algorithm PPO
    python forex_rl/scripts/train_real.py --symbol XAUUSD --algorithm SAC --optuna
"""
import sys
import os
import argparse
import time
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd

from forex_rl.src.data.loader import DataLoader
from forex_rl.src.data.cleaner import DataCleaner
from forex_rl.src.market.mtf_features import prepare_mtf_features
from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig
from forex_rl.src.agents.ppo_agent import PPOAgent
from forex_rl.src.agents.sac_agent import SACAgent
from forex_rl.src.agents.policy import AgentConfig
from forex_rl.src.backtest.engine import BacktestEngine, BacktestConfig
from forex_rl.src.backtest.metrics import BacktestMetrics
from forex_rl.src.backtest.monte_carlo import MonteCarloSimulator
from forex_rl.src.training.evaluation import Evaluator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("train_real")


def train_symbol(
    symbol: str,
    algorithm: str = "PPO",
    timeframe: str = "H1",
    htf_list: list[str] = None,
    n_steps: int = 100_000,
    eval_episodes: int = 20,
    seed: int = 42,
    output_dir: str = "forex_rl/models/checkpoints",
) -> dict:
    """Full training pipeline for a single symbol."""
    if htf_list is None:
        htf_list = ["H4"]

    t0 = time.time()
    logger.info("=" * 60)
    logger.info("TRAINING %s %s [%s] seed=%d", symbol, algorithm, timeframe, seed)
    logger.info("=" * 60)

    # ── 1. Load and prepare features ────────────────────────────────────
    logger.info("[1/6] Loading data and computing features...")
    merged_df, features, feature_names = prepare_mtf_features(
        symbol, base_tf=timeframe, htf_list=htf_list
    )
    logger.info("  Merged DataFrame: %d bars, %d features", len(features), features.shape[1])

    # ── 2. Split: 70% train, 15% val, 15% test ─────────────────────────
    logger.info("[2/6] Splitting data...")
    n = len(features)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)

    train_features = features[:train_end]
    val_features = features[train_end:val_end]
    test_features = features[val_end:]

    train_df = merged_df.iloc[:train_end].reset_index(drop=True)
    val_df = merged_df.iloc[train_end:val_end].reset_index(drop=True)
    test_df = merged_df.iloc[val_end:].reset_index(drop=True)

    logger.info("  Train: %d bars | Val: %d bars | Test: %d bars",
                len(train_features), len(val_features), len(test_features))

    # ── 3. Create environments ──────────────────────────────────────────
    logger.info("[3/6] Creating environments...")
    env_config = EnvConfig(
        lookback=64,
        action_mode="discrete",
        reward_type="risk_adjusted",
        risk_per_trade=0.005,
        sl_atr_multiplier=2.0,
        tp_atr_multiplier=3.0,
        max_episode_steps=min(1000, train_end - 64),
        normalize_obs=True,
    )

    train_env = ForexTradingEnv(train_df, env_config, features=train_features)
    val_env = ForexTradingEnv(val_df, env_config, features=val_features)
    test_env = ForexTradingEnv(test_df, env_config, features=test_features)

    logger.info("  Obs shape: %s | Action space: %s",
                train_env.observation_space.shape, train_env.action_space)

    # ── 4. Create and train agent ───────────────────────────────────────
    logger.info("[4/6] Training %s agent (%d timesteps)...", algorithm, n_steps)
    agent_config = AgentConfig(
        algorithm=algorithm,
        learning_rate=3e-4,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.01,
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        hidden_sizes=(256, 256),
        verbose=0,
        seed=seed,
    )

    if algorithm == "PPO":
        agent = PPOAgent(agent_config)
    elif algorithm == "SAC":
        agent = SACAgent(agent_config)
    else:
        raise ValueError(f"Unknown algorithm: {algorithm}")

    train_result = agent.train(train_env, total_timesteps=n_steps)
    logger.info("  Training done: %.1fs, mean_reward=%.4f",
                train_result.training_time, train_result.mean_reward)

    # ── 5. Evaluate on validation set ───────────────────────────────────
    logger.info("[5/6] Evaluating on validation set (%d episodes)...", eval_episodes)
    val_result = agent.evaluate(val_env, n_episodes=eval_episodes)
    logger.info("  Val: reward=%.4f±%.4f, sharpe=%.2f, max_dd=%.2f%%, trades=%.1f",
                val_result.mean_reward, val_result.std_reward,
                val_result.mean_sharpe, val_result.mean_max_dd, val_result.mean_trades)

    # ── 6. Backtest on test set ─────────────────────────────────────────
    logger.info("[6/6] Backtesting on test set...")
    bt_config = BacktestConfig(
        initial_balance=10000,
        spread_pips=env_config.spread_pips,
        slippage_pips=env_config.slippage_pips,
        commission_per_lot=env_config.commission_per_lot,
        risk_per_trade=env_config.risk_per_trade,
    )
    bt_engine = BacktestEngine(bt_config)
    bt_result = bt_engine.run(agent, test_env)
    metrics = bt_result.metrics

    logger.info("  ── TEST RESULTS ──")
    logger.info("  Total trades: %d", metrics.get("total_trades", 0))
    logger.info("  Net profit: $%.2f", metrics.get("net_profit", 0))
    logger.info("  Sharpe ratio: %.3f", metrics.get("sharpe_ratio", 0))
    logger.info("  Sortino ratio: %.3f", metrics.get("sortino_ratio", 0))
    logger.info("  Max drawdown: %.2f%%", metrics.get("max_drawdown", 0))
    logger.info("  Profit factor: %.3f", metrics.get("profit_factor", 0))
    logger.info("  Win rate: %.1f%%", metrics.get("win_rate", 0))
    logger.info("  Expectancy: $%.2f", metrics.get("expectancy", 0))

    # ── Monte Carlo ─────────────────────────────────────────────────────
    if bt_result.trades:
        mc = MonteCarloSimulator(n_simulations=2000, seed=seed)
        mc_result = mc.simulate(bt_result.trades)
        logger.info("  Monte Carlo median return: %.2f%%", mc_result.median_return)
        logger.info("  Ruin probability: %.3f", mc_result.probability_of_ruin)
        logger.info("  5th percentile: %.2f%% | 95th: %.2f%%",
                     mc_result.percentile_5, mc_result.percentile_95)

    # ── Save model ──────────────────────────────────────────────────────
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    model_path = out_path / f"{algorithm.lower()}_{symbol.lower()}_{timeframe}"
    agent.save(str(model_path))
    logger.info("  Model saved to %s", model_path)

    # ── Save results ────────────────────────────────────────────────────
    results = {
        "symbol": symbol,
        "algorithm": algorithm,
        "timeframe": timeframe,
        "htf_list": htf_list,
        "seed": seed,
        "n_steps": n_steps,
        "features_count": features.shape[1],
        "feature_names": feature_names,
        "train_bars": len(train_features),
        "val_bars": len(val_features),
        "test_bars": len(test_features),
        "training_time": train_result.training_time,
        "val_reward_mean": val_result.mean_reward,
        "val_sharpe": val_result.mean_sharpe,
        "val_max_dd": val_result.mean_max_dd,
        "test_metrics": {k: v for k, v in metrics.items()
                         if isinstance(v, (int, float, str))},
        "elapsed_seconds": time.time() - t0,
    }

    results_path = out_path / f"{algorithm.lower()}_{symbol.lower()}_{timeframe}_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info("  Results saved to %s", results_path)

    # ── Summary ─────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info("DONE: %s %s — %d trades, Sharpe=%.2f, PF=%.2f, DD=%.2f%% — %.0fs",
                symbol, algorithm,
                metrics.get("total_trades", 0),
                metrics.get("sharpe_ratio", 0),
                metrics.get("profit_factor", 0),
                metrics.get("max_drawdown", 0),
                elapsed)
    logger.info("=" * 60)

    return results


def main():
    parser = argparse.ArgumentParser(description="Train forex RL agent on real MT5 data")
    parser.add_argument("--symbol", default="EURUSD", help="Symbol to trade")
    parser.add_argument("--algorithm", default="PPO", choices=["PPO", "SAC"])
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--htf", default="H4", help="Higher timeframe for MTF features")
    parser.add_argument("--steps", type=int, default=100_000, help="Training timesteps")
    parser.add_argument("--eval-episodes", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="forex_rl/models/checkpoints")
    args = parser.parse_args()

    results = train_symbol(
        symbol=args.symbol,
        algorithm=args.algorithm,
        timeframe=args.timeframe,
        htf_list=[args.htf],
        n_steps=args.steps,
        eval_episodes=args.eval_episodes,
        seed=args.seed,
        output_dir=args.output,
    )

    print("\n" + "=" * 60)
    print(f"RESULTS: {results['symbol']} {results['algorithm']}")
    print(f"  Features: {results['features_count']}")
    print(f"  Val Sharpe: {results['val_sharpe']:.3f}")
    print(f"  Test Sharpe: {results['test_metrics'].get('sharpe_ratio', 0):.3f}")
    print(f"  Test PF: {results['test_metrics'].get('profit_factor', 0):.3f}")
    print(f"  Test MaxDD: {results['test_metrics'].get('max_drawdown', 0):.2f}%")
    print(f"  Time: {results['elapsed_seconds']:.0f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()

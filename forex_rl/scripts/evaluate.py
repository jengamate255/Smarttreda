"""Evaluate a trained RL model.

Usage:
    python scripts/evaluate.py --model models/ppo_eurusd.zip --symbol EURUSD
    python scripts/evaluate.py --model models/sac_xauusd.zip --symbol XAUUSD --episodes 50
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    config_path = PROJECT_ROOT / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


def load_data(symbol: str, timeframe: str, split: str = "val") -> "pd.DataFrame":
    import pandas as pd

    data_path = PROJECT_ROOT / "data" / "raw" / f"{symbol}_{timeframe}.parquet"
    config = load_config()

    if data_path.exists():
        df = pd.read_parquet(data_path)
        logger.info("Loaded %d bars from %s", len(df), data_path)
    else:
        logger.info("No local data, generating synthetic for %s %s", symbol, timeframe)
        from forex_rl.src.data.loader import DataLoader
        loader = DataLoader()
        dates_cfg = config.get(f"{split}_dates", config.get("test_dates", {}))
        start = dates_cfg.get("start", "2024-01-01")
        end = dates_cfg.get("end", "2025-12-31")
        df = loader.load_from_mt5(symbol, timeframe, start, end)

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.sort_index(inplace=True)
    return df


def load_agent(model_path: str):
    from stable_baselines3 import PPO, SAC

    path = Path(model_path)
    if not path.exists():
        raise FileNotFoundError(f"Model not found: {path}")

    stem = path.stem.lower()
    if "ppo" in stem:
        agent = PPO.load(str(path))
        logger.info("Loaded PPO model: %s", path)
    elif "sac" in stem:
        agent = SAC.load(str(path))
        logger.info("Loaded SAC model: %s", path)
    else:
        agent = PPO.load(str(path))
        logger.info("Loaded model (default PPO): %s", path)
    return agent


def create_env(df, config: dict, lookback: int = 64):
    from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig

    env_config = EnvConfig(
        lookback=lookback,
        initial_balance=config.get("Initial_balance", 10000.0),
        risk_per_trade=config.get("risk_per_trade", 0.005),
    )
    env = ForexTradingEnv(df, config=env_config)
    env.set_eval_mode()
    return env


def run_evaluation(agent, env, n_episodes: int = 30) -> dict:
    import numpy as np

    rewards = []
    trade_counts = []
    win_counts = []
    equity_curves = []
    max_drawdowns = []

    for ep in range(n_episodes):
        obs, _ = env.reset()
        done = False
        ep_reward = 0.0
        trades = 0
        wins = 0
        equity_curve = [env.cfg.initial_balance]

        while not done:
            action, _ = agent.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            ep_reward += float(reward)

            current_eq = info.get("equity", equity_curve[-1])
            equity_curve.append(current_eq)

            if info.get("n_positions", 0) > 0 and env.episode_step > 0:
                pass
            if terminated and info.get("total_trades", 0) > trades:
                new_trades = info["total_trades"] - trades
                trades = info["total_trades"]
                wins = info.get("win_rate", 0) * trades

        rewards.append(ep_reward)
        trade_counts.append(trades)
        win_counts.append(wins / max(trades, 1))
        equity_curves.append(equity_curve)

        eq = np.array(equity_curve)
        running_max = np.maximum.accumulate(eq)
        dd = (running_max - eq) / np.where(running_max > 0, running_max, 1.0)
        max_drawdowns.append(float(np.max(dd)) if len(dd) > 0 else 0.0)

    eq_final = equity_curves[-1] if equity_curves else [10000.0]
    eq_arr = np.array(eq_final)
    total_return = (eq_arr[-1] - eq_arr[0]) / eq_arr[0] if eq_arr[0] > 0 else 0.0
    returns = np.diff(eq_arr) / np.where(eq_arr[:-1] > 0, eq_arr[:-1], 1.0)
    sharpe = (
        float(np.mean(returns) / np.std(returns) * np.sqrt(252))
        if len(returns) > 1 and np.std(returns) > 0 else 0.0
    )

    return {
        "n_episodes": n_episodes,
        "mean_reward": float(np.mean(rewards)),
        "std_reward": float(np.std(rewards)),
        "min_reward": float(np.min(rewards)),
        "max_reward": float(np.max(rewards)),
        "mean_trades": float(np.mean(trade_counts)),
        "mean_win_rate": float(np.mean(win_counts)),
        "sharpe_ratio": sharpe,
        "mean_max_drawdown": float(np.mean(max_drawdowns)),
        "total_return": float(total_return),
        "final_equity": float(eq_arr[-1]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a trained RL model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--model", type=str, required=True,
        help="Path to trained model file (.zip).",
    )
    parser.add_argument(
        "--symbol", type=str, default="EURUSD",
        help="Symbol to evaluate on (default: EURUSD).",
    )
    parser.add_argument(
        "--timeframe", type=str, default="H1",
        help="Bar timeframe (default: H1).",
    )
    parser.add_argument(
        "--episodes", type=int, default=30,
        help="Number of evaluation episodes (default: 30).",
    )
    parser.add_argument(
        "--lookback", type=int, default=64,
        help="Observation lookback window (default: 64).",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSON path. Default: reports/eval_{symbol}.json.",
    )
    args = parser.parse_args()

    config = load_config()

    logger.info("Loading data: %s %s (test split)", args.symbol, args.timeframe)
    df = load_data(args.symbol, args.timeframe, "test")
    logger.info("Test bars: %d", len(df))

    agent = load_agent(args.model)
    env = create_env(df, config, args.lookback)

    logger.info("Running %d evaluation episodes...", args.episodes)
    start = time.time()
    results = run_evaluation(agent, env, args.episodes)
    elapsed = time.time() - start

    results["elapsed_seconds"] = elapsed
    results["model_path"] = args.model
    results["symbol"] = args.symbol
    results["timeframe"] = args.timeframe

    output_path = args.output or str(
        PROJECT_ROOT / "reports" / f"eval_{args.symbol.lower()}.json"
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    logger.info("Evaluation results saved to %s", output_path)
    print("\n" + "=" * 60)
    print("EVALUATION RESULTS")
    print("=" * 60)
    for key, val in results.items():
        if isinstance(val, float):
            print(f"  {key:30s}: {val:.4f}")
        else:
            print(f"  {key:30s}: {val}")
    print("=" * 60)


if __name__ == "__main__":
    main()

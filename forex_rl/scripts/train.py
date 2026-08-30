"""Train an RL agent for forex trading.

Usage:
    python scripts/train.py --algorithm PPO --symbol EURUSD --timeframe H1 --episodes 200
    python scripts/train.py --algorithm SAC --symbol EURUSD --timeframe H4 --timesteps 500000
    python scripts/train.py --symbol XAUUSD --lookback 128
"""

from __future__ import annotations

import argparse
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


def load_data(symbol: str, timeframe: str, split: str = "train") -> "pd.DataFrame":
    import pandas as pd
    from forex_rl.src.utils.config import TrainDates

    config = load_config()
    data_path = PROJECT_ROOT / "data" / "raw" / f"{symbol}_{timeframe}.parquet"

    if data_path.exists():
        df = pd.read_parquet(data_path)
        logger.info("Loaded %d bars from %s", len(df), data_path)
    else:
        logger.info("No local data for %s %s, generating synthetic data", symbol, timeframe)
        from forex_rl.src.data.loader import DataLoader
        loader = DataLoader()
        dates_cfg = config.get(f"{split}_dates", config.get("train_dates", {}))
        start = dates_cfg.get("start", "2018-01-01")
        end = dates_cfg.get("end", "2022-12-31")
        df = loader.load_from_mt5(symbol, timeframe, start, end)

    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.sort_index(inplace=True)
    return df


def create_env(df, config: dict, lookback: int = 64):
    from forex_rl.src.environment.forex_env import ForexTradingEnv, EnvConfig

    env_config = EnvConfig(
        lookback=lookback,
        initial_balance=config.get("Initial_balance", 10000.0),
        risk_per_trade=config.get("risk_per_trade", 0.005),
        max_positions=config.get("maximum_positions", 3),
        spread_pips=config.get("spread_pips", 1.5),
        slippage_pips=config.get("slippage_pips", 0.5),
        commission_per_lot=config.get("commission_per_lot", 7.0),
    )
    return ForexTradingEnv(df, config=env_config)


def create_agent(algorithm: str, env, config: dict):
    from forex_rl.src.agents.factory import AgentFactory
    from forex_rl.src.agents.policy import AgentConfig

    rl_cfg = config.get("rl", {})
    agent_config = AgentConfig(
        algorithm=algorithm,
        learning_rate=rl_cfg.get("learning_rate", 3e-4),
        gamma=rl_cfg.get("gamma", 0.99),
        batch_size=rl_cfg.get("batch_size", 64),
        n_steps=rl_cfg.get("n_steps", 2048),
        ent_coef=rl_cfg.get("entropy_coeff", 0.01),
        clip_range=rl_cfg.get("clip_range", 0.2),
        hidden_sizes=(rl_cfg.get("hidden_size", 256), rl_cfg.get("hidden_size", 256)),
        tensorboard_log=str(PROJECT_ROOT / "experiments" / "tensorboard"),
        verbose=1,
    )
    return AgentFactory.create(agent_config)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train an RL agent for forex trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--algorithm", type=str, default="PPO",
        choices=["PPO", "SAC"],
        help="RL algorithm (default: PPO).",
    )
    parser.add_argument(
        "--symbol", type=str, default="EURUSD",
        help="Symbol to train on (default: EURUSD).",
    )
    parser.add_argument(
        "--timeframe", type=str, default="H1",
        help="Bar timeframe (default: H1).",
    )
    parser.add_argument(
        "--episodes", type=int, default=200,
        help="Number of training episodes (used for timesteps calc).",
    )
    parser.add_argument(
        "--timesteps", type=int, default=None,
        help="Total timesteps (overrides --episodes).",
    )
    parser.add_argument(
        "--lookback", type=int, default=64,
        help="Observation lookback window (default: 64).",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed (default: 42).",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output model path. Default: models/{algo}_{symbol}.zip.",
    )
    args = parser.parse_args()

    config = load_config()
    config["rl"] = config.get("rl", {})

    logger.info("Loading data: %s %s", args.symbol, args.timeframe)
    df_train = load_data(args.symbol, args.timeframe, "train")
    df_val = load_data(args.symbol, args.timeframe, "val")

    logger.info("Train bars: %d | Val bars: %d", len(df_train), len(df_val))

    env_train = create_env(df_train, config, args.lookback)
    env_val = create_env(df_val, config, args.lookback)

    total_timesteps = args.timesteps or (args.episodes * env_train.cfg.max_episode_steps)
    logger.info(
        "Training: algo=%s symbol=%s tf=%s timesteps=%d lookback=%d seed=%d",
        args.algorithm, args.symbol, args.timeframe,
        total_timesteps, args.lookback, args.seed,
    )

    agent = create_agent(args.algorithm, env_train, config)

    start_time = time.time()
    result = agent.train(
        env=env_train,
        total_timesteps=total_timesteps,
        eval_env=env_val,
    )
    elapsed = time.time() - start_time

    output_path = args.output or str(
        PROJECT_ROOT / "models" / f"{args.algorithm.lower()}_{args.symbol.lower()}.zip"
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    agent.save(output_path)

    logger.info(
        "Training complete: mean_reward=%.4f std=%.4f time=%.1fs saved=%s",
        result.mean_reward, result.std_reward, elapsed, output_path,
    )

    eval_result = agent.evaluate(env_val, n_episodes=10)
    logger.info(
        "Final eval: mean_reward=%.4f win_rate=%.2f sharpe=%.2f max_dd=%.4f",
        eval_result.mean_reward, eval_result.mean_win_rate,
        eval_result.mean_sharpe, eval_result.mean_max_dd,
    )


if __name__ == "__main__":
    main()

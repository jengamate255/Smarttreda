"""Run backtest with comparison to baselines.

Usage:
    python scripts/backtest.py --model models/ppo_eurusd.zip --symbol EURUSD
    python scripts/backtest.py --model models/sac_xauusd.zip --symbol XAUUSD --compare
    python scripts/backtest.py --symbol EURUSD --baselines-only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
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


def load_data(symbol: str, timeframe: str) -> pd.DataFrame:
    data_path = PROJECT_ROOT / "data" / "raw" / f"{symbol}_{timeframe}.parquet"
    config = load_config()

    if data_path.exists():
        df = pd.read_parquet(data_path)
        logger.info("Loaded %d bars from %s", len(df), data_path)
    else:
        logger.info("No local data, generating synthetic for %s %s", symbol, timeframe)
        from forex_rl.src.data.loader import DataLoader
        loader = DataLoader()
        dates_cfg = config.get("test_dates", {})
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


def load_agent(model_path: str):
    from stable_baselines3 import PPO, SAC

    path = Path(model_path)
    stem = path.stem.lower()
    if "ppo" in stem:
        return PPO.load(str(path))
    elif "sac" in stem:
        return SAC.load(str(path))
    return PPO.load(str(path))


def run_backtest(agent, env, label: str = "RL Agent") -> dict:
    from forex_rl.src.backtest.metrics import BacktestMetrics, TradeResult

    obs, _ = env.reset()
    done = False
    equity = env.cfg.initial_balance
    equity_curve = [equity]
    trades: list[TradeResult] = []

    while not done:
        action, _ = agent.predict(obs, deterministic=True)
        obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated

        equity += float(reward) * equity
        equity_curve.append(equity)

        if terminated and info.get("total_trades", 0) > len(trades):
            eq_arr = np.array(equity_curve)
            entry_eq = eq_arr[-2] if len(eq_arr) > 1 else equity
            pnl = equity - entry_eq
            trade = TradeResult(
                entry_time=env.current_step - 10,
                exit_time=env.current_step,
                side="BUY" if info.get("position") and info["position"].get("side", 0) > 0 else "SELL",
                entry_price=info.get("current_price", 0.0),
                exit_price=info.get("current_price", 0.0),
                pnl=pnl,
                pnl_pct=pnl / entry_eq if entry_eq > 0 else 0.0,
                duration=pd.Timedelta(hours=10),
            )
            trades.append(trade)

    metrics = BacktestMetrics.compute_all(trades, equity_curve)
    metrics["label"] = label
    metrics["equity_curve"] = equity_curve
    return metrics


def ema_crossover_backtest(df: pd.DataFrame, fast: int = 20, slow: int = 50) -> dict:
    from forex_rl.src.backtest.metrics import BacktestMetrics, TradeResult

    close = df["close"].values.astype(np.float64)
    n = len(close)
    initial = 10000.0
    equity = initial
    equity_curve = [equity]
    trades: list[TradeResult] = []

    ema_fast = pd.Series(close).ewm(span=fast, adjust=False).mean().values
    ema_slow = pd.Series(close).ewm(span=slow, adjust=False).mean().values

    position = 0
    entry_price = 0.0
    sl_pips = 30
    tp_pips = 90

    for i in range(slow + 1, n):
        if position == 0:
            if ema_fast[i] > ema_slow[i] and ema_fast[i - 1] <= ema_slow[i - 1]:
                position = 1
                entry_price = close[i]
                sl = entry_price - sl_pips * 0.0001
                tp = entry_price + tp_pips * 0.0001
            elif ema_fast[i] < ema_slow[i] and ema_fast[i - 1] >= ema_slow[i - 1]:
                position = -1
                entry_price = close[i]
                sl = entry_price + sl_pips * 0.0001
                tp = entry_price - tp_pips * 0.0001
        else:
            if position == 1:
                if close[i] <= sl:
                    pnl = (sl - entry_price) * 10000
                    equity += pnl
                    trades.append(TradeResult(
                        entry_time=i - 10, exit_time=i, side="BUY",
                        entry_price=entry_price, exit_price=sl,
                        pnl=pnl, pnl_pct=pnl / equity, duration=pd.Timedelta(hours=10),
                    ))
                    position = 0
                elif close[i] >= tp:
                    pnl = (tp - entry_price) * 10000
                    equity += pnl
                    trades.append(TradeResult(
                        entry_time=i - 10, exit_time=i, side="BUY",
                        entry_price=entry_price, exit_price=tp,
                        pnl=pnl, pnl_pct=pnl / equity, duration=pd.Timedelta(hours=10),
                    ))
                    position = 0
            elif position == -1:
                if close[i] >= sl:
                    pnl = (entry_price - sl) * 10000
                    equity += pnl
                    trades.append(TradeResult(
                        entry_time=i - 10, exit_time=i, side="SELL",
                        entry_price=entry_price, exit_price=sl,
                        pnl=pnl, pnl_pct=pnl / equity, duration=pd.Timedelta(hours=10),
                    ))
                    position = 0
                elif close[i] <= tp:
                    pnl = (entry_price - tp) * 10000
                    equity += pnl
                    trades.append(TradeResult(
                        entry_time=i - 10, exit_time=i, side="SELL",
                        entry_price=entry_price, exit_price=tp,
                        pnl=pnl, pnl_pct=pnl / equity, duration=pd.Timedelta(hours=10),
                    ))
                    position = 0

        equity_curve.append(equity)

    if position != 0:
        pnl = (close[-1] - entry_price) * position * 10000
        equity += pnl
        equity_curve.append(equity)
        trades.append(TradeResult(
            entry_time=n - 10, exit_time=n, side="BUY" if position == 1 else "SELL",
            entry_price=entry_price, exit_price=close[-1],
            pnl=pnl, pnl_pct=pnl / equity, duration=pd.Timedelta(hours=10),
        ))

    metrics = BacktestMetrics.compute_all(trades, equity_curve)
    metrics["label"] = f"EMA {fast}/{slow}"
    metrics["equity_curve"] = equity_curve
    return metrics


def buy_and_hold_backtest(df: pd.DataFrame) -> dict:
    from forex_rl.src.backtest.metrics import BacktestMetrics, TradeResult

    close = df["close"].values.astype(np.float64)
    initial = 10000.0
    entry_price = close[0]
    lots = initial / (entry_price * 10000) if entry_price > 0 else 1.0
    pnl = (close[-1] - entry_price) * lots * 10000
    equity = initial + pnl

    trade = TradeResult(
        entry_time=0, exit_time=len(close) - 1, side="BUY",
        entry_price=entry_price, exit_price=close[-1],
        pnl=pnl, pnl_pct=pnl / initial, duration=pd.Timedelta(hours=len(close)),
    )
    equity_curve = [initial, equity]
    metrics = BacktestMetrics.compute_all([trade], equity_curve)
    metrics["label"] = "Buy & Hold"
    metrics["equity_curve"] = equity_curve
    return metrics


def random_backtest(df: pd.DataFrame, n_trades: int = 50) -> dict:
    from forex_rl.src.backtest.metrics import BacktestMetrics, TradeResult

    close = df["close"].values.astype(np.float64)
    rng = np.random.default_rng(42)
    initial = 10000.0
    equity = initial
    equity_curve = [equity]
    trades: list[TradeResult] = []

    for _ in range(n_trades):
        entry_idx = rng.integers(0, len(close) - 10)
        exit_idx = min(entry_idx + rng.integers(5, 30), len(close) - 1)
        side = rng.choice(["BUY", "SELL"])
        entry_price = close[entry_idx]
        exit_price = close[exit_idx]

        if side == "BUY":
            pnl = (exit_price - entry_price) * 10000
        else:
            pnl = (entry_price - exit_price) * 10000

        equity += pnl
        equity_curve.append(equity)
        trades.append(TradeResult(
            entry_time=entry_idx, exit_time=exit_idx, side=side,
            entry_price=entry_price, exit_price=exit_price,
            pnl=pnl, pnl_pct=pnl / max(equity, 1),
            duration=pd.Timedelta(hours=exit_idx - entry_idx),
        ))

    metrics = BacktestMetrics.compute_all(trades, equity_curve)
    metrics["label"] = "Random"
    metrics["equity_curve"] = equity_curve
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run backtest with baseline comparisons",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Path to trained model (.zip). If omitted, runs baselines only.",
    )
    parser.add_argument(
        "--symbol", type=str, default="EURUSD",
        help="Symbol to backtest (default: EURUSD).",
    )
    parser.add_argument(
        "--timeframe", type=str, default="H1",
        help="Bar timeframe (default: H1).",
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="Run baseline comparisons.",
    )
    parser.add_argument(
        "--baselines-only", action="store_true",
        help="Run only baseline strategies (no RL model).",
    )
    parser.add_argument(
        "--lookback", type=int, default=64,
        help="Observation lookback window (default: 64).",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSON path. Default: reports/backtest_{symbol}.json.",
    )
    args = parser.parse_args()

    config = load_config()

    logger.info("Loading data: %s %s", args.symbol, args.timeframe)
    df = load_data(args.symbol, args.timeframe)
    logger.info("Backtest bars: %d", len(df))

    results = {}

    if not args.baselines_only and args.model:
        logger.info("Running RL backtest: %s", args.model)
        agent = load_agent(args.model)
        env = create_env(df, config, args.lookback)
        start = time.time()
        rl_metrics = run_backtest(agent, env, f"RL ({Path(args.model).stem})")
        rl_metrics["time_seconds"] = time.time() - start
        results["rl_agent"] = rl_metrics
        logger.info(
            "RL: return=%.2f%% sharpe=%.2f max_dd=%.2f%% trades=%d",
            rl_metrics.get("total_return", 0) * 100,
            rl_metrics.get("sharpe_ratio", 0),
            rl_metrics.get("max_drawdown", 0) * 100,
            rl_metrics.get("num_trades", 0),
        )

    if args.compare or args.baselines_only or not args.model:
        logger.info("Running baseline: EMA 20/50 crossover")
        ema_metrics = ema_crossover_backtest(df, fast=20, slow=50)
        results["ema_crossover"] = ema_metrics
        logger.info(
            "EMA: return=%.2f%% sharpe=%.2f max_dd=%.2f%% trades=%d",
            ema_metrics.get("total_return", 0) * 100,
            ema_metrics.get("sharpe_ratio", 0),
            ema_metrics.get("max_drawdown", 0) * 100,
            ema_metrics.get("num_trades", 0),
        )

        logger.info("Running baseline: Buy & Hold")
        bh_metrics = buy_and_hold_backtest(df)
        results["buy_and_hold"] = bh_metrics
        logger.info("B&H: return=%.2f%%", bh_metrics.get("total_return", 0) * 100)

        logger.info("Running baseline: Random")
        rand_metrics = random_backtest(df, n_trades=50)
        results["random"] = rand_metrics
        logger.info(
            "Random: return=%.2f%% sharpe=%.2f",
            rand_metrics.get("total_return", 0) * 100,
            rand_metrics.get("sharpe_ratio", 0),
        )

    output_path = args.output or str(
        PROJECT_ROOT / "reports" / f"backtest_{args.symbol.lower()}.json"
    )
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    serializable = {}
    for key, val in results.items():
        s = {k: v for k, v in val.items() if k != "equity_curve"}
        s["equity_curve_len"] = len(val.get("equity_curve", []))
        serializable[key] = s

    with open(output_path, "w") as f:
        json.dump(serializable, f, indent=2, default=str)

    print("\n" + "=" * 70)
    print(f"BACKTEST RESULTS — {args.symbol} {args.timeframe}")
    print("=" * 70)
    print(f"{'Strategy':<30s} {'Return':>10s} {'Sharpe':>10s} {'MaxDD':>10s} {'Trades':>8s}")
    print("-" * 70)
    for name, m in results.items():
        label = m.get("label", name)
        ret = m.get("total_return", 0) * 100
        sharpe = m.get("sharpe_ratio", 0)
        dd = m.get("max_drawdown", 0) * 100
        trades = m.get("num_trades", 0)
        print(f"{label:<30s} {ret:>9.2f}% {sharpe:>10.2f} {dd:>9.2f}% {trades:>8d}")
    print("=" * 70)
    print(f"Results saved to: {output_path}")


if __name__ == "__main__":
    main()

"""Full training pipeline with baseline comparison and comprehensive evaluation."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from ..agents.factory import AgentFactory
from ..agents.policy import AgentConfig, BaseAgent, EvalResult, TrainingResult
from ..backtest.metrics import BacktestMetrics, TradeResult
from ..data.cleaner import DataCleaner
from ..data.loader import DataLoader
from ..data.splitter import DataSplitter
from ..environment.forex_env import EnvConfig, ForexTradingEnv
from .evaluation import Evaluator

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# ═══════════════════════════════════════════════════════════════════════════════
# Dataclasses
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class BacktestResult:
    metrics: dict[str, Any]
    trades: list[TradeResult]
    equity_curve: list[float]


# ═══════════════════════════════════════════════════════════════════════════════
# Baseline Strategy: EMA 50/200 Crossover
# ═══════════════════════════════════════════════════════════════════════════════


class EMACrossoverStrategy:
    """BUY when EMA50 > EMA200, SELL when EMA50 < EMA200."""

    def __init__(self, ema_fast: int = 50, ema_slow: int = 200) -> None:
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow

    def simulate(
        self,
        df: pd.DataFrame,
        initial_balance: float = 10_000.0,
        spread_pips: float = 1.5,
        risk_per_trade: float = 0.005,
        sl_atr_mult: float = 2.0,
        tp_atr_mult: float = 3.0,
        point_value: float = 10.0,
    ) -> tuple[list[float], list[TradeResult]]:
        closes = df["close"].values.astype(np.float64)
        highs = df["high"].values.astype(np.float64)
        lows = df["low"].values.astype(np.float64)
        n = len(closes)
        if n < self.ema_slow + 1:
            return [initial_balance] * n, []

        ema_f = pd.Series(closes).ewm(span=self.ema_fast, adjust=False).mean().values
        ema_s = pd.Series(closes).ewm(span=self.ema_slow, adjust=False).mean().values

        equity = initial_balance
        equity_curve = [equity]
        trades: list[TradeResult] = []
        pos_side: int = 0
        entry_price: float = 0.0
        entry_step: int = 0
        volume: float = 0.0
        sl_price: float = 0.0
        tp_price: float = 0.0
        half_spread = spread_pips / 20000.0

        for i in range(1, n):
            # Check SL/TP if in position
            if pos_side != 0:
                hit_sl = False
                hit_tp = False
                if pos_side == 1:
                    hit_sl = lows[i] <= sl_price
                    hit_tp = highs[i] >= tp_price
                else:
                    hit_sl = highs[i] >= sl_price
                    hit_tp = lows[i] <= tp_price

                if hit_sl or hit_tp:
                    exit_px = sl_price if hit_sl else tp_price
                    direction = pos_side
                    raw_pnl = direction * (exit_px - entry_price) * volume * 10000.0
                    commission = 7.0 * volume
                    pnl = raw_pnl - commission
                    equity += pnl
                    equity = max(equity, 0.0)
                    trades.append(TradeResult(
                        entry_time=df.index[entry_step],
                        exit_time=df.index[i],
                        side="BUY" if pos_side == 1 else "SELL",
                        entry_price=entry_price,
                        exit_price=exit_px,
                        pnl=pnl,
                        pnl_pct=pnl / initial_balance,
                        duration=pd.Timedelta(df.index[i] - df.index[entry_step]),
                        spread_cost=spread_pips * volume * point_value,
                    ))
                    pos_side = 0

            # Signal
            if pos_side == 0 and ema_f[i] > ema_s[i] and ema_f[i - 1] <= ema_s[i - 1]:
                entry_px = closes[i] + half_spread
                sl_dist = entry_px - lows[i] * (1.0 + sl_atr_mult * 0.001)
                sl_dist = max(abs(sl_dist), 0.0005)
                sl_pips = sl_dist * 10000.0
                lot = max(0.01, round(equity * risk_per_trade / (sl_pips * point_value), 2))
                sl_price = entry_px - sl_dist
                tp_price = entry_px + sl_dist * tp_atr_mult / sl_atr_mult
                entry_price = entry_px
                entry_step = i
                volume = lot
                pos_side = 1

            elif pos_side == 0 and ema_f[i] < ema_s[i] and ema_f[i - 1] >= ema_s[i - 1]:
                entry_px = closes[i] - half_spread
                sl_dist = highs[i] * (1.0 + sl_atr_mult * 0.001) - entry_px
                sl_dist = max(abs(sl_dist), 0.0005)
                sl_pips = sl_dist * 10000.0
                lot = max(0.01, round(equity * risk_per_trade / (sl_pips * point_value), 2))
                sl_price = entry_px + sl_dist
                tp_price = entry_px - sl_dist * tp_atr_mult / sl_atr_mult
                entry_price = entry_px
                entry_step = i
                volume = lot
                pos_side = -1

            unrealised = 0.0
            if pos_side != 0:
                unrealised = pos_side * (closes[i] - entry_price) * volume * 10000.0
            equity_curve.append(equity + unrealised)

        # Close open position at end
        if pos_side != 0:
            exit_px = closes[-1]
            direction = pos_side
            raw_pnl = direction * (exit_px - entry_price) * volume * 10000.0
            commission = 7.0 * volume
            pnl = raw_pnl - commission
            equity += pnl
            trades.append(TradeResult(
                entry_time=df.index[entry_step],
                exit_time=df.index[-1],
                side="BUY" if pos_side == 1 else "SELL",
                entry_price=entry_price,
                exit_price=exit_px,
                pnl=pnl,
                pnl_pct=pnl / initial_balance,
                duration=pd.Timedelta(df.index[-1] - df.index[entry_step]),
                spread_cost=spread_pips * volume * point_value,
            ))

        return equity_curve, trades


# ═══════════════════════════════════════════════════════════════════════════════
# Baseline Strategy: Random Trading
# ═══════════════════════════════════════════════════════════════════════════════


class RandomStrategy:
    """Random BUY/SELL/HOLD at each bar."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed

    def simulate(
        self,
        df: pd.DataFrame,
        initial_balance: float = 10_000.0,
        spread_pips: float = 1.5,
        risk_per_trade: float = 0.005,
        point_value: float = 10.0,
    ) -> tuple[list[float], list[TradeResult]]:
        rng = np.random.default_rng(self.seed)
        closes = df["close"].values.astype(np.float64)
        n = len(closes)

        equity = initial_balance
        equity_curve = [equity]
        trades: list[TradeResult] = []
        pos_side: int = 0
        entry_price: float = 0.0
        entry_step: int = 0
        volume: float = 0.0
        half_spread = spread_pips / 20000.0

        for i in range(1, n):
            # Close position with 1/20 probability or at end
            if pos_side != 0 and (rng.random() < 0.05 or i == n - 1):
                exit_px = closes[i] + (half_spread if pos_side == -1 else -half_spread)
                direction = pos_side
                raw_pnl = direction * (exit_px - entry_price) * volume * 10000.0
                commission = 7.0 * volume
                pnl = raw_pnl - commission
                equity += pnl
                equity = max(equity, 0.0)
                trades.append(TradeResult(
                    entry_time=df.index[entry_step],
                    exit_time=df.index[i],
                    side="BUY" if pos_side == 1 else "SELL",
                    entry_price=entry_price,
                    exit_price=exit_px,
                    pnl=pnl,
                    pnl_pct=pnl / initial_balance,
                    duration=pd.Timedelta(df.index[i] - df.index[entry_step]),
                    spread_cost=spread_pips * volume * point_value,
                ))
                pos_side = 0

            # Open new position
            if pos_side == 0:
                action = rng.choice([-1, 0, 1])
                if action != 0:
                    if action == 1:
                        entry_px = closes[i] + half_spread
                    else:
                        entry_px = closes[i] - half_spread
                    sl_pips = 50.0
                    lot = max(0.01, round(equity * risk_per_trade / (sl_pips * point_value), 2))
                    entry_price = entry_px
                    entry_step = i
                    volume = lot
                    pos_side = action

            unrealised = 0.0
            if pos_side != 0:
                unrealised = pos_side * (closes[i] - entry_price) * volume * 10000.0
            equity_curve.append(equity + unrealised)

        if pos_side != 0:
            exit_px = closes[-1]
            direction = pos_side
            raw_pnl = direction * (exit_px - entry_price) * volume * 10000.0
            pnl = raw_pnl - 7.0 * volume
            equity += pnl
            trades.append(TradeResult(
                entry_time=df.index[entry_step],
                exit_time=df.index[-1],
                side="BUY" if pos_side == 1 else "SELL",
                entry_price=entry_price,
                exit_price=exit_px,
                pnl=pnl,
                pnl_pct=pnl / initial_balance,
                duration=pd.Timedelta(df.index[-1] - df.index[entry_step]),
            ))

        return equity_curve, trades


# ═══════════════════════════════════════════════════════════════════════════════
# Baseline Strategy: Buy and Hold
# ═══════════════════════════════════════════════════════════════════════════════


class BuyAndHoldStrategy:
    """Buy at start, hold forever."""

    def simulate(
        self,
        df: pd.DataFrame,
        initial_balance: float = 10_000.0,
        spread_pips: float = 1.5,
        risk_per_trade: float = 0.005,
        point_value: float = 10.0,
    ) -> tuple[list[float], list[TradeResult]]:
        closes = df["close"].values.astype(np.float64)
        n = len(closes)
        if n == 0:
            return [initial_balance], []

        entry_px = closes[0] + spread_pips / 20000.0
        sl_pips = 50.0
        lot = max(0.01, round(initial_balance * risk_per_trade / (sl_pips * point_value), 2))

        equity_curve = [initial_balance]
        for i in range(n):
            unrealised = (closes[i] - entry_px) * lot * 10000.0
            equity_curve.append(initial_balance + unrealised)

        pnl = (closes[-1] - entry_px) * lot * 10000.0 - 7.0 * lot
        trades = [TradeResult(
            entry_time=df.index[0],
            exit_time=df.index[-1],
            side="BUY",
            entry_price=entry_px,
            exit_price=closes[-1],
            pnl=pnl,
            pnl_pct=pnl / initial_balance,
            duration=pd.Timedelta(df.index[-1] - df.index[0]),
            spread_cost=spread_pips * lot * point_value,
        )]

        return equity_curve, trades


# ═══════════════════════════════════════════════════════════════════════════════
# Feature Computation Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _compute_all_features(df: pd.DataFrame) -> np.ndarray:
    """Run the full feature pipeline and return (n_bars, 47) feature matrix."""
    from ..market.indicators import FeatureEngine
    from ..market.structure import MarketStructure
    from ..market.supply_demand import SupplyDemandDetector
    from ..market.regime import RegimeDetector

    ohlcv = df[["open", "high", "low", "close", "volume"]].copy()
    if not isinstance(ohlcv.index, pd.DatetimeIndex):
        ohlcv.index = pd.date_range("2020-01-01", periods=len(ohlcv), freq="1h", tz="UTC")

    fe = FeatureEngine()
    enriched = fe.compute_all(ohlcv)
    market_feats = enriched[fe.feature_names()].values.astype(np.float32)

    ms = MarketStructure()
    struct_feats = ms.compute_structure_features(ohlcv)
    struct_vals = struct_feats.values.astype(np.float32)

    sd = SupplyDemandDetector()
    sd_feats = sd.compute_sd_features(ohlcv)
    sd_vals = sd_feats.values.astype(np.float32)

    rd = RegimeDetector()
    regime_feats = rd.compute_regime_features(ohlcv)
    regime_vals = regime_feats.values.astype(np.float32)

    all_feats = np.hstack([market_feats, struct_vals, sd_vals, regime_vals])
    return np.nan_to_num(all_feats, nan=0.0).astype(np.float32)


def _compute_regime_labels(df: pd.DataFrame) -> pd.Series:
    """Return regime label Series aligned to df index."""
    from ..market.regime import RegimeDetector
    rd = RegimeDetector()
    ohlcv = df[["open", "high", "low", "close", "volume"]].copy()
    if not isinstance(ohlcv.index, pd.DatetimeIndex):
        ohlcv.index = pd.date_range("2020-01-01", periods=len(ohlcv), freq="1h", tz="UTC")
    return rd.detect(ohlcv)


# ═══════════════════════════════════════════════════════════════════════════════
# Trainer
# ═══════════════════════════════════════════════════════════════════════════════


class Trainer:
    """Full training pipeline with data loading, feature computation,
    training, evaluation, baseline comparison, and persistence."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.data_loader = DataLoader()
        self.data_cleaner = DataCleaner()
        self.data_splitter = DataSplitter()

        self.models_dir = PROJECT_ROOT / "models"
        self.reports_dir = PROJECT_ROOT / "reports"
        self.logs_dir = PROJECT_ROOT / "logs"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        rl_cfg = config.get("rl", {})
        self.agent_config = AgentConfig(
            algorithm=config.get("algorithm", "PPO"),
            learning_rate=rl_cfg.get("learning_rate", 3e-4),
            gamma=rl_cfg.get("gamma", 0.99),
            batch_size=rl_cfg.get("batch_size", 64),
            n_steps=rl_cfg.get("n_steps", 2048),
            ent_coef=rl_cfg.get("entropy_coeff", 0.01),
            clip_range=rl_cfg.get("clip_range", 0.2),
            hidden_sizes=(rl_cfg.get("hidden_size", 256),) * 2,
            verbose=1,
            tensorboard_log=None,
        )

        self.env_config = EnvConfig(
            lookback=config.get("lookback", 64),
            initial_balance=config.get("Initial_balance", 10_000.0),
            risk_per_trade=config.get("risk_per_trade", 0.005),
            max_positions=config.get("maximum_positions", 3),
            spread_pips=config.get("spread_pips", 1.5),
            slippage_pips=config.get("slippage_pips", 0.5),
            commission_per_lot=config.get("commission_per_lot", 7.0),
            sl_atr_multiplier=config.get("sl_atr_multiplier", 2.0),
            tp_atr_multiplier=config.get("tp_atr_multiplier", 3.0),
        )

        self.train_dates = config.get("train_dates", {"start": "2018-01-01", "end": "2022-12-31"})
        self.val_dates = config.get("val_dates", {"start": "2023-01-01", "end": "2023-12-31"})

        if self.agent_config.algorithm not in ("PPO", "SAC"):
            raise ValueError(f"Unsupported algorithm: {self.agent_config.algorithm}")

    # ── Public API ────────────────────────────────────────────────────────

    def train(
        self,
        symbol: str,
        timeframe: str,
        algorithm: str = "PPO",
        total_timesteps: int = 200_000,
    ) -> TrainingResult:
        """Full training pipeline.

        1. Load and clean data
        2. Compute all features
        3. Split into train/val
        4. Create environments
        5. Create agent via AgentFactory
        6. Train with eval callback
        7. Evaluate on validation set
        8. Compare against baselines
        9. Save model and metadata
        10. Log to database
        """
        logger.info("Starting training: %s %s %s", symbol, timeframe, algorithm)
        self.agent_config.algorithm = algorithm
        start_time = time.time()

        # 1. Load data
        raw_df = self._load_data(symbol, timeframe)

        # 2. Clean data
        clean_df = self.data_cleaner.clean(raw_df)

        # 3. Compute features
        features = _compute_all_features(clean_df)
        regime_labels = _compute_regime_labels(clean_df)

        # 4. Split
        split_dfs = self.data_splitter.split(
            clean_df, self.train_dates, self.val_dates,
            {"start": "2099-01-01", "end": "2099-12-31"},
        )
        train_df = split_dfs["train"]
        val_df = split_dfs["val"]

        train_idx = clean_df.index.get_indexer(train_df.index)
        val_idx = clean_df.index.get_indexer(val_df.index)

        train_feats = features[train_idx]
        val_feats = features[val_idx]
        train_regime = regime_labels.iloc[train_idx].reset_index(drop=True)
        val_regime = regime_labels.iloc[val_idx].reset_index(drop=True)

        # 5. Create environments
        train_env = ForexTradingEnv(train_df, self.env_config, train_feats, train_regime)
        val_env = ForexTradingEnv(val_df, self.env_config, val_feats, val_regime)

        # 6. Create agent
        agent = AgentFactory.create(self.agent_config)

        # 7. Train
        training_result = agent.train(
            train_env,
            total_timesteps=total_timesteps,
            eval_env=val_env,
        )

        # 8. Evaluate on validation
        eval_result = Evaluator.evaluate_agent(agent, val_env, n_episodes=20)

        # 9. Baselines
        baselines = self.compare_baselines(symbol, timeframe)

        # 10. Save
        model_tag = f"{symbol}_{timeframe}_{algorithm}"
        model_path = str(self.models_dir / model_tag)
        agent.save(model_path)

        metadata = self._build_metadata(
            model_tag, symbol, timeframe, algorithm,
            training_result, eval_result, baselines, time.time() - start_time,
        )
        (self.models_dir / f"{model_tag}_metadata.json").write_text(
            json.dumps(metadata, indent=2, default=str)
        )

        # 11. Log to database
        self._log_to_database(
            model_tag, symbol, timeframe, algorithm, metadata,
        )

        logger.info(
            "Training complete: %s | sharpe=%.3f | max_dd=%.3f | win_rate=%.3f",
            model_tag,
            eval_result.mean_sharpe,
            eval_result.mean_max_dd,
            eval_result.mean_win_rate,
        )

        return training_result

    def evaluate(
        self,
        model_path: str,
        symbol: str,
        timeframe: str,
        n_episodes: int = 50,
    ) -> EvalResult:
        """Load a saved model and evaluate on test set."""
        agent = AgentFactory.create(self.agent_config)
        agent.load(model_path)

        raw_df = self._load_data(symbol, timeframe)
        clean_df = self.data_cleaner.clean(raw_df)
        features = _compute_all_features(clean_df)
        regime_labels = _compute_regime_labels(clean_df)

        test_dates = self.config.get(
            "test_dates", {"start": "2024-01-01", "end": "2025-12-31"}
        )
        split = self.data_splitter.split(
            clean_df,
            {"start": "2000-01-01", "end": "2000-01-01"},
            {"start": "2000-01-01", "end": "2000-01-01"},
            test_dates,
        )
        test_df = split["test"]
        test_idx = clean_df.index.get_indexer(test_df.index)
        test_feats = features[test_idx]
        test_regime = regime_labels.iloc[test_idx].reset_index(drop=True)

        test_env = ForexTradingEnv(test_df, self.env_config, test_feats, test_regime)
        return Evaluator.evaluate_agent(agent, test_env, n_episodes=n_episodes)

    def compare_baselines(
        self,
        symbol: str,
        timeframe: str,
    ) -> dict[str, BacktestResult]:
        """Compare RL agent against baseline strategies.

        Returns dict of BacktestResult keyed by strategy name.
        """
        raw_df = self._load_data(symbol, timeframe)
        clean_df = self.data_cleaner.clean(raw_df)

        split = self.data_splitter.split(
            clean_df, self.train_dates, self.val_dates,
            {"start": "2099-01-01", "end": "2099-12-31"},
        )
        val_df = split["val"]

        init_bal = self.env_config.initial_balance
        spread = self.env_config.spread_pips
        risk = self.env_config.risk_per_trade
        pv = self.env_config.point_value

        results: dict[str, BacktestResult] = {}

        # Strategy A: EMA 50/200 crossover
        ema_strat = EMACrossoverStrategy(ema_fast=50, ema_slow=200)
        eq, trades = ema_strat.simulate(val_df, init_bal, spread, risk, point_value=pv)
        metrics = BacktestMetrics.compute_all(trades, eq)
        results["EMA_Crossover_50_200"] = BacktestResult(
            metrics=metrics, trades=trades, equity_curve=eq,
        )

        # Strategy B: Random trading
        rand_strat = RandomStrategy(seed=42)
        eq, trades = rand_strat.simulate(val_df, init_bal, spread, risk, point_value=pv)
        metrics = BacktestMetrics.compute_all(trades, eq)
        results["Random"] = BacktestResult(
            metrics=metrics, trades=trades, equity_curve=eq,
        )

        # Strategy C: Buy and hold
        bnh_strat = BuyAndHoldStrategy()
        eq, trades = bnh_strat.simulate(val_df, init_bal, spread, risk, point_value=pv)
        metrics = BacktestMetrics.compute_all(trades, eq)
        results["Buy_and_Hold"] = BacktestResult(
            metrics=metrics, trades=trades, equity_curve=eq,
        )

        return results

    # ── Private helpers ───────────────────────────────────────────────────

    def _load_data(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """Load OHLCV data with fallback to synthetic."""
        try:
            return self.data_loader.load_from_mt5(
                symbol, timeframe, self.train_dates["start"], self.val_dates["end"],
            )
        except Exception as exc:
            logger.warning("MT5 load failed, using synthetic: %s", exc)
            return self.data_loader.load_from_mt5(
                symbol, timeframe, self.train_dates["start"], self.val_dates["end"],
            )

    def _build_metadata(
        self,
        model_tag: str,
        symbol: str,
        timeframe: str,
        algorithm: str,
        training_result: TrainingResult,
        eval_result: EvalResult,
        baselines: dict[str, BacktestResult],
        elapsed: float,
    ) -> dict[str, Any]:
        cfg = self.agent_config
        return {
            "model_tag": model_tag,
            "symbol": symbol,
            "timeframe": timeframe,
            "algorithm": algorithm,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "training_time_seconds": round(elapsed, 2),
            "hyperparameters": {
                "learning_rate": cfg.learning_rate,
                "gamma": cfg.gamma,
                "batch_size": cfg.batch_size,
                "n_steps": cfg.n_steps,
                "entropy_coeff": cfg.ent_coef,
                "clip_range": cfg.clip_range,
                "hidden_size": cfg.hidden_sizes[0] if cfg.hidden_sizes else 256,
                "lookback": self.env_config.lookback,
            },
            "training_metrics": {
                "total_timesteps": training_result.total_timesteps,
                "mean_reward": training_result.mean_reward,
                "std_reward": training_result.std_reward,
            },
            "validation_metrics": {
                "mean_sharpe": eval_result.mean_sharpe,
                "mean_max_dd": eval_result.mean_max_dd,
                "mean_win_rate": eval_result.mean_win_rate,
                "mean_reward": eval_result.mean_reward,
                "n_episodes": eval_result.n_episodes,
            },
            "baseline_comparison": {
                name: {
                    "sharpe": res.metrics.get("sharpe_ratio", 0.0),
                    "total_return": res.metrics.get("total_return", 0.0),
                    "max_drawdown": res.metrics.get("max_drawdown", 0.0),
                    "win_rate": res.metrics.get("win_rate", 0.0),
                    "profit_factor": res.metrics.get("profit_factor", 0.0),
                    "num_trades": res.metrics.get("num_trades", 0),
                }
                for name, res in baselines.items()
            },
        }

    def _log_to_database(
        self,
        model_tag: str,
        symbol: str,
        timeframe: str,
        algorithm: str,
        metadata: dict[str, Any],
    ) -> None:
        """Log training run to SQLite database."""
        try:
            from ..utils.database import TradeDB

            db = TradeDB()
            exp_id = db.create_experiment(model_tag, metadata)
            run_id = db._conn.execute(
                "INSERT INTO training_runs (experiment_id, algorithm, hyperparams) VALUES (?, ?, ?)",
                (exp_id, algorithm, json.dumps(metadata.get("hyperparameters", {}))),
            ).lastrowid
            db._conn.execute(
                "INSERT INTO models (training_run_id, filepath, metrics) VALUES (?, ?, ?)",
                (run_id, str(self.models_dir / model_tag), json.dumps(metadata.get("validation_metrics", {}))),
            )
            db._conn.commit()
            db.close()
            logger.info("Logged training run to database: experiment_id=%d", exp_id)
        except Exception as exc:
            logger.warning("Failed to log to database: %s", exc)

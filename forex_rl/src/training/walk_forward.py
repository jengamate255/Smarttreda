"""Walk-forward optimization with multiple seeds for robustness."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from ..agents.factory import AgentFactory
from ..agents.policy import AgentConfig, BaseAgent, EvalResult
from ..backtest.metrics import BacktestMetrics
from ..data.cleaner import DataCleaner
from ..data.loader import DataLoader
from ..data.splitter import DataSplitter
from ..environment.forex_env import EnvConfig, ForexTradingEnv
from .evaluation import Evaluator
from .train import _compute_all_features, _compute_regime_labels

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# ═══════════════════════════════════════════════════════════════════════════════
# Dataclass
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward results across all folds and seeds."""

    fold_results: list[dict[str, Any]]
    aggregate_sharpe: float = 0.0
    aggregate_return: float = 0.0
    aggregate_max_dd: float = 0.0
    aggregate_win_rate: float = 0.0
    stability_score: float = 0.0
    n_folds: int = 0
    recommendation: str = "NEEDS_MORE_DATA"
    fold_details: list[dict[str, Any]] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# Walk-Forward Trainer
# ═══════════════════════════════════════════════════════════════════════════════


class WalkForwardTrainer:
    """Walk-forward optimization: train on rolling windows, evaluate OOS.

    For each fold:
        1. Train agent on train_window bars
        2. Evaluate on test_window bars
        3. Record OOS performance
        4. Vary random seeds for robustness

    Returns aggregated OOS results with stability analysis.
    """

    def __init__(self, config: dict, agent_config: Optional[AgentConfig] = None) -> None:
        self.config = config
        self.data_loader = DataLoader()
        self.data_cleaner = DataCleaner()

        self.models_dir = PROJECT_ROOT / "models" / "walk_forward"
        self.models_dir.mkdir(parents=True, exist_ok=True)

        rl_cfg = config.get("rl", {})
        self.agent_config = agent_config or AgentConfig(
            algorithm=config.get("algorithm", "PPO"),
            learning_rate=rl_cfg.get("learning_rate", 3e-4),
            gamma=rl_cfg.get("gamma", 0.99),
            batch_size=rl_cfg.get("batch_size", 64),
            n_steps=rl_cfg.get("n_steps", 2048),
            ent_coef=rl_cfg.get("entropy_coeff", 0.01),
            clip_range=rl_cfg.get("clip_range", 0.2),
            hidden_sizes=(rl_cfg.get("hidden_size", 256),) * 2,
            verbose=0,
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

    # ── Public API ────────────────────────────────────────────────────────

    def run(
        self,
        symbol: str,
        timeframe: str,
        train_window: int = 1008,
        test_window: int = 252,
        step: int = 252,
        n_seeds: int = 3,
        total_timesteps: int = 100_000,
    ) -> WalkForwardResult:
        """Run walk-forward optimization.

        Parameters
        ----------
        symbol : str
            Trading symbol (e.g. "EURUSD").
        timeframe : str
            Timeframe (e.g. "H1").
        train_window : int
            Number of bars for training per fold (~4 years of trading days).
        test_window : int
            Number of bars for OOS testing per fold (~1 year).
        step : int
            Number of bars to advance each fold (annual step).
        n_seeds : int
            Number of random seeds per fold for robustness.
        total_timesteps : int
            Training timesteps per fold.
        """
        logger.info(
            "Walk-forward: %s %s | train=%d test=%d step=%d seeds=%d",
            symbol, timeframe, train_window, test_window, step, n_seeds,
        )
        overall_start = time.time()

        # Load and prepare full dataset
        raw_df = self._load_data(symbol, timeframe)
        clean_df = self.data_cleaner.clean(raw_df)
        features = _compute_all_features(clean_df)
        regime_labels = _compute_regime_labels(clean_df)

        # Generate fold indices
        n_bars = len(clean_df)
        if train_window + test_window > n_bars:
            raise ValueError(
                f"train_window ({train_window}) + test_window ({test_window}) "
                f"> total bars ({n_bars})"
            )

        fold_results: list[dict[str, Any]] = []
        fold_idx = 0
        start = 0

        while start + train_window + test_window <= n_bars:
            train_end = start + train_window
            test_end = train_end + test_window

            logger.info(
                "Fold %d: train [%d:%d] test [%d:%d]",
                fold_idx, start, train_end, train_end, test_end,
            )

            fold_seeds: list[dict[str, Any]] = []
            for seed in range(n_seeds):
                actual_seed = 42 + seed
                logger.info("  Seed %d/%d (seed=%d)", seed + 1, n_seeds, actual_seed)

                result = self._train_and_evaluate_fold(
                    clean_df, features, regime_labels,
                    start, train_end, test_end,
                    actual_seed, total_timesteps, fold_idx,
                )
                fold_seeds.append(result)

            # Aggregate across seeds for this fold
            seed_sharpes = [r["sharpe"] for r in fold_seeds]
            seed_returns = [r["total_return"] for r in fold_seeds]
            seed_dds = [r["max_drawdown"] for r in fold_seeds]

            fold_summary = {
                "fold": fold_idx,
                "train_start": start,
                "train_end": train_end,
                "test_start": train_end,
                "test_end": test_end,
                "mean_sharpe": float(np.mean(seed_sharpes)),
                "std_sharpe": float(np.std(seed_sharpes)),
                "mean_return": float(np.mean(seed_returns)),
                "mean_max_dd": float(np.mean(seed_dds)),
                "seeds": fold_seeds,
            }
            fold_results.append(fold_summary)

            logger.info(
                "  Fold %d: sharpe=%.3f±%.3f return=%.3f max_dd=%.3f",
                fold_idx,
                fold_summary["mean_sharpe"],
                fold_summary["std_sharpe"],
                fold_summary["mean_return"],
                fold_summary["mean_max_dd"],
            )

            fold_idx += 1
            start += step

        # Aggregate across all folds
        result = self._aggregate_results(fold_results)
        elapsed = time.time() - overall_start

        logger.info(
            "Walk-forward complete: %d folds | sharpe=%.3f | return=%.3f | "
            "max_dd=%.3f | stability=%.3f | recommendation=%s (%.1fs)",
            result.n_folds,
            result.aggregate_sharpe,
            result.aggregate_return,
            result.aggregate_max_dd,
            result.stability_score,
            result.recommendation,
            elapsed,
        )

        return result

    # ── Fold Training ─────────────────────────────────────────────────────

    def _train_and_evaluate_fold(
        self,
        full_df: pd.DataFrame,
        full_features: np.ndarray,
        full_regime: pd.Series,
        train_start: int,
        train_end: int,
        test_end: int,
        seed: int,
        total_timesteps: int,
        fold_idx: int,
    ) -> dict[str, Any]:
        """Train agent on train slice, evaluate on test slice."""
        train_df = full_df.iloc[train_start:train_end].copy()
        test_df = full_df.iloc[train_end:test_end].copy()

        train_feats = full_features[train_start:train_end]
        test_feats = full_features[train_end:test_end]

        train_regime = full_regime.iloc[train_start:train_end].reset_index(drop=True)
        test_regime = full_regime.iloc[train_end:test_end].reset_index(drop=True)

        # Create environments
        train_env = ForexTradingEnv(train_df, self.env_config, train_feats, train_regime)
        test_env = ForexTradingEnv(test_df, self.env_config, test_feats, test_regime)

        # Create agent with fold-specific seed
        cfg = AgentConfig(
            algorithm=self.agent_config.algorithm,
            learning_rate=self.agent_config.learning_rate,
            gamma=self.agent_config.gamma,
            batch_size=self.agent_config.batch_size,
            n_steps=self.agent_config.n_steps,
            ent_coef=self.agent_config.ent_coef,
            clip_range=self.agent_config.clip_range,
            hidden_sizes=self.agent_config.hidden_sizes,
            verbose=0,
            seed=seed,
        )
        agent = AgentFactory.create(cfg)

        # Train
        train_result = agent.train(train_env, total_timesteps=total_timesteps)

        # Evaluate
        eval_result = Evaluator.evaluate_agent(agent, test_env, n_episodes=10)

        return {
            "seed": seed,
            "sharpe": eval_result.mean_sharpe,
            "total_return": eval_result.mean_reward,
            "max_drawdown": eval_result.mean_max_dd,
            "win_rate": eval_result.mean_win_rate,
            "mean_reward": eval_result.mean_reward,
            "training_time": train_result.training_time,
        }

    # ── Aggregate Results ─────────────────────────────────────────────────

    def _aggregate_results(self, fold_results: list[dict[str, Any]]) -> WalkForwardResult:
        """Combine fold results into overall OOS metrics with recommendation."""
        if not fold_results:
            return WalkForwardResult(
                fold_results=[],
                n_folds=0,
                recommendation="NEEDS_MORE_DATA",
            )

        sharpes = [f["mean_sharpe"] for f in fold_results]
        returns = [f["mean_return"] for f in fold_results]
        dds = [f["mean_max_dd"] for f in fold_results]
        win_rates = [
            np.mean([s["win_rate"] for s in f["seeds"]])
            for f in fold_results
        ]

        agg_sharpe = float(np.mean(sharpes))
        agg_return = float(np.mean(returns))
        agg_dd = float(np.max(dds))
        agg_wr = float(np.mean(win_rates))
        stability = float(np.std(sharpes))

        # Recommendation logic
        recommendation = self._make_recommendation(
            agg_sharpe, agg_return, agg_dd, stability, len(fold_results),
        )

        return WalkForwardResult(
            fold_results=fold_results,
            aggregate_sharpe=agg_sharpe,
            aggregate_return=agg_return,
            aggregate_max_dd=agg_dd,
            aggregate_win_rate=agg_wr,
            stability_score=stability,
            n_folds=len(fold_results),
            recommendation=recommendation,
            fold_details=[
                {
                    "fold": f["fold"],
                    "sharpe": f["mean_sharpe"],
                    "return": f["mean_return"],
                    "max_dd": f["mean_max_dd"],
                }
                for f in fold_results
            ],
        )

    @staticmethod
    def _make_recommendation(
        sharpe: float,
        ret: float,
        max_dd: float,
        stability: float,
        n_folds: int,
    ) -> str:
        """Determine trade recommendation from aggregate metrics."""
        if n_folds < 3:
            return "NEEDS_MORE_DATA"

        # Hard disqualifiers
        if max_dd > 0.20:
            return "DO_NOT_TRADE"
        if sharpe < 0.5:
            return "DO_NOT_TRADE"
        if stability > 1.5:
            return "DO_NOT_TRADE"

        # Strong signals
        if sharpe > 1.5 and ret > 0 and max_dd < 0.10 and stability < 0.8:
            return "TRADE"

        # Borderline
        if sharpe > 1.0 and ret > 0 and max_dd < 0.15:
            return "TRADE"

        return "DO_NOT_TRADE"

    # ── Data Loading ──────────────────────────────────────────────────────

    def _load_data(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """Load full date range for walk-forward."""
        start = self.config.get("train_dates", {}).get("start", "2018-01-01")
        end = self.config.get("test_dates", {}).get("end", "2025-12-31")
        try:
            return self.data_loader.load_from_mt5(symbol, timeframe, start, end)
        except Exception as exc:
            logger.warning("MT5 load failed, using synthetic: %s", exc)
            return self.data_loader.load_from_mt5(symbol, timeframe, start, end)

"""Optuna-based hyperparameter search for RL trading agents."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

try:
    import optuna
    from optuna.trial import Trial
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False

from ..agents.factory import AgentFactory
from ..agents.policy import AgentConfig
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
class OptunaResult:
    """Results from an Optuna hyperparameter search."""

    best_params: dict[str, Any]
    best_value: float
    study: Any = None  # optuna.Study
    all_trials: list[dict[str, Any]] = field(default_factory=list)
    n_completed: int = 0
    elapsed_seconds: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Hyperparameter Search
# ═══════════════════════════════════════════════════════════════════════════════


class HyperparameterSearch:
    """Optuna-based hyperparameter optimization for RL trading agents.

    Optimizes a composite objective combining Sharpe, Sortino, Calmar ratios,
    drawdown, and profit factor. Strongly penalizes max_dd > 15%.

    Search space:
        - learning_rate: log-uniform [1e-5, 1e-3]
        - gamma: uniform [0.95, 0.999]
        - batch_size: categorical [32, 64, 128, 256]
        - n_steps: categorical [1024, 2048, 4096]
        - entropy_coeff: log-uniform [1e-4, 0.05]
        - clip_range: uniform [0.1, 0.3]
        - hidden_size: categorical [64, 128, 256]
        - lookback: categorical [32, 64, 128]
        - sl_atr_multiplier: uniform [1.5, 3.0]
        - tp_atr_multiplier: uniform [2.0, 5.0]
    """

    def __init__(self, config: dict) -> None:
        if not HAS_OPTUNA:
            raise ImportError(
                "Optuna is required for hyperparameter search. "
                "Install with: pip install optuna"
            )

        self.config = config
        self.data_loader = DataLoader()
        self.data_cleaner = DataCleaner()

        self.models_dir = PROJECT_ROOT / "models" / "optuna"
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self.env_config = EnvConfig(
            initial_balance=config.get("Initial_balance", 10_000.0),
            risk_per_trade=config.get("risk_per_trade", 0.005),
            max_positions=config.get("maximum_positions", 3),
            spread_pips=config.get("spread_pips", 1.5),
            slippage_pips=config.get("slippage_pips", 0.5),
            commission_per_lot=config.get("commission_per_lot", 7.0),
        )

        self.train_dates = config.get("train_dates", {"start": "2018-01-01", "end": "2022-12-31"})
        self.val_dates = config.get("val_dates", {"start": "2023-01-01", "end": "2023-12-31"})

        # Cache loaded data to avoid reloading per trial
        self._cached_symbol: Optional[str] = None
        self._cached_timeframe: Optional[str] = None
        self._cached_df: Optional[pd.DataFrame] = None
        self._cached_features: Optional[np.ndarray] = None
        self._cached_regime: Optional[pd.Series] = None
        self._cached_split_dfs: Optional[dict[str, pd.DataFrame]] = None
        self._cached_train_idx: Optional[np.ndarray] = None
        self._cached_val_idx: Optional[np.ndarray] = None

    # ── Public API ────────────────────────────────────────────────────────

    def optimize(
        self,
        symbol: str,
        timeframe: str,
        n_trials: int = 50,
        n_seeds: int = 3,
        total_timesteps: int = 100_000,
    ) -> OptunaResult:
        """Run Optuna study optimizing composite objective.

        Parameters
        ----------
        symbol : str
            Trading symbol.
        timeframe : str
            Timeframe.
        n_trials : int
            Number of Optuna trials.
        n_seeds : int
            Number of seeds per trial for robustness.
        total_timesteps : int
            Training timesteps per trial.
        """
        logger.info(
            "Starting hyperparameter search: %s %s | trials=%d seeds=%d",
            symbol, timeframe, n_trials, n_seeds,
        )
        overall_start = time.time()

        # Pre-load data (cached across trials)
        self._prepare_data(symbol, timeframe)

        # Create study
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42),
            pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=2),
            study_name=f"{symbol}_{timeframe}_hpsearch",
        )

        # Run optimization
        study.optimize(
            lambda trial: self._objective(
                trial, symbol, timeframe, n_seeds, total_timesteps,
            ),
            n_trials=n_trials,
            show_progress_bar=True,
            gc_after_trial=True,
        )

        elapsed = time.time() - overall_start

        # Collect all trial results
        all_trials = []
        for t in study.trials:
            if t.state == optuna.trial.TrialState.COMPLETE:
                all_trials.append({
                    "number": t.number,
                    "value": t.value,
                    "params": t.params,
                    "state": str(t.state),
                })

        result = OptunaResult(
            best_params=study.best_params,
            best_value=study.best_value,
            study=study,
            all_trials=all_trials,
            n_completed=len(all_trials),
            elapsed_seconds=round(elapsed, 2),
        )

        logger.info(
            "Optuna search complete: best_value=%.4f | %d trials in %.1fs",
            result.best_value,
            result.n_completed,
            elapsed,
        )

        return result

    def get_best_config(self, optuna_result: OptunaResult) -> AgentConfig:
        """Convert best Optuna trial parameters to AgentConfig."""
        p = optuna_result.best_params
        return AgentConfig(
            algorithm=self.config.get("algorithm", "PPO"),
            learning_rate=p["learning_rate"],
            gamma=p["gamma"],
            batch_size=p["batch_size"],
            n_steps=p["n_steps"],
            ent_coef=p["entropy_coeff"],
            clip_range=p["clip_range"],
            hidden_sizes=(p["hidden_size"],) * 2,
            verbose=0,
        )

    # ── Optuna Objective ──────────────────────────────────────────────────

    def _objective(
        self,
        trial: Trial,
        symbol: str,
        timeframe: str,
        n_seeds: int,
        total_timesteps: int,
    ) -> float:
        """Optuna objective function.

        Composite objective:
            sharpe * 0.3 + sortino * 0.2 + calmar * 0.2
            - max_dd * 0.2 - (1 / profit_factor) * 0.1

        Strongly penalizes max_dd > 15%.
        """
        # Sample hyperparameters
        lr = trial.suggest_float("learning_rate", 1e-5, 1e-3, log=True)
        gamma = trial.suggest_float("gamma", 0.95, 0.999)
        batch_size = trial.suggest_categorical("batch_size", [32, 64, 128, 256])
        n_steps = trial.suggest_categorical("n_steps", [1024, 2048, 4096])
        entropy = trial.suggest_float("entropy_coeff", 1e-4, 0.05, log=True)
        clip = trial.suggest_float("clip_range", 0.1, 0.3)
        hidden = trial.suggest_categorical("hidden_size", [64, 128, 256])
        lookback = trial.suggest_categorical("lookback", [32, 64, 128])
        sl_mult = trial.suggest_float("sl_atr_multiplier", 1.5, 3.0)
        tp_mult = trial.suggest_float("tp_atr_multiplier", 2.0, 5.0)

        # Environment config for this trial
        env_cfg = EnvConfig(
            lookback=lookback,
            initial_balance=self.env_config.initial_balance,
            risk_per_trade=self.env_config.risk_per_trade,
            max_positions=self.env_config.max_positions,
            spread_pips=self.env_config.spread_pips,
            slippage_pips=self.env_config.slippage_pips,
            commission_per_lot=self.env_config.commission_per_lot,
            sl_atr_multiplier=sl_mult,
            tp_atr_multiplier=tp_mult,
        )

        # Run with multiple seeds
        seed_sharpes: list[float] = []
        seed_sortinos: list[float] = []
        seed_calmars: list[float] = []
        seed_dds: list[float] = []
        seed_pfs: list[float] = []

        for seed in range(n_seeds):
            actual_seed = 42 + seed * 7

            agent_cfg = AgentConfig(
                algorithm=self.config.get("algorithm", "PPO"),
                learning_rate=lr,
                gamma=gamma,
                batch_size=batch_size,
                n_steps=n_steps,
                ent_coef=entropy,
                clip_range=clip,
                hidden_sizes=(hidden,) * 2,
                verbose=0,
                seed=actual_seed,
            )

            # Create env from cached data
            train_df = self._cached_split_dfs["train"]
            val_df = self._cached_split_dfs["val"]

            train_feats = self._cached_features[self._cached_train_idx]
            val_feats = self._cached_features[self._cached_val_idx]

            train_regime = self._cached_regime.iloc[self._cached_train_idx].reset_index(drop=True)
            val_regime = self._cached_regime.iloc[self._cached_val_idx].reset_index(drop=True)

            train_env = ForexTradingEnv(train_df, env_cfg, train_feats, train_regime)
            val_env = ForexTradingEnv(val_df, env_cfg, val_feats, val_regime)

            # Train and evaluate
            agent = AgentFactory.create(agent_cfg)
            agent.train(train_env, total_timesteps=total_timesteps)

            eval_result = Evaluator.evaluate_agent(agent, val_env, n_episodes=10)

            seed_sharpes.append(eval_result.mean_sharpe)
            seed_dds.append(eval_result.mean_max_dd)

            # Approximate sortino and calmar from available data
            sortino = eval_result.mean_sharpe * 0.8 if eval_result.mean_sharpe > 0 else eval_result.mean_sharpe * 1.2
            calmar = eval_result.mean_reward / max(eval_result.mean_max_dd, 1e-6) if eval_result.mean_max_dd > 0 else 0.0
            pf = 1.0 + eval_result.mean_win_rate if eval_result.mean_win_rate > 0 else 0.5

            seed_sortinos.append(sortino)
            seed_calmars.append(calmar)
            seed_pfs.append(pf)

            # Report intermediate value for pruning
            trial.report(float(np.mean(seed_sharpes)), step=seed)
            if trial.should_prune():
                raise optuna.TrialPruned()

        # Aggregate across seeds
        avg_sharpe = float(np.mean(seed_sharpes))
        avg_sortino = float(np.mean(seed_sortinos))
        avg_calmar = float(np.mean(seed_calmars))
        avg_dd = float(np.mean(seed_dds))
        avg_pf = float(np.mean(seed_pfs))

        # Composite objective
        objective = (
            avg_sharpe * 0.3
            + avg_sortino * 0.2
            + avg_calmar * 0.2
            - avg_dd * 0.2
            - (1.0 / max(avg_pf, 0.01)) * 0.1
        )

        # Strong penalty for excessive drawdown
        if avg_dd > 0.15:
            objective -= 10.0 * (avg_dd - 0.15)

        logger.debug(
            "Trial %d: objective=%.4f sharpe=%.3f dd=%.3f pf=%.3f",
            trial.number, objective, avg_sharpe, avg_dd, avg_pf,
        )

        return objective

    # ── Data Preparation ──────────────────────────────────────────────────

    def _prepare_data(self, symbol: str, timeframe: str) -> None:
        """Load and cache data for reuse across trials."""
        if self._cached_symbol == symbol and self._cached_timeframe == timeframe:
            return

        logger.info("Loading and caching data for %s %s", symbol, timeframe)

        raw_df = self.data_loader.load_from_mt5(
            symbol, timeframe,
            self.train_dates["start"],
            self.val_dates["end"],
        )
        clean_df = self.data_cleaner.clean(raw_df)
        features = _compute_all_features(clean_df)
        regime_labels = _compute_regime_labels(clean_df)

        splitter = DataSplitter()
        split_dfs = splitter.split(
            clean_df, self.train_dates, self.val_dates,
            {"start": "2099-01-01", "end": "2099-12-31"},
        )

        train_df = split_dfs["train"]
        val_df = split_dfs["val"]
        train_idx = clean_df.index.get_indexer(train_df.index)
        val_idx = clean_df.index.get_indexer(val_df.index)

        self._cached_symbol = symbol
        self._cached_timeframe = timeframe
        self._cached_df = clean_df
        self._cached_features = features
        self._cached_regime = regime_labels
        self._cached_split_dfs = split_dfs
        self._cached_train_idx = train_idx
        self._cached_val_idx = val_idx

        logger.info(
            "Cached data: train=%d val=%d total_features=%d",
            len(train_df), len(val_df), features.shape[1],
        )

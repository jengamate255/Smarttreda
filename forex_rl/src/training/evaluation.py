"""Comprehensive evaluation utilities for RL trading agents."""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

from ..agents.factory import AgentConfig, AgentFactory, BaseAgent
from ..backtest.metrics import BacktestMetrics, TradeResult
from ..environment.forex_env import ForexTradingEnv

logger = logging.getLogger(__name__)


class Evaluator:
    """Static evaluation methods for RL trading agents.

    Provides agent evaluation, regime-specific analysis, seed stability
    testing, and feature importance computation.
    """

    @staticmethod
    def evaluate_agent(
        agent: BaseAgent,
        env: ForexTradingEnv,
        n_episodes: int = 100,
    ) -> Any:
        """Run agent for n episodes, collect comprehensive stats.

        Tracks equity curves, trade outcomes, drawdown, Sharpe ratio,
        and win rate across all episodes. Returns an EvalResult.
        """
        from ..agents.policy import EvalResult

        all_rewards: list[float] = []
        all_win_rates: list[float] = []
        all_sharpes: list[float] = []
        all_max_dds: list[float] = []
        all_trade_counts: list[int] = []
        all_equity_curves: list[list[float]] = []

        for ep in range(n_episodes):
            obs, _ = env.reset()
            done = False
            episode_reward = 0.0
            equity_curve = [env.cfg.initial_balance]
            trades_taken = 0
            wins = 0
            current_position_side: int = 0
            entry_price: float = 0.0
            entry_step: int = 0
            equity = env.cfg.initial_balance

            while not done:
                action = agent.predict(obs, deterministic=True)
                obs, reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated
                episode_reward += float(reward)

                # Track position changes for trade counting
                pos = info.get("position")
                pos_side = pos["side"] if pos and isinstance(pos, dict) else 0

                if pos_side != 0 and current_position_side == 0:
                    # Position opened
                    current_position_side = pos_side
                    entry_price = pos["entry_price"]
                    entry_step = info.get("episode_step", 0)
                elif pos_side == 0 and current_position_side != 0:
                    # Position closed
                    trades_taken += 1
                    current_price = info.get("current_price", entry_price)
                    pnl = current_position_side * (current_price - entry_price)
                    if pnl > 0:
                        wins += 1
                    current_position_side = 0

                equity_curve.append(info.get("equity", equity_curve[-1]))

            all_rewards.append(episode_reward)
            all_trade_counts.append(trades_taken)
            all_win_rates.append(wins / max(trades_taken, 1))
            all_equity_curves.append(equity_curve)

            # Per-episode Sharpe
            eq = np.array(equity_curve)
            if len(eq) > 1:
                returns = np.diff(eq) / np.where(eq[:-1] != 0, eq[:-1], 1.0)
                std = float(np.std(returns))
                mean_r = float(np.mean(returns))
                sharpe = mean_r / std * np.sqrt(252) if std > 1e-10 else 0.0
            else:
                sharpe = 0.0
            all_sharpes.append(sharpe)

            # Per-episode max drawdown
            running_max = np.maximum.accumulate(eq)
            dd = (running_max - eq) / np.where(running_max != 0, running_max, 1.0)
            max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0
            all_max_dds.append(max_dd)

        return EvalResult(
            mean_reward=float(np.mean(all_rewards)),
            std_reward=float(np.std(all_rewards)),
            min_reward=float(np.min(all_rewards)),
            max_reward=float(np.max(all_rewards)),
            mean_trades=float(np.mean(all_trade_counts)),
            mean_win_rate=float(np.mean(all_win_rates)),
            mean_sharpe=float(np.mean(all_sharpes)),
            mean_max_dd=float(np.mean(all_max_dds)),
            n_episodes=n_episodes,
        )

    @staticmethod
    def regime_evaluation(
        agent: BaseAgent,
        env: ForexTradingEnv,
        regime_labels: pd.Series,
    ) -> dict[str, Any]:
        """Evaluate agent performance per market regime.

        Parameters
        ----------
        agent : BaseAgent
            Trained agent.
        env : ForexTradingEnv
            Environment (will be reset and run through once).
        regime_labels : pd.Series
            Regime label per bar (from RegimeDetector.detect()).

        Returns
        -------
        dict mapping regime name → performance metrics.
        """
        obs, _ = env.reset()
        done = False
        regime_stats: dict[str, dict[str, Any]] = {}

        while not done:
            action = agent.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated

            step = info.get("current_step", 0)
            if step < len(regime_labels):
                regime = str(regime_labels.iloc[step])
            else:
                regime = "unknown"

            if regime not in regime_stats:
                regime_stats[regime] = {
                    "rewards": [],
                    "equity_changes": [],
                    "n_steps": 0,
                }

            regime_stats[regime]["rewards"].append(float(reward))
            regime_stats[regime]["n_steps"] += 1

            # Track equity change
            equity = info.get("equity", 0.0)
            regime_stats[regime]["equity_changes"].append(equity)

        # Compute per-regime metrics
        result: dict[str, Any] = {}
        for regime, stats in regime_stats.items():
            rewards = np.array(stats["rewards"])
            eq = np.array(stats["equity_changes"])

            mean_reward = float(np.mean(rewards)) if len(rewards) > 0 else 0.0
            total_return = float((eq[-1] - eq[0]) / eq[0]) if len(eq) > 1 and eq[0] > 0 else 0.0

            # Sharpe within regime
            if len(rewards) > 1 and float(np.std(rewards)) > 0:
                sharpe = float(np.mean(rewards) / np.std(rewards) * np.sqrt(252))
            else:
                sharpe = 0.0

            # Max drawdown within regime
            if len(eq) > 1:
                running_max = np.maximum.accumulate(eq)
                dd = (running_max - eq) / np.where(running_max != 0, running_max, 1.0)
                max_dd = float(np.max(dd))
            else:
                max_dd = 0.0

            # Win rate (reward > 0)
            wins = int(np.sum(rewards > 0))
            win_rate = wins / len(rewards) if len(rewards) > 0 else 0.0

            result[regime] = {
                "n_steps": stats["n_steps"],
                "mean_reward": mean_reward,
                "total_return": total_return,
                "sharpe": sharpe,
                "max_drawdown": max_dd,
                "win_rate": win_rate,
            }

        return result

    @staticmethod
    def seed_stability(
        agent_config: AgentConfig,
        env: ForexTradingEnv,
        n_seeds: int = 5,
        total_timesteps: int = 50_000,
    ) -> dict[str, Any]:
        """Train with n different seeds, report stability metrics.

        Parameters
        ----------
        agent_config : AgentConfig
            Base configuration (seed will be overridden per run).
        env : ForexTradingEnv
            Environment to train and evaluate on.
        n_seeds : int
            Number of different seeds to test.
        total_timesteps : int
            Training timesteps per seed.

        Returns
        -------
        dict with keys: sharpes, returns, max_dds, win_rates,
        mean_sharpe, std_sharpe, stability_score, recommendation.
        """
        sharpes: list[float] = []
        returns: list[float] = []
        max_dds: list[float] = []
        win_rates: list[float] = []
        rewards_list: list[float] = []

        for seed in range(n_seeds):
            actual_seed = seed * 17 + 42
            cfg = AgentConfig(
                algorithm=agent_config.algorithm,
                learning_rate=agent_config.learning_rate,
                gamma=agent_config.gamma,
                batch_size=agent_config.batch_size,
                n_steps=agent_config.n_steps,
                ent_coef=agent_config.ent_coef,
                clip_range=agent_config.clip_range,
                hidden_sizes=agent_config.hidden_sizes,
                verbose=0,
                seed=actual_seed,
            )

            agent = AgentFactory.create(cfg)
            agent.train(env, total_timesteps=total_timesteps)

            eval_result = Evaluator.evaluate_agent(agent, env, n_episodes=10)

            sharpes.append(eval_result.mean_sharpe)
            returns.append(eval_result.mean_reward)
            max_dds.append(eval_result.mean_max_dd)
            win_rates.append(eval_result.mean_win_rate)
            rewards_list.append(eval_result.mean_reward)

        mean_sharpe = float(np.mean(sharpes))
        std_sharpe = float(np.std(sharpes))
        mean_return = float(np.mean(returns))
        mean_dd = float(np.mean(max_dds))
        mean_wr = float(np.mean(win_rates))

        # Stability score: coefficient of variation of Sharpe (lower is more stable)
        stability_score = std_sharpe / max(abs(mean_sharpe), 1e-6)

        # Recommendation
        if mean_sharpe > 1.0 and stability_score < 0.5 and mean_dd < 0.15:
            recommendation = "STABLE_PROFITABLE"
        elif mean_sharpe > 0.5 and stability_score < 1.0:
            recommendation = "MARGINALLY_STABLE"
        elif stability_score > 2.0:
            recommendation = "UNSTABLE"
        else:
            recommendation = "NEEDS_MORE_TUNING"

        return {
            "n_seeds": n_seeds,
            "sharpes": sharpes,
            "returns": returns,
            "max_dds": max_dds,
            "win_rates": win_rates,
            "mean_sharpe": mean_sharpe,
            "std_sharpe": std_sharpe,
            "mean_return": mean_return,
            "mean_max_dd": mean_dd,
            "mean_win_rate": mean_wr,
            "stability_score": stability_score,
            "recommendation": recommendation,
        }

    @staticmethod
    def feature_importance(
        agent: BaseAgent,
        env: ForexTradingEnv,
        n_episodes: int = 10,
        method: str = "permutation",
    ) -> dict[str, int]:
        """Compute feature importance via perturbation.

        For each feature, zeros out that column in the observation and
        measures the impact on mean reward. Features are ranked by the
        magnitude of reward degradation.

        Parameters
        ----------
        agent : BaseAgent
            Trained agent.
        env : ForexTradingEnv
            Environment with normalized observations.
        n_episodes : int
            Episodes per feature perturbation.
        method : str
            'permutation' (zero-out) or 'noise' (add Gaussian noise).

        Returns
        -------
        dict mapping feature_index → importance_rank (0 = most important).
        """
        n_features = env.n_total_features

        # Baseline performance
        baseline_result = Evaluator.evaluate_agent(agent, env, n_episodes=n_episodes)
        baseline_reward = baseline_result.mean_reward

        feature_impacts: list[tuple[int, float]] = []

        for feat_idx in range(n_features):
            total_impact = 0.0

            for _ in range(n_episodes):
                obs, _ = env.reset()
                done = False
                episode_reward = 0.0

                # Save original observation
                obs_copy = obs.copy()

                while not done:
                    # Perturb the feature
                    if method == "permutation":
                        obs[:, feat_idx] = 0.0
                    elif method == "noise":
                        obs[:, feat_idx] += np.random.normal(0, 0.1, size=obs.shape[0])

                    action = agent.predict(obs, deterministic=True)
                    obs, reward, terminated, truncated, info = env.step(action)
                    done = terminated or truncated
                    episode_reward += float(reward)

                    # Restore and re-perturb for next step
                    obs = obs_copy.copy()

                total_impact += episode_reward

            mean_impact = total_impact / n_episodes
            impact = abs(baseline_reward - mean_impact)
            feature_impacts.append((feat_idx, impact))

        # Sort by impact (descending) and assign ranks
        feature_impacts.sort(key=lambda x: x[1], reverse=True)
        importance: dict[int, int] = {}
        for rank, (feat_idx, _) in enumerate(feature_impacts):
            importance[feat_idx] = rank

        return importance

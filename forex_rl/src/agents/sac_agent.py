from __future__ import annotations

import time
from typing import Any, Optional

import numpy as np
from stable_baselines3 import SAC
from stable_baselines3.common.callbacks import BaseCallback, EvalCallback
from stable_baselines3.common.vec_env import DummyVecEnv

from .policy import AgentConfig, BaseAgent, EvalResult, TrainingResult


class SACAgent(BaseAgent):
    def __init__(self, config: AgentConfig):
        self.config = config
        self.model: Optional[SAC] = None

    def _build_model(self, env: Any) -> SAC:
        c = self.config
        policy_kwargs: dict[str, Any] = dict(
            net_arch=list(c.hidden_sizes),
        )
        return SAC(
            policy="MlpPolicy",
            env=env,
            learning_rate=c.learning_rate,
            buffer_size=c.buffer_size,
            learning_starts=c.learning_starts,
            batch_size=c.batch_size,
            tau=c.tau,
            gamma=c.gamma,
            train_freq=c.train_freq,
            gradient_steps=c.gradient_steps,
            tensorboard_log=c.tensorboard_log,
            verbose=c.verbose,
            seed=c.seed,
            device=c.device,
            policy_kwargs=policy_kwargs,
        )

    def train(
        self,
        env: Any,
        total_timesteps: int,
        eval_env: Any | None = None,
        callback: Any | None = None,
    ) -> TrainingResult:
        if self.model is None or env is not self.model.env:
            self.model = self._build_model(env)

        callbacks: list[BaseCallback] = []
        if callback is not None:
            callbacks.append(callback)

        eval_callback: Optional[EvalCallback] = None
        if eval_env is not None:
            eval_callback = EvalCallback(
                eval_env,
                best_model_save_path=self.config.tensorboard_log or "./logs/best_model",
                log_path=self.config.tensorboard_log or "./logs",
                eval_freq=10_000,
                n_eval_episodes=5,
                deterministic=True,
                verbose=self.config.verbose,
            )
            callbacks.append(eval_callback)

        start = time.perf_counter()
        self.model.learn(
            total_timesteps=total_timesteps,
            callback=callbacks if callbacks else None,
            progress_bar=False,
        )
        elapsed = time.perf_counter() - start

        eval_result = self.evaluate(env, n_episodes=10)

        return TrainingResult(
            total_timesteps=total_timesteps,
            total_episodes=eval_result.n_episodes,
            best_reward=eval_result.max_reward,
            mean_reward=eval_result.mean_reward,
            std_reward=eval_result.std_reward,
            training_time=elapsed,
        )

    def predict(
        self, observation: np.ndarray, deterministic: bool = True
    ) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("Model not initialised. Call train() or load() first.")
        action, _ = self.model.predict(observation, deterministic=deterministic)
        return action

    def save(self, path: str) -> None:
        if self.model is None:
            raise RuntimeError("No model to save.")
        self.model.save(path)

    def load(self, path: str) -> None:
        self.model = SAC.load(path)

    def evaluate(self, env: Any, n_episodes: int = 10) -> EvalResult:
        if self.model is None:
            raise RuntimeError("Model not initialised.")

        rewards: list[float] = []
        trade_counts: list[int] = []
        win_counts: list[int] = []
        sharpe_list: list[float] = []
        dd_list: list[float] = []

        for _ in range(n_episodes):
            obs, _ = env.reset()
            done = False
            episode_reward = 0.0
            trades = 0
            wins = 0
            equity_curve = [1.0]

            while not done:
                action, _ = self.model.predict(obs, deterministic=True)
                obs, reward, terminated, truncated, info = env.step(action)
                done = terminated or truncated
                episode_reward += float(reward)

                if "position" in info:
                    pos = info["position"]
                    if pos != 0:
                        trades += 1
                        if float(reward) > 0:
                            wins += 1

                equity_curve.append(equity_curve[-1] + float(reward))

            rewards.append(episode_reward)
            trade_counts.append(trades)
            win_counts.append(wins / max(trades, 1))

            eq = np.array(equity_curve)
            rets = np.diff(eq) / eq[:-1]
            if len(rets) > 1 and np.std(rets) > 0:
                sharpe_list.append(float(np.mean(rets) / np.std(rets) * np.sqrt(252)))
            else:
                sharpe_list.append(0.0)

            running_max = np.maximum.accumulate(eq)
            dd = (running_max - eq) / running_max
            dd_list.append(float(np.max(dd)) if len(dd) > 0 else 0.0)

        return EvalResult(
            mean_reward=float(np.mean(rewards)),
            std_reward=float(np.std(rewards)),
            min_reward=float(np.min(rewards)),
            max_reward=float(np.max(rewards)),
            mean_trades=float(np.mean(trade_counts)),
            mean_win_rate=float(np.mean(win_counts)),
            mean_sharpe=float(np.mean(sharpe_list)),
            mean_max_dd=float(np.mean(dd_list)),
            n_episodes=n_episodes,
        )

    def get_action_distribution(self, observation: np.ndarray) -> dict[str, Any]:
        if self.model is None:
            raise RuntimeError("Model not initialised.")

        obs = np.expand_dims(observation, axis=0)
        self.model.policy.set_training_mode(False)

        with np.no_grad():
            obs_tensor = self.model.policy.obs_to_tensor(obs)[0]
            mean_actions, log_std, _ = self.model.policy.get_action_params(obs_tensor)
            std = log_std.exp()

        return {
            "mean": mean_actions.detach().cpu().numpy().flatten(),
            "std": std.detach().cpu().numpy().flatten(),
        }

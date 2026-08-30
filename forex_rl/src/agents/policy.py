from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np


@dataclass
class TrainingResult:
    total_timesteps: int
    total_episodes: int
    best_reward: float
    mean_reward: float
    std_reward: float
    training_time: float


@dataclass
class EvalResult:
    mean_reward: float
    std_reward: float
    min_reward: float
    max_reward: float
    mean_trades: float
    mean_win_rate: float
    mean_sharpe: float
    mean_max_dd: float
    n_episodes: int


@dataclass
class AgentConfig:
    algorithm: str = "PPO"
    learning_rate: float = 3e-4
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_range: float = 0.2
    ent_coef: float = 0.01
    vf_coef: float = 0.5
    max_grad_norm: float = 0.5
    n_steps: int = 2048
    batch_size: int = 64
    n_epochs: int = 10
    hidden_sizes: tuple[int, ...] = (256, 256)
    device: str = "auto"
    tensorboard_log: Optional[str] = None
    verbose: int = 1
    seed: int = 42
    buffer_size: int = 300_000
    learning_starts: int = 1000
    tau: float = 0.005
    train_freq: int = 1
    gradient_steps: int = -1


class BaseAgent(ABC):
    @abstractmethod
    def train(
        self,
        env: Any,
        total_timesteps: int,
        eval_env: Any | None = None,
        callback: Any | None = None,
    ) -> TrainingResult:
        ...

    @abstractmethod
    def predict(
        self, observation: np.ndarray, deterministic: bool = True
    ) -> np.ndarray:
        ...

    @abstractmethod
    def save(self, path: str) -> None:
        ...

    @abstractmethod
    def load(self, path: str) -> None:
        ...

    @abstractmethod
    def evaluate(self, env: Any, n_episodes: int = 10) -> EvalResult:
        ...

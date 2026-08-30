from __future__ import annotations

from .policy import AgentConfig, BaseAgent
from .ppo_agent import PPOAgent
from .sac_agent import SACAgent


class AgentFactory:
    @staticmethod
    def create(config: AgentConfig) -> BaseAgent:
        algo = config.algorithm.upper()
        if algo == "PPO":
            return PPOAgent(config)
        elif algo == "SAC":
            return SACAgent(config)
        else:
            raise ValueError(f"Unknown algorithm: {config.algorithm}")

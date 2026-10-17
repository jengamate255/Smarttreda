from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from trading_ai.models.schemas import Signal
from trading_ai.strategies.genome.schema import StrategyGenome


class BaseStrategy(ABC):

    def __init__(self, genome: StrategyGenome):
        self.genome = genome

    @abstractmethod
    def analyze(
        self,
        df_multi_timeframe: dict[str, pd.DataFrame],
        market_state: dict,
    ) -> Optional[Signal]:
        ...

    def validate_signal(self, signal: Signal) -> bool:
        if signal.pips_risk <= 0:
            return False
        rr = signal.risk_reward_ratio or signal.risk_reward
        if rr <= 0:
            risk = abs(signal.entry_price - signal.stop_loss)
            reward = abs(signal.take_profit - signal.entry_price)
            rr = reward / risk if risk > 0 else 0.0
        if rr < 1.0:
            return False
        if signal.confidence < 0.0 or signal.confidence > 1.0:
            return False
        if signal.symbol not in self.genome.market and "*" not in self.genome.market:
            return False
        if signal.stop_loss == signal.entry_price:
            return False
        if signal.take_profit == signal.entry_price:
            return False
        return True

    def get_version(self) -> str:
        return f"{self.genome.strategy_id}-v{self.genome.version}"

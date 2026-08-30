from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .metrics import TradeResult


@dataclass
class MonteCarloResult:
    median_return: float
    mean_return: float
    percentile_5: float
    percentile_95: float
    worst_drawdown: float
    probability_of_ruin: float
    distribution: np.ndarray


class MonteCarloSimulator:
    def __init__(self, n_simulations: int = 5000, seed: int = 42):
        self.n_simulations = n_simulations
        self.rng = np.random.default_rng(seed)

    def simulate(
        self,
        trades: list[TradeResult],
        initial_balance: float = 10_000.0,
        slippage_std: float = 0.2,
        spread_std: float = 0.3,
    ) -> MonteCarloResult:
        if not trades:
            return MonteCarloResult(
                median_return=0.0,
                mean_return=0.0,
                percentile_5=0.0,
                percentile_95=0.0,
                worst_drawdown=0.0,
                probability_of_ruin=0.0,
                distribution=np.array([]),
            )

        base_pnls = np.array([t.pnl for t in trades])
        n_trades = len(base_pnls)

        final_returns = np.zeros(self.n_simulations)
        worst_dds = np.zeros(self.n_simulations)

        for sim in range(self.n_simulations):
            indices = self.rng.integers(0, n_trades, size=n_trades)
            resampled_pnls = base_pnls[indices].copy()

            slippage_noise = self.rng.normal(0, slippage_std, size=n_trades)
            spread_noise = self.rng.normal(0, spread_std, size=n_trades)
            resampled_pnls -= slippage_noise
            resampled_pnls -= spread_noise

            equity = initial_balance
            equity_path = np.empty(n_trades + 1)
            equity_path[0] = equity
            for i, pnl in enumerate(resampled_pnls):
                equity += pnl
                equity_path[i + 1] = equity

            final_returns[sim] = (equity - initial_balance) / initial_balance

            running_max = np.maximum.accumulate(equity_path)
            dd = (running_max - equity_path) / running_max
            worst_dds[sim] = float(np.max(dd)) if len(dd) > 0 else 0.0

        ruin_count = int(np.sum(final_returns < -0.5))
        prob_ruin = ruin_count / self.n_simulations

        return MonteCarloResult(
            median_return=float(np.median(final_returns)),
            mean_return=float(np.mean(final_returns)),
            percentile_5=float(np.percentile(final_returns, 5)),
            percentile_95=float(np.percentile(final_returns, 95)),
            worst_drawdown=float(np.max(worst_dds)),
            probability_of_ruin=prob_ruin,
            distribution=final_returns,
        )

    def compute_ruin_probability(self, result: MonteCarloResult) -> float:
        return result.probability_of_ruin

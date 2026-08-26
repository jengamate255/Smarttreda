"""Trading agent: combines models + regime + setup + direction + TP-before-SL +
spread + session + risk into a final decision.

The agent NEVER sizes positions (risk engine does) and NEVER places orders
(executor does). Its output is a structured decision dict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from src.config import Config


@dataclass
class Decision:
    signal: str  # BUY | SELL | NO_TRADE
    reasons: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class TradingAgent:
    """Gated decision maker. Feed it a feature row + model probabilities."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.min_confidence = float(cfg.get("agent.min_confidence", 0.65))
        self.min_tp_probability = float(cfg.get("agent.min_tp_probability", 0.65))
        self.max_spread_pips = float(cfg.get("agent.max_spread_pips", 2.5))
        self.min_candidate_score = float(cfg.get("agent.min_candidate_score", 0.4))

    def evaluate(
        self,
        row: dict[str, Any],
        market_regime: str = "UNKNOWN",
        session: str = "UNKNOWN",
        spread_pips: float = 0.0,
    ) -> Decision:
        """row keys: setup_probability, buy_probability, sell_probability,
        no_trade_probability, tp_before_sl_probability."""
        reasons: list[str] = []
        contradictions: list[str] = []

        setup = float(row.get("setup_probability", 0.0))
        buy = float(row.get("buy_probability", 0.0))
        sell = float(row.get("sell_probability", 0.0))
        no_trade = float(row.get("no_trade_probability", 0.0))
        tp_before_sl = float(row.get("tp_before_sl_probability", 0.0))

        if setup < self.min_confidence:
            return Decision(
                "NO_TRADE",
                reasons=[f"setup_probability {setup:.2f} < {self.min_confidence}"],
                raw=row,
            )

        direction = "BUY" if max(buy, sell, no_trade) == buy and buy >= self.min_confidence else (
            "SELL" if max(buy, sell, no_trade) == sell and sell >= self.min_confidence else "NO_TRADE"
        )
        if direction == "NO_TRADE":
            reasons.append(
                f"no direction meets min_confidence ({self.min_confidence}): "
                f"buy {buy:.2f}, sell {sell:.2f}, no_trade {no_trade:.2f}"
            )

        if tp_before_sl < self.min_tp_probability:
            contradictions.append(f"tp_before_sl {tp_before_sl:.2f} < {self.min_tp_probability}")

        if spread_pips > self.max_spread_pips:
            contradictions.append(f"spread {spread_pips:.2f} > {self.max_spread_pips}")

        if market_regime == "TRENDING_DOWN" and direction == "BUY":
            contradictions.append("BUY against TRENDING_DOWN regime")
        if market_regime == "TRENDING_UP" and direction == "SELL":
            contradictions.append("SELL against TRENDING_UP regime")

        if direction != "NO_TRADE":
            reasons.append(f"direction={direction} (buy {buy:.2f}, sell {sell:.2f})")
            reasons.append(f"setup={setup:.2f}, tp_before_sl={tp_before_sl:.2f}")
            reasons.append(f"regime={market_regime}, session={session}, spread={spread_pips:.2f}")

        score = (
            setup * 0.3
            + (buy if direction == "BUY" else sell if direction == "SELL" else no_trade) * 0.3
            + tp_before_sl * 0.4
        )
        if contradictions:
            score *= 0.5
        if score < self.min_candidate_score or contradictions and direction == "NO_TRADE":
            return Decision("NO_TRADE", reasons, contradictions, raw=row)

        if contradictions:
            return Decision("NO_TRADE", reasons, contradictions, raw=row)
        return Decision(direction, reasons, contradictions, raw=row)


def market_regime_from_features(features: pd.DataFrame, idx: int) -> str:
    if "structure_state" not in features.columns:
        return "UNKNOWN"
    v = features["structure_state"].iloc[idx]
    if v == 1:
        return "TRENDING_UP"
    if v == -1:
        return "TRENDING_DOWN"
    return "RANGING"


def session_from_features(features: pd.DataFrame, idx: int) -> str:
    if "session_london" in features.columns and "session_new_york" in features.columns:
        if features["session_london"].iloc[idx] and features["session_new_york"].iloc[idx]:
            return "LONDON_NY_OVERLAP"
        if features["session_london"].iloc[idx]:
            return "LONDON"
        if features["session_new_york"].iloc[idx]:
            return "NEW_YORK"
    return "OTHER"

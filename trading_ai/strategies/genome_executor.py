"""Genome-driven strategy executor: turns a StrategyGenome into a backtestable callable."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

import pandas as pd

from trading_ai.config.settings import settings
from trading_ai.execution.prop_filters import check_pre_trade, compute_adr_pips, current_spread_pips
from trading_ai.models import Signal, Side
from trading_ai.strategies.genome.schema import StrategyGenome
from trading_ai.strategies.supply_demand import SupplyDemandStrategy
from trading_ai.strategies.trend import TrendAgent
from trading_ai.strategies.breakout import BreakoutAgent
from trading_ai.strategies.momentum import MomentumAgent
from trading_ai.strategies.mean_reversion import MeanReversionAgent
from trading_ai.strategies.volatility import VolatilityAgent

_STRATEGY_CLASSES = {
    "supply_demand": SupplyDemandStrategy,
    "trend": TrendAgent,
    "breakout": BreakoutAgent,
    "momentum": MomentumAgent,
    "mean_reversion": MeanReversionAgent,
    "volatility": VolatilityAgent,
}


def _pick_strategy_class(genome: StrategyGenome) -> type:
    """Choose the strategy class from genome entry_rules type, default supply_demand."""
    for rule in genome.entry_rules:
        rtype = str(rule.get("type", "")).lower()
        if rtype in _STRATEGY_CLASSES:
            return _STRATEGY_CLASSES[rtype]
    return SupplyDemandStrategy


def _rule_params(genome: StrategyGenome, rule_type: str) -> dict:
    for rule in genome.entry_rules + genome.exit_rules + genome.risk_rules:
        if str(rule.get("type", "")).lower() == rule_type:
            return dict(rule.get("params") or {})
    return {}


def _apply_genome_rules(genome: StrategyGenome, signal: Signal) -> Optional[Signal]:
    """Apply genome entry/exit/risk rules as post-filters so mutations alter results."""
    entry_params = _rule_params(genome, "supply_demand")
    exit_params = _rule_params(genome, "risk_reward")
    risk_params = _rule_params(genome, "stop_placement")

    # Entry confidence gate
    min_conf = float(entry_params.get("min_confidence", 0.0))
    if signal.metadata.get("confidence", 0.5) < min_conf:
        return None

    # Entry strength / RR gate from entry rules
    min_rr_entry = float(entry_params.get("min_rr", 0.0))
    if min_rr_entry > 0 and signal.risk_reward < min_rr_entry:
        return None

    # Exit / risk rules: enforce minimum reward:risk by extending TP or tightening SL
    min_rr = max(
        float(exit_params.get("min_rr", 0.0)),
        float(risk_params.get("min_rr", 0.0)),
    )
    if min_rr > 0 and signal.risk_reward > 0 and signal.risk_reward < min_rr:
        entry = signal.entry_price
        sl = signal.stop_loss
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        if signal.side == Side.BUY:
            signal.take_profit = entry + risk * min_rr
        else:
            signal.take_profit = entry - risk * min_rr
        signal.risk_reward = min_rr
        if hasattr(signal, "risk_reward_ratio"):
            signal.risk_reward_ratio = min_rr

    # Max risk distance (as fraction of entry) from stop_placement
    max_risk_frac = float(risk_params.get("max_risk_frac", 0.0))
    if max_risk_frac > 0:
        risk = abs(signal.entry_price - signal.stop_loss)
        if risk > signal.entry_price * max_risk_frac:
            return None

    return signal


def _pydantic_to_dataclass_signal(py_sig: Any, timestamp: datetime) -> Optional[Signal]:
    """Convert a Pydantic schemas.Signal to a models.Signal dataclass for BacktestEngine."""
    if py_sig is None:
        return None
    direction = getattr(py_sig, "direction", "BUY")
    side = Side.BUY if str(direction).upper() in ("BUY", "LONG") else Side.SELL
    entry = float(py_sig.entry_price)
    sl = float(py_sig.stop_loss)
    tp = float(py_sig.take_profit)
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = reward / risk if risk > 0 else 0.0
    return Signal(
        timestamp=timestamp,
        symbol=str(py_sig.symbol),
        side=side,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward=rr,
        metadata={
            "strategy_id": str(getattr(py_sig, "strategy_id", "")),
            "confidence": float(getattr(py_sig, "confidence", 0.5)),
            "reasoning": list(getattr(py_sig, "reasoning", []) or []),
        },
    )


def make_strategy_fn(
    genome: StrategyGenome,
    multi_timeframe_data: dict[str, pd.DataFrame],
    market_state: dict,
    bid_ask: Optional[dict[str, tuple[float, float]]] = None,
) -> Callable[..., Optional[Signal]]:
    """Build a BacktestEngine-compatible strategy callable from a genome.

    The returned callable receives (lookback_df, strategy_state) and returns
    a models.Signal or None. It reuses the multi-timeframe strategy analyze()
    logic, then applies genome entry/exit/risk rules so mutations change results.

    If `bid_ask` is provided (dict symbol -> (bid, ask)), pre-trade prop-firm
    filters (spread, ADR, session, allowed symbols) are applied before returning.
    """
    strat_cls = _pick_strategy_class(genome)
    strategy = strat_cls(genome)
    tf_items = list(multi_timeframe_data.items())
    throttle = max(1, int(_rule_params(genome, "supply_demand").get("eval_every", 5)))

    # Pre-fetch H1 data for ADR calc
    h1_data = multi_timeframe_data.get("H1")
    if h1_data is None:
        for k, v in multi_timeframe_data.items():
            if "H1" in k.upper():
                h1_data = v
                break

    def _strategy_fn(lookback: pd.DataFrame, state: dict[str, Any]) -> Optional[Signal]:
        if lookback is None or len(lookback) < 20:
            return None
        ts = lookback.index[-1] if hasattr(lookback.index, "__getitem__") else None
        if ts is None:
            return None
        n = state.get("_n", 0) + 1
        state["_n"] = n
        if n % throttle != 0:
            return None

        slice_mt: dict[str, pd.DataFrame] = {}
        for tf, frame in tf_items:
            try:
                slice_mt[tf] = frame[frame.index <= ts]
            except Exception:
                slice_mt[tf] = frame
        if any(len(v) < 30 for v in slice_mt.values()):
            return None

        ms = dict(market_state)
        ms.setdefault("symbol", genome.market[0] if genome.market else "EURUSD")
        # Live ATR from M5 for volatility filters
        m5 = slice_mt.get("M5")
        if m5 is not None and len(m5) >= 15:
            recent = m5.iloc[-14:]
            tr = (recent["high"] - recent["low"]).abs().mean()
            ms["atr"] = float(tr)
        try:
            py_sig = strategy.analyze(slice_mt, ms)
        except Exception:
            return None
        signal = _pydantic_to_dataclass_signal(py_sig, timestamp=pd.Timestamp(ts).to_pydatetime())
        if signal is None:
            return None
        signal = _apply_genome_rules(genome, signal)
        if signal is None:
            return None

        # ---- Prop-firm pre-trade filters ----
        if bid_ask:
            sym = signal.symbol
            if sym in bid_ask:
                bid, ask = bid_ask[sym]
                check = check_pre_trade(
                    symbol=sym,
                    bid=bid,
                    ask=ask,
                    df_h1=h1_data,
                    utc_now=pd.Timestamp(ts).to_pydatetime(),
                )
                if not check.allowed:
                    return None
        return signal

    return _strategy_fn

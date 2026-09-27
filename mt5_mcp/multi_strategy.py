"""Multi-strategy system — runs multiple strategies in parallel and picks the best.

Strategies:
1. trend_zone (existing) — trend + supply/demand zone + confirmation candle
2. mean_reversion — RSI extremes + Bollinger Band touches in ranging markets
3. momentum_breakout — ADX > 25 + volume spike + price breaking key levels

Each strategy returns a TradePlan. The system picks the highest-scoring valid plan.
Conflicting signals (BUY vs SELL on same symbol) are resolved by score.

Usage:
    from mt5_mcp.multi_strategy import evaluate_all_strategies
    plans = evaluate_all_strategies("EURUSD")
    best = max(plans, key=lambda p: p.trade_score if p.valid else -1)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from mt5_mcp.client import get_client
from mt5_mcp.common import TIMEFRAME_MT5, TradePlan, pip_size, pip_value_per_lot
from mt5_mcp.config import get_settings
from mt5_mcp.indicators import atr, rsi
from mt5_mcp.strategy import evaluate_strategy

logger = logging.getLogger("multi_strategy")


def _bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0):
    """Compute Bollinger Bands."""
    close = df["close"]
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    return upper, sma, lower


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — measures trend strength."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr_val = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr_val)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr_val)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(period).mean()
    return adx


def mean_reversion_strategy(
    symbol: str,
    risk_percent: float | None = None,
    timeframe: str = "H1",
) -> TradePlan:
    """Mean reversion: RSI extremes + Bollinger Band touches in ranging markets.

    BUY when RSI < 30 and price touches lower Bollinger Band.
    SELL when RSI > 70 and price touches upper Bollinger Band.
    Only in ranging markets (ADX < 25).
    """
    settings = get_settings()
    risk = settings.risk
    risk_percent = risk_percent if risk_percent is not None else risk.default_risk_percent

    plan = TradePlan(
        action="NONE", symbol=symbol, timeframe=timeframe,
        risk_percent=risk_percent,
    )

    try:
        client = get_client()
        client.ensure_connected()
        info = client.symbol_info(symbol)
        tick = client.tick(symbol)
    except Exception as exc:
        plan.errors.append(f"market data unavailable: {exc}")
        plan.reason = str(exc)
        return plan

    tf = timeframe.upper()
    if tf not in TIMEFRAME_MT5:
        plan.errors.append(f"unknown timeframe '{tf}'")
        plan.reason = "invalid timeframe"
        return plan

    try:
        frame = client.rates(symbol, tf, 200)
    except Exception as exc:
        plan.errors.append(f"no {tf} data: {exc}")
        plan.reason = str(exc)
        return plan

    if len(frame) < 50:
        plan.reason = "insufficient data"
        return plan

    bid = float(tick.get("bid", 0.0))
    ask = float(tick.get("ask", bid))

    # Compute indicators
    rsi_series = rsi(frame, 14)
    bb_upper, bb_mid, bb_lower = _bollinger_bands(frame, 20, 2.0)
    adx_series = _adx(frame, 14)

    if rsi_series is None or len(rsi_series) < 2:
        plan.reason = "RSI computation failed"
        return plan

    current_rsi = float(rsi_series.iloc[-1])
    current_adx = float(adx_series.iloc[-1]) if len(adx_series) > 0 else 30.0
    current_close = float(frame["close"].iloc[-1])
    current_bb_upper = float(bb_upper.iloc[-1]) if len(bb_upper) > 0 else current_close + 1
    current_bb_lower = float(bb_lower.iloc[-1]) if len(bb_lower) > 0 else current_close - 1

    # Only trade mean reversion in ranging markets (ADX < 25)
    if current_adx >= 25:
        plan.reason = f"market trending (ADX {current_adx:.1f} >= 25); mean reversion inactive"
        return plan

    pip = pip_size(info)

    # BUY signal: RSI < 30 and price near lower BB
    if current_rsi < 30 and current_close <= current_bb_lower * 1.002:
        action = "BUY"
        entry = ask
        sl = entry - 25 * pip  # 25 pip SL
        tp = entry + 50 * pip  # 50 pip TP (2:1 RR)
        sl_pips = 25.0
        tp_pips = 50.0
        confidence = min(80, (30 - current_rsi) * 2 + 50)  # lower RSI = higher confidence
        zone_strength = 60.0
        trade_score = confidence * 0.6 + 40 * 0.4  # weighted score

    # SELL signal: RSI > 70 and price near upper BB
    elif current_rsi > 70 and current_close >= current_bb_upper * 0.998:
        action = "SELL"
        entry = bid
        sl = entry + 25 * pip
        tp = entry - 50 * pip
        sl_pips = 25.0
        tp_pips = 50.0
        confidence = min(80, (current_rsi - 70) * 2 + 50)
        zone_strength = 60.0
        trade_score = confidence * 0.6 + 40 * 0.4

    else:
        plan.reason = f"no mean reversion setup (RSI {current_rsi:.1f}, BB touch no)"
        return plan

    risk_reward = tp_pips / sl_pips if sl_pips else 0.0
    account = client.account_info()
    equity = float(account.get("equity", 0.0) or 0.0)

    from mt5_mcp.strategy import _calc_lots
    lots, risk_amount, lot_warnings = _calc_lots(
        symbol, equity, risk_percent, sl_pips, pip_value_per_lot(info), info
    )
    plan.warnings.extend(lot_warnings)
    if lots is None:
        plan.reason = "cannot size a valid lot size"
        return plan

    plan.action = action
    plan.entry = round(entry, int(info.get("digits", 5)))
    plan.sl = round(sl, int(info.get("digits", 5)))
    plan.tp = round(tp, int(info.get("digits", 5)))
    plan.sl_pips = round(sl_pips, 1)
    plan.tp_pips = round(tp_pips, 1)
    plan.risk_reward = round(risk_reward, 2)
    plan.confirmation = f"mean_reversion (RSI {current_rsi:.1f}, ADX {current_adx:.1f})"
    plan.confirmation_confidence = confidence / 100.0
    plan.lots = lots
    plan.risk_amount = round(risk_amount, 2)
    plan.trade_score = round(trade_score, 1)
    plan.confidence = round(confidence, 1)
    plan.htf_trend = "ranging"
    plan.zone_strength = zone_strength
    plan.valid = trade_score >= 60 and confidence >= 50
    plan.reason = f"mean_reversion {action} RSI={current_rsi:.1f} ADX={current_adx:.1f}"

    return plan


def momentum_breakout_strategy(
    symbol: str,
    risk_percent: float | None = None,
    timeframe: str = "H1",
) -> TradePlan:
    """Momentum breakout: ADX > 25 + strong directional move + volume confirmation.

    BUY when ADX > 25 and price breaks above recent 20-bar high.
    SELL when ADX > 25 and price breaks below recent 20-bar low.
    """
    settings = get_settings()
    risk = settings.risk
    risk_percent = risk_percent if risk_percent is not None else risk.default_risk_percent

    plan = TradePlan(
        action="NONE", symbol=symbol, timeframe=timeframe,
        risk_percent=risk_percent,
    )

    try:
        client = get_client()
        client.ensure_connected()
        info = client.symbol_info(symbol)
        tick = client.tick(symbol)
    except Exception as exc:
        plan.errors.append(f"market data unavailable: {exc}")
        plan.reason = str(exc)
        return plan

    tf = timeframe.upper()
    if tf not in TIMEFRAME_MT5:
        plan.errors.append(f"unknown timeframe '{tf}'")
        plan.reason = "invalid timeframe"
        return plan

    try:
        frame = client.rates(symbol, tf, 200)
    except Exception as exc:
        plan.errors.append(f"no {tf} data: {exc}")
        plan.reason = str(exc)
        return plan

    if len(frame) < 50:
        plan.reason = "insufficient data"
        return plan

    bid = float(tick.get("bid", 0.0))
    ask = float(tick.get("ask", bid))

    adx_series = _adx(frame, 14)
    current_adx = float(adx_series.iloc[-1]) if len(adx_series) > 0 else 0.0

    # Only trade breakouts in trending markets (ADX > 25)
    if current_adx < 25:
        plan.reason = f"market ranging (ADX {current_adx:.1f} < 25); breakout inactive"
        return plan

    # Find recent 20-bar high/low
    recent_high = float(frame["high"].iloc[-20:].max())
    recent_low = float(frame["low"].iloc[-20:].min())
    current_close = float(frame["close"].iloc[-1])

    pip = pip_size(info)

    # BUY breakout: price above recent high
    if current_close > recent_high:
        action = "BUY"
        entry = ask
        sl = entry - 30 * pip
        tp = entry + 80 * pip
        sl_pips = 30.0
        tp_pips = 80.0
        confidence = min(85, current_adx * 0.8 + 30)
        zone_strength = 65.0
        trade_score = confidence * 0.5 + zone_strength * 0.3 + 50 * 0.2

    # SELL breakout: price below recent low
    elif current_close < recent_low:
        action = "SELL"
        entry = bid
        sl = entry + 30 * pip
        tp = entry - 80 * pip
        sl_pips = 30.0
        tp_pips = 80.0
        confidence = min(85, current_adx * 0.8 + 30)
        zone_strength = 65.0
        trade_score = confidence * 0.5 + zone_strength * 0.3 + 50 * 0.2

    else:
        plan.reason = f"no breakout (close {current_close:.5f} within {recent_low:.5f}-{recent_high:.5f})"
        return plan

    risk_reward = tp_pips / sl_pips if sl_pips else 0.0
    account = client.account_info()
    equity = float(account.get("equity", 0.0) or 0.0)

    from mt5_mcp.strategy import _calc_lots
    lots, risk_amount, lot_warnings = _calc_lots(
        symbol, equity, risk_percent, sl_pips, pip_value_per_lot(info), info
    )
    plan.warnings.extend(lot_warnings)
    if lots is None:
        plan.reason = "cannot size a valid lot size"
        return plan

    plan.action = action
    plan.entry = round(entry, int(info.get("digits", 5)))
    plan.sl = round(sl, int(info.get("digits", 5)))
    plan.tp = round(tp, int(info.get("digits", 5)))
    plan.sl_pips = round(sl_pips, 1)
    plan.tp_pips = round(tp_pips, 1)
    plan.risk_reward = round(risk_reward, 2)
    plan.confirmation = f"momentum_breakout (ADX {current_adx:.1f})"
    plan.confirmation_confidence = confidence / 100.0
    plan.lots = lots
    plan.risk_amount = round(risk_amount, 2)
    plan.trade_score = round(trade_score, 1)
    plan.confidence = round(confidence, 1)
    plan.htf_trend = "trending"
    plan.zone_strength = zone_strength
    plan.valid = trade_score >= 60 and confidence >= 50
    plan.reason = f"momentum_breakout {action} ADX={current_adx:.1f}"

    return plan


def evaluate_all_strategies(
    symbol: str,
    risk_percent: float | None = None,
) -> list[TradePlan]:
    """Run all strategies and return their plans. Caller picks the best."""
    plans: list[TradePlan] = []

    # 1. Trend + zone (existing strategy)
    try:
        trend_plan = evaluate_strategy(symbol, risk_percent=risk_percent, adaptive=True)
        trend_plan.confirmation = f"trend_zone: {trend_plan.confirmation}" if trend_plan.confirmation else "trend_zone"
        plans.append(trend_plan)
    except Exception as exc:
        logger.warning("trend_zone strategy failed for %s: %s", symbol, exc)

    # 2. Mean reversion
    try:
        mr_plan = mean_reversion_strategy(symbol, risk_percent=risk_percent)
        plans.append(mr_plan)
    except Exception as exc:
        logger.warning("mean_reversion strategy failed for %s: %s", symbol, exc)

    # 3. Momentum breakout
    try:
        mb_plan = momentum_breakout_strategy(symbol, risk_percent=risk_percent)
        plans.append(mb_plan)
    except Exception as exc:
        logger.warning("momentum_breakout strategy failed for %s: %s", symbol, exc)

    return plans


def pick_best_strategy(plans: list[TradePlan]) -> TradePlan | None:
    """Pick the best valid plan from a list. Returns None if no valid plans."""
    valid = [p for p in plans if p.valid and p.action in ("BUY", "SELL")]
    if not valid:
        return None

    # Check for conflicts (BUY vs SELL on same symbol)
    buys = [p for p in valid if p.action == "BUY"]
    sells = [p for p in valid if p.action == "SELL"]

    if buys and sells:
        # Conflict: pick the side with higher total score
        buy_score = max(p.trade_score for p in buys)
        sell_score = max(p.trade_score for p in sells)
        valid = buys if buy_score >= sell_score else sells
        logger.info(
            "strategy conflict on %s: BUY max=%.1f vs SELL max=%.1f → picked %s",
            valid[0].symbol,
            buy_score,
            sell_score,
            valid[0].action,
        )

    # Pick highest scoring plan
    best = max(valid, key=lambda p: p.trade_score)
    return best

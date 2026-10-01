"""Multi-strategy system — runs multiple strategies in parallel and picks the best.

Strategies:
1. trend_zone (existing) — trend + supply/demand zone + confirmation candle
2. mean_reversion — RSI extremes + Bollinger Band touches in ranging markets
3. momentum_breakout — ADX > 25 + volume spike + price breaking key levels
4. btc_trend_following — volatility-adjusted trend for crypto (EMA cross + RSI pullback)
5. ema_crossover — EMA20/50 cross + ADX trend filter for indices
6. bb_mr_fade — Bollinger+RSI mean-reversion fade (EURCHF research 2026-09-30, PF 1.25)

Each strategy returns a TradePlan. The system picks the highest-scoring valid plan.
Conflicting signals (BUY vs SELL on same symbol) are resolved by score.

NOTE: only the routed strategy per symbol is called in production — via
analysis_agent's router hook (READ-ONLY paper path); evaluate_all_strategies /
pick_best_strategy have no production call sites (B4, report 2026-09-30).

Strategy routing (based on 2026-09-27 backtest):
- EURJPY, GBPJPY, USDJPY, GBPUSD → mean_reversion (PF 1.52-2.65)
- USDCAD → momentum_breakout (PF 1.56)
- EURUSD, USDCHF, ETHUSD, XAUUSD → trend_zone (PF 1.34-3.25)
- BTCUSD → btc_trend_following (PF 1.17)
- SPX500, NDX100 → ema_crossover (PF 1.78-1.92)
- EURCHF → bb_mr_fade (PF 1.25, PAPER-TRADE only — off the consensus allowlist)
- AUDUSD → no profitable strategy (skip or use tightest filters)

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

STRATEGY_ROUTING: dict[str, str] = {
    "EURJPY": "mean_reversion",
    "GBPJPY": "mean_reversion",
    "USDJPY": "mean_reversion",
    "GBPUSD": "mean_reversion",
    "USDCAD": "momentum_breakout",
    "EURUSD": "trend_zone",
    "USDCHF": "trend_zone",
    "ETHUSD": "trend_zone",
    "XAUUSD": "trend_zone",
    "BTCUSD": "btc_trend_following",
    "SPX500": "ema_crossover",
    "NDX100": "ema_crossover",
    "EURCHF": "bb_mr_fade",
}

BACKTEST_PF: dict[tuple[str, str], float] = {
    ("EURJPY", "mean_reversion"): 2.65,
    ("EURJPY", "momentum_breakout"): 1.16,
    ("EURJPY", "trend_zone"): 0.21,
    ("GBPJPY", "mean_reversion"): 2.32,
    ("GBPJPY", "momentum_breakout"): 0.94,
    ("GBPJPY", "trend_zone"): 0.24,
    ("USDJPY", "mean_reversion"): 2.32,
    ("USDJPY", "momentum_breakout"): 0.75,
    ("USDJPY", "trend_zone"): 0.44,
    ("GBPUSD", "mean_reversion"): 1.52,
    ("GBPUSD", "momentum_breakout"): 0.89,
    ("GBPUSD", "trend_zone"): 0.58,
    ("USDCAD", "momentum_breakout"): 1.56,
    ("USDCAD", "mean_reversion"): 0.45,
    ("USDCAD", "trend_zone"): 0.46,
    ("EURUSD", "trend_zone"): 3.25,
    ("USDCHF", "trend_zone"): 1.34,
    ("USDCHF", "mean_reversion"): 0.0,
    ("AUDUSD", "mean_reversion"): 0.70,
    ("AUDUSD", "momentum_breakout"): 0.53,
    ("AUDUSD", "trend_zone"): 0.09,
    ("BTCUSD", "momentum_breakout"): 0.90,
    ("BTCUSD", "mean_reversion"): 0.56,
    ("BTCUSD", "trend_zone"): 0.53,
    ("BTCUSD", "btc_trend_following"): 1.17,
    ("ETHUSD", "trend_zone"): 1.69,
    ("ETHUSD", "mean_reversion"): 0.67,
    ("ETHUSD", "momentum_breakout"): 0.67,
    ("XAUUSD", "trend_zone"): 1.71,
    ("XAUUSD", "momentum_breakout"): 0.92,
    ("XAUUSD", "mean_reversion"): 0.66,
    ("SPX500", "ema_crossover"): 1.78,
    ("SPX500", "trend_zone"): 1.09,
    ("SPX500", "momentum_breakout"): 0.96,
    ("NDX100", "ema_crossover"): 1.92,
    ("NDX100", "momentum_breakout"): 1.32,
    ("NDX100", "trend_zone"): 0.41,
    ("EURCHF", "bb_mr_fade"): 1.25,
    ("EURCHF", "trend_zone"): 0.28,
}


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
    rsi_series = rsi(frame["close"], 14)
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


def bb_mr_fade_strategy(
    symbol: str,
    risk_percent: float | None = None,
    timeframe: str = "H1",
) -> TradePlan:
    """Bollinger-band + RSI mean-reversion fade (EURCHF research 2026-09-30).

    Research verdict (mt5_mcp/memory/research_lab/report_2026-09-30.md):
    H1, 3973 eval bars → 69 trades, WR 52.2%, PF 1.25, thirds 1.26/1.25/1.24,
    bootstrap P(PF>1)=0.80, walk-forward ~1.0 — THIN edge → paper-trade read-only
    via analysis_agent until the allowlist review (~2026-10-20).

    Spec: close < BB(20, 2.0σ) lower band AND RSI(14) < 30 (SELL symmetric);
    SL = 1.5 × ATR(14), TP = 2.0 × ATR(14); no ADX gate; engine timeout 80 bars.
    Structural RR is 1.33 — below the stack's 2.0/3.0 RR floors by design (the
    edge comes from a >50% win rate), so `avail` stays False; paper signals are
    tracked from the plan line + consensus stance.
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

    rsi_series = rsi(frame["close"], 14)
    bb_upper, bb_mid, bb_lower = _bollinger_bands(frame, 20, 2.0)
    atr_series = atr(frame, 14)

    if rsi_series is None or len(rsi_series) < 2:
        plan.reason = "RSI computation failed"
        return plan

    current_rsi = float(rsi_series.iloc[-1])
    current_close = float(frame["close"].iloc[-1])
    current_atr = float(atr_series.iloc[-1]) if len(atr_series) else 0.0
    current_bb_lower = float(bb_lower.iloc[-1]) if len(bb_lower) else 0.0
    current_bb_upper = float(bb_upper.iloc[-1]) if len(bb_upper) else 0.0

    if not current_atr or current_atr <= 0 or np.isnan(current_atr):
        plan.reason = "ATR unavailable"
        return plan

    pip = pip_size(info)
    digits = int(info.get("digits", 5))

    if current_rsi < 30.0 and current_close < current_bb_lower:
        action = "BUY"
        entry = ask
        sl = entry - 1.5 * current_atr
        tp = entry + 2.0 * current_atr
        confidence = float(min(95, max(55, 70 + (30.0 - current_rsi) * 1.5)))
    elif current_rsi > 70.0 and current_close > current_bb_upper:
        action = "SELL"
        entry = bid
        sl = entry + 1.5 * current_atr
        tp = entry - 2.0 * current_atr
        confidence = float(min(95, max(55, 70 + (current_rsi - 70.0) * 1.5)))
    else:
        plan.reason = (
            f"no bb_mr_fade setup (RSI {current_rsi:.1f}, "
            f"close vs BB {current_bb_lower:.5f}/{current_bb_upper:.5f})"
        )
        return plan

    sl_pips = abs(entry - sl) / pip if pip else 0.0
    tp_pips = abs(tp - entry) / pip if pip else 0.0
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
    plan.entry = round(entry, digits)
    plan.sl = round(sl, digits)
    plan.tp = round(tp, digits)
    plan.sl_pips = round(sl_pips, 1)
    plan.tp_pips = round(tp_pips, 1)
    plan.risk_reward = round(risk_reward, 2)
    plan.confirmation = (
        f"bb_mr_fade {action} RSI {current_rsi:.1f} close beyond BB(20,2.0) "
        f"SL 1.5xATR TP 2.0xATR"
    )
    plan.confirmation_confidence = confidence / 100.0
    plan.lots = lots
    plan.risk_amount = round(risk_amount, 2)
    plan.trade_score = round(confidence, 1)
    plan.confidence = round(confidence, 1)
    plan.htf_trend = "mean-reverting"
    plan.zone_strength = 60.0
    plan.valid = plan.trade_score >= 60 and confidence >= 50
    plan.reason = (
        f"bb_mr_fade {action} RSI={current_rsi:.1f} ATR={current_atr:.5f} "
        f"RR={risk_reward:.2f}"
    )
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


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def btc_trend_following_strategy(
    symbol: str,
    risk_percent: float | None = None,
    timeframe: str = "H4",
) -> TradePlan:
    """Volatility-adjusted trend following for crypto (BTCUSD).

    Designed for BTC's characteristics: ATR ~$462, 3.3% avg pullbacks,
    8-day trends, $969 daily moves. Uses ATR-based stops to survive
    noise while capturing multi-day trends.

    Entry: EMA 50/200 crossover confirmed + RSI pullback completion
    (RSI crosses above 40 for BUY, below 60 for SELL after a trend is established).
    SL: 2.5x ATR (survives typical pullbacks).
    TP: 4x ATR (1.6:1 RR).
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
        frame = client.rates(symbol, tf, 250)
    except Exception as exc:
        plan.errors.append(f"no {tf} data: {exc}")
        plan.reason = str(exc)
        return plan

    if len(frame) < 210:
        plan.reason = "insufficient data for EMA 200"
        return plan

    bid = float(tick.get("bid", 0.0))
    ask = float(tick.get("ask", bid))
    close = frame["close"]

    ema_fast = _ema(close, 50)
    ema_slow = _ema(close, 200)
    rsi_series = rsi(close, 14)
    atr_series = atr(frame, 14)

    if rsi_series is None or len(rsi_series) < 3:
        plan.reason = "indicator computation failed"
        return plan

    current_rsi = float(rsi_series.iloc[-1])
    prev_rsi = float(rsi_series.iloc[-2])
    current_atr = float(atr_series.iloc[-1]) if len(atr_series) > 0 else 0.0
    current_ema_fast = float(ema_fast.iloc[-1])
    current_ema_slow = float(ema_slow.iloc[-1])
    current_close = float(close.iloc[-1])

    if current_atr <= 0:
        plan.reason = "ATR is zero"
        return plan

    pip = pip_size(info)
    atr_pips = current_atr / pip

    # Trend direction from EMA alignment
    bullish_trend = current_ema_fast > current_ema_slow
    bearish_trend = current_ema_fast < current_ema_slow

    # RSI pullback completion: cross above 40 (BUY) or below 60 (SELL)
    rsi_crossed_up = prev_rsi < 40 and current_rsi >= 40
    rsi_crossed_down = prev_rsi > 60 and current_rsi <= 60

    sl_distance = 2.5 * current_atr
    tp_distance = 4.0 * current_atr
    sl_pips = sl_distance / pip
    tp_pips = tp_distance / pip

    if bullish_trend and rsi_crossed_up:
        action = "BUY"
        entry = ask
        sl = entry - sl_distance
        tp = entry + tp_distance
        confidence = min(80, 50 + (current_rsi - 40) * 1.5)
        zone_strength = 60.0
    elif bearish_trend and rsi_crossed_down:
        action = "SELL"
        entry = bid
        sl = entry + sl_distance
        tp = entry - tp_distance
        confidence = min(80, 50 + (60 - current_rsi) * 1.5)
        zone_strength = 60.0
    else:
        trend_dir = "bullish" if bullish_trend else "bearish" if bearish_trend else "neutral"
        plan.reason = (
            f"no btc_trend setup ({trend_dir} trend, RSI {current_rsi:.1f}, "
            f"ATR {current_atr:.0f})"
        )
        return plan

    trade_score = confidence * 0.6 + zone_strength * 0.4
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

    digits = int(info.get("digits", 5))
    plan.action = action
    plan.entry = round(entry, digits)
    plan.sl = round(sl, digits)
    plan.tp = round(tp, digits)
    plan.sl_pips = round(sl_pips, 1)
    plan.tp_pips = round(tp_pips, 1)
    plan.risk_reward = round(risk_reward, 2)
    plan.confirmation = f"btc_trend_following (EMA cross, RSI {current_rsi:.1f}, ATR {current_atr:.0f})"
    plan.confirmation_confidence = confidence / 100.0
    plan.lots = lots
    plan.risk_amount = round(risk_amount, 2)
    plan.trade_score = round(trade_score, 1)
    plan.confidence = round(confidence, 1)
    plan.htf_trend = "bullish" if bullish_trend else "bearish"
    plan.zone_strength = zone_strength
    plan.valid = trade_score >= 60 and confidence >= 50
    plan.reason = f"btc_trend_following {action} RSI={current_rsi:.1f} ATR={current_atr:.0f}"

    return plan


INDEX_SYMBOLS = {"SPX500", "NDX100", "DJI30", "JP225"}


def ema_crossover_strategy(
    symbol: str,
    risk_percent: float | None = None,
    timeframe: str = "H1",
) -> TradePlan:
    """EMA crossover with ADX trend filter — optimized for stock indices.

    BUY when EMA20 crosses above EMA50 and ADX > 20 (trending).
    SELL when EMA20 crosses below EMA50 and ADX > 20.
    SL: 2x ATR, TP: 4x ATR (2:1 RR).
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
        frame = client.rates(symbol, tf, 250)
    except Exception as exc:
        plan.errors.append(f"no {tf} data: {exc}")
        plan.reason = str(exc)
        return plan

    if len(frame) < 60:
        plan.reason = "insufficient data"
        return plan

    bid = float(tick.get("bid", 0.0))
    ask = float(tick.get("ask", bid))
    close = frame["close"]

    ema_fast = _ema(close, 20)
    ema_slow = _ema(close, 50)
    adx_series = _adx(frame, 14)
    atr_series = atr(frame, 14)

    current_adx = float(adx_series.iloc[-1]) if len(adx_series) > 0 else 0.0
    current_atr = float(atr_series.iloc[-1]) if len(atr_series) > 0 else 0.0

    if current_adx < 20:
        plan.reason = f"market not trending (ADX {current_adx:.1f} < 20); ema_crossover inactive"
        return plan

    if current_atr <= 0:
        plan.reason = "ATR is zero"
        return plan

    # Detect crossover: compare current and previous EMA positions
    ema_f_now = float(ema_fast.iloc[-1])
    ema_s_now = float(ema_slow.iloc[-1])
    ema_f_prev = float(ema_fast.iloc[-2])
    ema_s_prev = float(ema_slow.iloc[-2])

    pip = pip_size(info)
    sl_distance = 2.0 * current_atr
    tp_distance = 4.0 * current_atr
    sl_pips = sl_distance / pip
    tp_pips = tp_distance / pip

    crossed_up = ema_f_prev <= ema_s_prev and ema_f_now > ema_s_now
    crossed_down = ema_f_prev >= ema_s_prev and ema_f_now < ema_s_now

    if crossed_up:
        action = "BUY"
        entry = ask
        sl = entry - sl_distance
        tp = entry + tp_distance
        confidence = min(80, 50 + current_adx * 0.5)
        zone_strength = 65.0
    elif crossed_down:
        action = "SELL"
        entry = bid
        sl = entry + sl_distance
        tp = entry - tp_distance
        confidence = min(80, 50 + current_adx * 0.5)
        zone_strength = 65.0
    else:
        plan.reason = (
            f"no ema crossover (EMA20 {ema_f_now:.2f} vs EMA50 {ema_s_now:.2f}, "
            f"ADX {current_adx:.1f})"
        )
        return plan

    trade_score = confidence * 0.5 + zone_strength * 0.3 + 50 * 0.2
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

    digits = int(info.get("digits", 5))
    plan.action = action
    plan.entry = round(entry, digits)
    plan.sl = round(sl, digits)
    plan.tp = round(tp, digits)
    plan.sl_pips = round(sl_pips, 1)
    plan.tp_pips = round(tp_pips, 1)
    plan.risk_reward = round(risk_reward, 2)
    plan.confirmation = f"ema_crossover (EMA20/50, ADX {current_adx:.1f}, ATR {current_atr:.2f})"
    plan.confirmation_confidence = confidence / 100.0
    plan.lots = lots
    plan.risk_amount = round(risk_amount, 2)
    plan.trade_score = round(trade_score, 1)
    plan.confidence = round(confidence, 1)
    plan.htf_trend = "bullish" if crossed_up else "bearish"
    plan.zone_strength = zone_strength
    plan.valid = trade_score >= 60 and confidence >= 50
    plan.reason = f"ema_crossover {action} ADX={current_adx:.1f} ATR={current_atr:.2f}"

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

    # 4. BTC trend following (only for BTCUSD)
    if symbol == "BTCUSD":
        try:
            btc_plan = btc_trend_following_strategy(symbol, risk_percent=risk_percent)
            plans.append(btc_plan)
        except Exception as exc:
            logger.warning("btc_trend_following strategy failed for %s: %s", symbol, exc)

    # 5. EMA crossover (only for indices)
    if symbol in INDEX_SYMBOLS:
        try:
            ema_plan = ema_crossover_strategy(symbol, risk_percent=risk_percent)
            plans.append(ema_plan)
        except Exception as exc:
            logger.warning("ema_crossover strategy failed for %s: %s", symbol, exc)

    return plans


def _strategy_name_from_plan(plan: TradePlan) -> str:
    """Extract strategy name from a plan's confirmation field."""
    conf = (plan.confirmation or "").lower()
    if "bb_mr_fade" in conf:
        return "bb_mr_fade"
    if "mean_reversion" in conf:
        return "mean_reversion"
    if "momentum_breakout" in conf or "momentum_brk" in conf:
        return "momentum_breakout"
    if "btc_trend_following" in conf:
        return "btc_trend_following"
    if "ema_crossover" in conf:
        return "ema_crossover"
    if "trend_zone" in conf:
        return "trend_zone"
    return "unknown"


def pick_best_strategy(plans: list[TradePlan]) -> TradePlan | None:
    """Pick the best valid plan from a list. Returns None if no valid plans.

    Uses backtest profit factor as a score multiplier so strategies that
    historically performed better on a given symbol get preferred.
    Rejects strategies with PF < 1.0 (unprofitable in backtest).
    """
    valid = [p for p in plans if p.valid and p.action in ("BUY", "SELL")]
    if not valid:
        return None

    symbol = valid[0].symbol

    # Filter out strategies with PF < 1.0 (unprofitable in backtest)
    def _get_pf(plan: TradePlan) -> float:
        strat = _strategy_name_from_plan(plan)
        return BACKTEST_PF.get((symbol, strat), 0.0)

    profitable = [p for p in valid if _get_pf(p) >= 1.0]
    if not profitable:
        logger.info("no profitable strategy for %s (all PF < 1.0), skipping", symbol)
        return None

    valid = profitable

    def _adjusted_score(plan: TradePlan) -> float:
        strat = _strategy_name_from_plan(plan)
        pf = BACKTEST_PF.get((symbol, strat), 1.0)
        pf_boost = max(0.5, min(pf, 3.0))
        return plan.trade_score * pf_boost

    buys = [p for p in valid if p.action == "BUY"]
    sells = [p for p in valid if p.action == "SELL"]

    if buys and sells:
        buy_score = max(_adjusted_score(p) for p in buys)
        sell_score = max(_adjusted_score(p) for p in sells)
        valid = buys if buy_score >= sell_score else sells
        logger.info(
            "strategy conflict on %s: BUY max=%.1f vs SELL max=%.1f → picked %s",
            valid[0].symbol,
            buy_score,
            sell_score,
            valid[0].action,
        )

    best = max(valid, key=_adjusted_score)

    strat = _strategy_name_from_plan(best)
    pf = BACKTEST_PF.get((symbol, strat), 0.0)
    preferred = STRATEGY_ROUTING.get(symbol, "")
    if preferred and strat != preferred:
        logger.info(
            "picked %s for %s (score %.1f, PF %.2f) over preferred %s",
            strat, symbol, best.trade_score, pf, preferred,
        )
    else:
        logger.info(
            "picked %s for %s (score %.1f, PF %.2f)",
            strat, symbol, best.trade_score, pf,
        )

    return best

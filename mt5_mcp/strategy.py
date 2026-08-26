"""The trader's strategy engine.

Rules implemented:

1. Determine overall trend with EMA 50/200 + market structure (HH/HL/LH/LL)
2. Find strong Supply/Demand zones on H1 and H4
3. Require price to be revisiting a zone (proximity check)
4. Require confirmation: engulfing, pin bar, hammer, BOS or CHOCH
5. Risk only 0.5% per trade (configurable)
6. Target 80-100 pips, SL 20-30 pips or beyond the zone
7. Minimum Risk:Reward 1:3

Produces a scored TradePlan that the trade execution tools can fill.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from mt5_mcp.client import get_client
from mt5_mcp.common import (
    TIMEFRAME_MT5,
    PatternResult,
    TradePlan,
    TrendResult,
    pip_size,
    pip_value_per_lot,
    round_to_step,
)
from mt5_mcp.config import get_settings
from mt5_mcp.indicators import atr, ema, swing_highs, swing_lows
from mt5_mcp.price_action import detect_patterns
from mt5_mcp.supply_demand import Zone, detect_zones

try:
    from ml_ranker import load_model, rank_signal

    _ML_AVAILABLE = True
except Exception:  # noqa: BLE001
    _ML_AVAILABLE = False

logger = logging.getLogger("mt5_mcp.strategy")

DEMAND = "demand"
SUPPLY = "supply"

BULLISH_CONFIRMATIONS = {
    "bullish_engulfing",
    "pin_bar",
    "hammer",
    "morning_star",
    "outside_bar",
    "BOS",
    "CHOCH",
    "liquidity_sweep",
    "fake_breakout",
}
BEARISH_CONFIRMATIONS = {
    "bearish_engulfing",
    "pin_bar",
    "shooting_star",
    "evening_star",
    "outside_bar",
    "BOS",
    "CHOCH",
    "liquidity_sweep",
    "fake_breakout",
}


def market_structure(df: pd.DataFrame, left: int = 5, right: int = 5) -> dict[str, int]:
    highs = swing_highs(df, left, right)
    lows = swing_lows(df, left, right)
    hh = lh = hl = ll = 0
    for k in range(1, len(highs)):
        if df.iloc[highs[k]].high > df.iloc[highs[k - 1]].high:
            hh += 1
        else:
            lh += 1
    for k in range(1, len(lows)):
        if df.iloc[lows[k]].low > df.iloc[lows[k - 1]].low:
            hl += 1
        else:
            ll += 1
    return {"hh": hh, "hl": hl, "lh": lh, "ll": ll, "highs": highs, "lows": lows}


def analyze_trend(
    df: pd.DataFrame, fast_ema: int = 50, slow_ema: int = 200
) -> TrendResult:
    if df is None or len(df) < max(fast_ema, slow_ema) + 10:
        return TrendResult(direction="sideways", confidence=0.0)
    e_fast = float(ema(df["close"], fast_ema).iloc[-1])
    e_slow = float(ema(df["close"], slow_ema).iloc[-1])
    atr_val = float(atr(df).iloc[-1]) or 1e-9
    structure = market_structure(df)
    hh, hl, lh, ll = structure["hh"], structure["hl"], structure["lh"], structure["ll"]
    up_strength = hh + hl
    down_strength = lh + ll
    separation = (e_fast - e_slow) / atr_val

    if e_fast > e_slow and up_strength >= down_strength and up_strength > 0:
        direction = "bullish"
    elif e_fast < e_slow and down_strength >= up_strength and down_strength > 0:
        direction = "bearish"
    else:
        direction = "sideways"

    structure_bonus = 0.0
    if direction == "bullish":
        structure_bonus = min(0.2, 0.1 + 0.05 * min(up_strength, 4))
    elif direction == "bearish":
        structure_bonus = min(0.2, 0.1 + 0.05 * min(down_strength, 4))
    confidence = max(
        0.0, min(1.0, 0.5 + min(0.3, abs(separation) * 0.06) + structure_bonus)
    )

    highs = structure["highs"]
    lows = structure["lows"]
    return TrendResult(
        direction=direction,
        confidence=round(confidence, 3),
        ema_fast=round(e_fast, 6),
        ema_slow=round(e_slow, 6),
        hh=hh,
        hl=hl,
        lh=lh,
        ll=ll,
        last_swing_high=round(float(df.iloc[highs[-1]].high), 6) if highs else None,
        last_swing_low=round(float(df.iloc[lows[-1]].low), 6) if lows else None,
        detail={"separation_atr": round(separation, 3), "atr": round(atr_val, 6)},
    )


def _best_confirmation(
    patterns: list[PatternResult], direction: str
) -> PatternResult | None:
    wanted = BULLISH_CONFIRMATIONS if direction == "bullish" else BEARISH_CONFIRMATIONS
    candidates = [
        p for p in patterns if p.direction == direction and p.pattern in wanted
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.confidence)


def _select_zone(
    zones: list[Zone],
    zone_type: str,
    price: float,
    atr_val: float,
    min_zone_score: float,
) -> tuple[Zone | None, float]:
    """Pick the strongest suitable zone price is currently revisiting.

    Returns (zone, proximity_bonus)."""
    candidates = [
        z
        for z in zones
        if z.type == zone_type and not z.broken and z.strength >= min_zone_score
    ]
    if not candidates:
        return None, 0.0
    best_zone: Zone | None = None
    best_score = -1.0
    for zone in candidates:
        if zone.lower - 0.5 * atr_val <= price <= zone.upper + 1.5 * atr_val:
            if price <= zone.upper:
                proximity = 1.0
            elif price <= zone.upper + 0.5 * atr_val:
                proximity = 0.8
            else:
                proximity = 0.6
            score = zone.strength * proximity
            if score > best_score:
                best_score = score
                best_zone = zone
    return best_zone, (best_score / best_zone.strength if best_zone else 0.0)


def _choose_entry_math(
    action: str,
    entry: float,
    zone: Zone,
    atr_val: float,
    pip: float,
    sl_min: float,
    tp_min: float,
    tp_max: float,
    min_rr: float,
) -> tuple[
    float | None, float | None, float | None, float | None, float | None, list[str]
]:
    warnings: list[str] = []
    if action == "BUY":
        zone_dist_pips = max(0.0, (entry - zone.lower) / pip) + 0.2 * atr_val / pip
        sl_pips = max(sl_min, zone_dist_pips)
        tp_pips = min(tp_max, max(tp_min, (tp_min + tp_max) / 2.0))
        rr = tp_pips / sl_pips if sl_pips else 0.0
        if rr < min_rr:
            tp_pips = sl_pips * min_rr
            warnings.append(f"TP extended to {tp_pips:.1f} pips to satisfy minimum RR")
        return (
            entry,
            entry - sl_pips * pip,
            entry + tp_pips * pip,
            sl_pips,
            tp_pips,
            warnings,
        )
    zone_dist_pips = max(0.0, (zone.upper - entry) / pip) + 0.2 * atr_val / pip
    sl_pips = max(sl_min, zone_dist_pips)
    tp_pips = min(tp_max, max(tp_min, (tp_min + tp_max) / 2.0))
    rr = tp_pips / sl_pips if sl_pips else 0.0
    if rr < min_rr:
        tp_pips = sl_pips * min_rr
        warnings.append(f"TP extended to {tp_pips:.1f} pips to satisfy minimum RR")
    return (
        entry,
        entry + sl_pips * pip,
        entry - tp_pips * pip,
        sl_pips,
        tp_pips,
        warnings,
    )


def _calc_lots(
    symbol: str,
    equity: float,
    risk_percent: float,
    sl_pips: float,
    pip_value: float,
    info: dict,
) -> tuple[float | None, float, list[str]]:
    warnings: list[str] = []
    if sl_pips <= 0 or pip_value <= 0:
        return None, 0.0, ["cannot size lot (SL pips or pip value unavailable)"]
    risk_amount = equity * risk_percent / 100.0
    lots = risk_amount / (sl_pips * pip_value)
    volume_min = float(info.get("volume_min", 0.01) or 0.01)
    volume_step = float(info.get("volume_step", 0.01) or 0.01)
    volume_max = float(info.get("volume_max", 100.0) or 100.0)
    lots = round_to_step(lots, volume_step, floor=volume_min, ceil=volume_max)
    if lots < volume_min:
        warnings.append("lot size below broker minimum; no trade")
        return None, risk_amount, warnings
    return lots, risk_amount, warnings


def evaluate_strategy(
    symbol: str,
    risk_percent: float | None = None,
    tp_min: float | None = None,
    tp_max: float | None = None,
    sl_min: float | None = None,
    sl_max: float | None = None,
    min_rr: float | None = None,
    min_zone_score: float | None = None,
    min_trade_score: float | None = None,
    min_confidence_percent: float | None = None,
    trend_timeframe: str | None = None,
    zone_timeframes: list[str] | None = None,
    confirmation_timeframe: str | None = None,
    lookback: int | None = None,
    closed_confirmation: bool = False,
) -> TradePlan:
    settings = get_settings()
    risk = settings.risk
    strategy_cfg = settings.strategy

    risk_percent = (
        risk_percent if risk_percent is not None else risk.default_risk_percent
    )
    tp_min = tp_min if tp_min is not None else risk.target_tp_min_pips
    tp_max = tp_max if tp_max is not None else risk.target_tp_max_pips
    sl_min = sl_min if sl_min is not None else risk.sl_min_pips
    sl_max = sl_max if sl_max is not None else risk.sl_max_pips
    min_rr = min_rr if min_rr is not None else risk.min_risk_reward
    min_zone_score = (
        min_zone_score if min_zone_score is not None else strategy_cfg.min_zone_score
    )
    min_trade_score = (
        min_trade_score if min_trade_score is not None else strategy_cfg.min_trade_score
    )
    min_confidence = (
        min_confidence_percent
        if min_confidence_percent is not None
        else strategy_cfg.min_confidence_percent
    )
    trend_tf = (trend_timeframe or strategy_cfg.trend_timeframe).upper()
    confirm_tf = (confirmation_timeframe or strategy_cfg.confirmation_timeframe).upper()
    zone_tfs = [tf.upper() for tf in (zone_timeframes or strategy_cfg.zone_timeframes)]
    candles = lookback or strategy_cfg.lookback_candles

    plan = TradePlan(
        action="NONE", symbol=symbol, timeframe=confirm_tf, risk_percent=risk_percent
    )

    try:
        client = get_client()
        client.ensure_connected()
        info = client.symbol_info(symbol)
        tick = client.tick(symbol)
    except Exception as exc:
        plan.errors.append(f"market data unavailable: {exc}")
        plan.reason = "; ".join(plan.errors)
        return plan

    for tf in (trend_tf, confirm_tf, *zone_tfs):
        if tf not in TIMEFRAME_MT5:
            plan.errors.append(f"unknown timeframe '{tf}'")
    if plan.errors:
        plan.reason = "; ".join(plan.errors)
        return plan

    pip = pip_size(info)
    bid = float(tick.get("bid", 0.0))
    ask = float(tick.get("ask", bid))

    try:
        trend_frame = client.rates(symbol, trend_tf, max(candles, 300))
    except Exception as exc:
        plan.errors.append(f"no {trend_tf} data: {exc}")
        plan.reason = "; ".join(plan.errors)
        return plan

    trend = analyze_trend(
        trend_frame, strategy_cfg.trend_fast_ema, strategy_cfg.trend_slow_ema
    )
    plan.htf_trend = trend.direction
    plan.confidence = round(trend.confidence * 100.0, 1)

    if trend.direction == "bullish":
        action = "BUY"
        zone_type = DEMAND
    elif trend.direction == "bearish":
        action = "SELL"
        zone_type = SUPPLY
    else:
        plan.reason = "HTF trend is sideways; strategy waits for a directional trend"
        plan.warnings.append("EMA50/EMA200 not aligned and structure mixed")
        return plan

    zone_frames: dict[str, tuple[pd.DataFrame, float]] = {}
    for tf in zone_tfs:
        try:
            frame = client.rates(symbol, tf, candles)
            zone_frames[tf] = (frame, float(atr(frame).iloc[-1]) or 1e-9)
        except Exception as exc:
            plan.warnings.append(f"no {tf} data: {exc}")

    best_zone: Zone | None = None
    best_proximity = 0.0
    for frame, atr_val in zone_frames.values():
        for zone in detect_zones(frame, lookback=candles, min_strength=min_zone_score):
            if zone.type != zone_type or zone.broken:
                continue
            chosen, proximity = _select_zone(
                [zone],
                zone_type,
                ask if action == "BUY" else bid,
                atr_val,
                min_zone_score,
            )
            if chosen is not None and proximity >= best_proximity:
                best_zone, best_proximity = chosen, proximity

    if best_zone is None:
        plan.reason = f"No strong {'demand' if action == 'BUY' else 'supply'} zone being revisited"
        plan.warnings.append(f"min zone score {min_zone_score:.0f}")
        return plan

    zone_atr = (
        max(atr(frame).iloc[-1] for frame, _ in zone_frames.values())
        if zone_frames
        else 1e-9
    )
    plan.zone_strength = best_zone.strength
    plan.zone_type = best_zone.type
    plan.zone_upper = best_zone.upper
    plan.zone_lower = best_zone.lower

    try:
        confirm_frame = client.rates(symbol, confirm_tf, 300)
    except Exception as exc:
        plan.errors.append(f"no {confirm_tf} data: {exc}")
        plan.reason = "; ".join(plan.errors)
        return plan

    # Automation should evaluate only completed candles. Keep the default
    # behaviour unchanged for existing dashboard/MCP callers.
    if closed_confirmation and len(confirm_frame) > 1:
        confirm_frame = confirm_frame.iloc[:-1].reset_index(drop=True)

    patterns = detect_patterns(confirm_frame)
    confirmation = _best_confirmation(
        patterns, "bullish" if action == "BUY" else "bearish"
    )
    if confirmation is None:
        plan.reason = f"no {'bullish' if action == 'BUY' else 'bearish'} confirmation candle found"
        plan.warnings.append("look for engulfing, pin bar, hammer, BOS or CHOCH")
        return plan

    entry = ask if action == "BUY" else bid
    entry, sl, tp, sl_pips, tp_pips, math_warnings = _choose_entry_math(
        action, entry, best_zone, zone_atr, pip, sl_min, tp_min, tp_max, min_rr
    )
    if entry is None:
        plan.reason = "; ".join(math_warnings)
        return plan
    plan.warnings.extend(math_warnings)

    risk_reward = tp_pips / sl_pips if sl_pips else 0.0
    account = client.account_info()
    equity = float(account.get("equity", 0.0) or 0.0)
    lots, risk_amount, lot_warnings = _calc_lots(
        symbol, equity, risk_percent, sl_pips, pip_value_per_lot(info), info
    )
    plan.warnings.extend(lot_warnings)
    if lots is None:
        plan.reason = "cannot size a valid lot size at current risk %"
        return plan
    try:
        margin_needed = client.order_calc_margin(1, symbol, lots, entry)
        free_margin = float(account.get("margin_free", 0.0) or 0.0)
        if margin_needed > free_margin:
            plan.reason = "insufficient free margin"
            plan.errors.append(f"need {margin_needed:.2f}, free {free_margin:.2f}")
            return plan
    except Exception:
        plan.warnings.append(
            "margin verification skipped (order_calc_margin unavailable)"
        )

    rr_score = min(1.0, risk_reward / min_rr) if min_rr else 1.0
    trade_score = (
        trend.confidence * 0.25
        + (best_zone.strength / 100.0) * 0.35
        + confirmation.confidence * 0.25
        + rr_score * 0.15
    ) * 100.0
    confidence_pct = (
        trend.confidence * 0.3
        + (best_zone.strength / 100.0) * 0.35
        + confirmation.confidence * 0.35
    ) * 100.0

    plan.action = action
    plan.entry = round(entry, int(info.get("digits", 5)))
    plan.sl = round(sl, int(info.get("digits", 5)))
    plan.tp = round(tp, int(info.get("digits", 5)))
    plan.sl_pips = round(sl_pips, 1)
    plan.tp_pips = round(tp_pips, 1)
    plan.risk_reward = round(risk_reward, 2)
    plan.confirmation = f"{confirmation.pattern} ({confirmation.direction}, conf {confirmation.confidence:.2f})"
    plan.confirmation_confidence = round(confirmation.confidence, 3)
    plan.lots = lots
    plan.risk_amount = round(risk_amount, 2)
    plan.trade_score = round(trade_score, 1)
    plan.confidence = round(confidence_pct, 1)

    reasons = [
        f"HTF trend {trend.direction} (EMA{strategy_cfg.trend_fast_ema}/{strategy_cfg.trend_slow_ema}, conf {trend.confidence:.0%})",
        f"{best_zone.type} zone {best_zone.lower:.5f}-{best_zone.upper:.5f} strength {best_zone.strength:.0f}/100 touches {best_zone.touches}",
        f"confirmation: {confirmation.pattern} ({confirmation.direction}, {confirmation.confidence:.0%})",
        f"RR {risk_reward:.2f} (min {min_rr:.1f}), risk {risk_percent:.1f}% = {risk_amount:.2f} {account.get('currency', '')}",
    ]
    plan.reason = "; ".join(reasons)

    if _ML_AVAILABLE:
        try:
            model = load_model()
            plan.ml_score = round(
                rank_signal(
                    trend.confidence,
                    best_zone.strength,
                    confirmation.confidence,
                    risk_reward,
                    sl_pips,
                    float(datetime.now(timezone.utc).hour),
                ),
                3,
            )
            plan.ml_note = (
                f"ML ranker AUC {model.get('cv_auc', 0.0):.2f} on "
                f"{model.get('n_trades', 0)} backtest trades"
            )
        except Exception as exc:  # noqa: BLE001
            plan.ml_note = f"ML ranker unavailable: {exc}"
    else:
        plan.ml_note = "ML ranker not installed"

    if trade_score < min_trade_score or confidence_pct < min_confidence:
        plan.valid = False
        plan.warnings.append(
            f"below thresholds (score {trade_score:.0f} < {min_trade_score:.0f}, confidence {confidence_pct:.0f}% < {min_confidence:.0f}%)"
        )
    else:
        plan.valid = True
        logger.info(
            "Strategy plan %s %s score=%.1f confidence=%.1f%% zone=%.1f confirm=%s",
            action,
            symbol,
            trade_score,
            confidence_pct,
            best_zone.strength,
            confirmation.pattern,
        )
    return plan


def trend_analysis(
    symbol: str, timeframe: str = "H4", candles: int = 400
) -> TrendResult:
    client = get_client()
    client.ensure_connected()
    tf = timeframe.upper()
    frame = client.rates(symbol, tf, candles)
    return analyze_trend(frame)

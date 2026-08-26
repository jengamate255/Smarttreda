"""Market Intelligence Agent.

Monitors symbols and produces an explainable Market Health Score (0-100):

* trend clarity   - EMA50/200 separation + market structure (from strategy)
* volatility      - current ATR percentile vs its own history
* regime          - trending / ranging / expanding / contracting
* spread health   - live spread vs the configured limit
* session quality - journal win rate for the active session
* news risk       - high-impact events inside the blackout window
* liquidity       - market open, trading mode, fresh quotes

The score is a weighted blend of independently explainable components, so the
agent reports *why* a symbol is tradeable, cautious or to be avoided - it never
returns a black-box number.

Also detects abnormal volatility (ATR percentile/z-score spikes) and names the
active trading session with its overlap set.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from mt5_mcp.client import get_client
from mt5_mcp.config import get_settings
from mt5_mcp.indicators import atr, bollinger, ema
from mt5_mcp.strategy import analyze_trend

logger = logging.getLogger("mt5_mcp.market_intel")

SESSIONS: list[tuple[str, int, int]] = [
    ("sydney", 21, 6),
    ("tokyo", 0, 9),
    ("london", 7, 16),
    ("new_york", 12, 21),
]

SESSION_ACTIVITY = {
    "new_york": 0,
    "london": 1,
    "tokyo": 2,
    "sydney": 3,
}

DEFAULT_SCAN_GROUPS = ("FX", "METAL", "US500", "NAS", "JP225", "US30", "SPX")

REGIME_SCORE = {
    "trending_bullish": 100.0,
    "trending_bearish": 100.0,
    "ranging": 70.0,
    "transition": 55.0,
    "quiet_contraction": 55.0,
    "volatile_breakout": 45.0,
    "unknown": 40.0,
}

HEALTH_WEIGHTS: dict[str, float] = {
    "trend": 0.20,
    "volatility": 0.15,
    "regime": 0.20,
    "spread": 0.10,
    "session": 0.10,
    "news": 0.15,
    "liquidity": 0.10,
}


def atr_percentile(df: pd.DataFrame, period: int = 14, lookback: int = 250) -> dict:
    """Where the current ATR sits in its own recent history (0-100) plus z-score."""
    series = atr(df, period)
    if len(series) < 30:
        current = float(series.iloc[-1]) if len(series) else 0.0
        return {
            "atr_percentile": 50.0,
            "atr_z": 0.0,
            "atr": round(current, 8),
            "abnormal_volatility": False,
        }
    window = series.iloc[-lookback:]
    current = float(series.iloc[-1])
    std = float(window.std() or 1e-12)
    pct = float((window < current).mean() * 100.0)
    z = (current - float(window.mean())) / std
    return {
        "atr_percentile": round(pct, 1),
        "atr_z": round(float(z), 2),
        "atr": round(current, 8),
        "abnormal_volatility": bool(pct >= 85.0 or z >= 2.5),
    }


def _bollinger_width_ratio(df: pd.DataFrame, period: int = 20) -> float:
    bands = bollinger(df["close"], period)
    width = (bands["upper"] - bands["lower"]) / bands["middle"].replace(0.0, np.nan)
    tail = width.tail(min(100, len(width)))
    if len(tail) < 20:
        return 1.0
    current = float(width.iloc[-1])
    average = float(tail.mean())
    if not average or average <= 0:
        return 1.0
    return round(current / average, 2)


def market_regime(
    df: pd.DataFrame, fast: int = 50, slow: int = 200
) -> dict[str, Any]:
    """Classify the current market regime from one timeframe of candles."""
    if df is None or len(df) < max(fast, slow):
        return {
            "regime": "unknown",
            "trend": "sideways",
            "evidence": "not enough data",
            "ema_separation_atr": 0.0,
            "bollinger_width_ratio": 1.0,
            "atr_percentile": 50.0,
            "atr_z": 0.0,
            "atr": 0.0,
            "abnormal_volatility": False,
        }
    e_fast = float(ema(df["close"], fast).iloc[-1])
    e_slow = float(ema(df["close"], slow).iloc[-1])
    atr_val = float(atr(df).iloc[-1]) or 1e-9
    separation = (e_fast - e_slow) / atr_val
    vol = atr_percentile(df)
    width_ratio = _bollinger_width_ratio(df)

    if abs(separation) >= 1.0:
        regime = "trending_bullish" if separation > 0 else "trending_bearish"
        trend = "bullish" if separation > 0 else "bearish"
        evidence = (
            f"EMA{fast}/EMA{slow} separation {separation:.2f} ATRs, "
            f"ATR at {vol['atr_percentile']:.0f}th percentile"
        )
    elif vol["atr_percentile"] >= 85.0 and width_ratio >= 1.3:
        regime = "volatile_breakout"
        trend = "sideways"
        evidence = (
            f"ATR at {vol['atr_percentile']:.0f}th percentile, "
            f"bands expanding {width_ratio:.2f}x - abnormal volatility"
        )
    elif vol["atr_percentile"] <= 25.0 and width_ratio <= 0.8:
        regime = "quiet_contraction"
        trend = "sideways"
        evidence = (
            f"ATR at {vol['atr_percentile']:.0f}th percentile, "
            f"bands contracting {width_ratio:.2f}x - thin market"
        )
    elif abs(separation) < 0.35:
        regime = "ranging"
        trend = "sideways"
        evidence = (
            f"EMA separation only {separation:.2f} ATRs, ATR at "
            f"{vol['atr_percentile']:.0f}th percentile - chop zone"
        )
    else:
        regime = "transition"
        trend = "bullish" if separation > 0 else "bearish"
        evidence = (
            f"EMA separation {separation:.2f} ATRs between trend and chop - "
            "transitional regime"
        )

    return {
        "regime": regime,
        "trend": trend,
        "evidence": evidence,
        "ema_separation_atr": round(separation, 3),
        "bollinger_width_ratio": width_ratio,
        "atr_percentile": vol["atr_percentile"],
        "atr_z": vol["atr_z"],
        "atr": vol["atr"],
        "abnormal_volatility": vol["abnormal_volatility"],
    }


def session_name(hour: int | None = None) -> tuple[str, list[str]]:
    """Active sessions for a UTC hour; returns (primary, all active)."""
    hour = datetime.now(timezone.utc).hour if hour is None else int(hour) % 24
    active: list[str] = []
    for name, start, end in SESSIONS:
        if start <= end and start <= hour < end:
            active.append(name)
        elif start > end and (hour >= start or hour < end):
            active.append(name)
    if not active:
        return "off_hours", []
    active.sort(key=lambda name: SESSION_ACTIVITY[name])
    return active[0], active


def current_session(hour: int | None = None) -> dict:
    primary, active = session_name(hour)
    return {
        "primary": primary,
        "active": active,
        "hour_utc": datetime.now(timezone.utc).hour if hour is None else int(hour) % 24,
    }


def _session_score(primary: str) -> float:
    """Score the active session from the journal's historical win rate."""
    try:
        from memory.learning import best_sessions

        for entry in best_sessions() or []:
            if entry["session"] == f"{int(datetime.now(timezone.utc).hour):02d}:00 UTC":
                return max(0.0, min(100.0, entry["win_rate"]))
    except Exception as exc:  # noqa: BLE001
        logger.debug("session score unavailable: %s", exc)
    if primary == "off_hours":
        return 40.0
    return 50.0


def _news_score(settings) -> float:
    try:
        from tools.news import get_provider

        window = timedelta(minutes=settings.risk.news_blackout_minutes)
        now = datetime.now(timezone.utc)
        score = 100.0
        for event in get_provider().upcoming(hours=24) or []:
            event_time = getattr(event, "time", None)
            if event_time is None:
                continue
            delta = abs(event_time - now)
            if event.impact == "high":
                if delta <= window:
                    return 30.0
                if delta <= 2 * window:
                    score = min(score, 55.0)
            elif event.impact == "medium" and delta <= window:
                score = min(score, 75.0)
        return score
    except Exception as exc:  # noqa: BLE001
        logger.debug("news score unavailable: %s", exc)
        return 100.0


def market_health(
    symbol: str, timeframe: str = "H1", lookback: int = 400
) -> dict[str, Any]:
    """Full Market Intelligence report for one symbol: health score + evidence."""
    client = get_client()
    client.ensure_connected()
    settings = get_settings()
    risk = settings.risk
    tf = timeframe.upper()
    errors: list[str] = []

    try:
        frame = client.rates(symbol, tf, lookback)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"no {tf} data: {exc}")
        frame = None
    if frame is None or len(frame) < 60:
        errors.append(f"not enough {tf} candles to assess {symbol}")
        return {
            "symbol": symbol,
            "timeframe": tf,
            "health_score": 0.0,
            "verdict": "unavailable",
            "regime": "unknown",
            "errors": errors,
        }

    try:
        info = client.symbol_info(symbol)
    except Exception as exc:  # noqa: BLE001
        info = {}
        errors.append(f"symbol info unavailable: {exc}")
    try:
        tick = client.tick(symbol)
    except Exception as exc:  # noqa: BLE001
        tick = {}
        errors.append(f"tick unavailable: {exc}")

    trend = analyze_trend(frame)
    regime = market_regime(frame)
    session = current_session()

    components: list[dict[str, Any]] = []
    evidence: list[str] = []

    trend_score = round(trend.confidence * 100.0, 1)
    components.append(
        {
            "name": "trend",
            "score": trend_score,
            "weight": HEALTH_WEIGHTS["trend"],
            "note": f"EMA50/200 + structure: {trend.direction} "
            f"(HH {trend.hh}, HL {trend.hl}, LH {trend.lh}, LL {trend.ll})",
        }
    )
    evidence.append(f"trend {trend.direction} with {trend.confidence:.0%} clarity")

    volatility_score = round(max(0.0, 100.0 - 2.0 * abs(regime["atr_percentile"] - 50.0)), 1)
    if regime["abnormal_volatility"]:
        volatility_score = min(volatility_score, 35.0)
    components.append(
        {
            "name": "volatility",
            "score": volatility_score,
            "weight": HEALTH_WEIGHTS["volatility"],
            "note": f"ATR {regime['atr']:.6f} at {regime['atr_percentile']:.0f}th "
            f"percentile (z {regime['atr_z']:+.1f})",
        }
    )
    if regime["abnormal_volatility"]:
        evidence.append(
            f"abnormal volatility: ATR at {regime['atr_percentile']:.0f}th percentile"
        )

    regime_score = REGIME_SCORE.get(regime["regime"], 40.0)
    components.append(
        {
            "name": "regime",
            "score": regime_score,
            "weight": HEALTH_WEIGHTS["regime"],
            "note": regime["regime"].replace("_", " ") + f" - {regime['evidence']}",
        }
    )
    evidence.append(f"regime: {regime['regime']}")

    spread_score = 100.0
    spread_note = "spread data unavailable"
    try:
        point = float(info.get("point") or 0.0)
        if point and tick:
            spread_points = (
                float(tick.get("ask") or 0.0) - float(tick.get("bid") or 0.0)
            ) / point
            max_spread = float(risk.max_spread_points) or 40.0
            spread_score = round(max(0.0, 100.0 * (1.0 - spread_points / max_spread)), 1)
            spread_note = f"spread {spread_points:.1f} points (limit {max_spread:.0f})"
    except Exception as exc:  # noqa: BLE001
        spread_note = f"spread unavailable: {exc}"
    components.append(
        {
            "name": "spread",
            "score": spread_score,
            "weight": HEALTH_WEIGHTS["spread"],
            "note": spread_note,
        }
    )
    if spread_score < 50.0:
        evidence.append("spread near or above the configured limit")

    session_score = round(_session_score(session["primary"]), 1)
    components.append(
        {
            "name": "session",
            "score": session_score,
            "weight": HEALTH_WEIGHTS["session"],
            "note": f"{session['primary']} active ({', '.join(session['active']) or 'none'})",
        }
    )
    if session["primary"] == "off_hours":
        evidence.append("off-hours session with thin liquidity")

    news_score = round(_news_score(settings), 1)
    components.append(
        {
            "name": "news",
            "score": news_score,
            "weight": HEALTH_WEIGHTS["news"],
            "note": "no high-impact events imminent"
            if news_score >= 100.0
            else "high-impact news in the blackout window",
        }
    )
    if news_score < 60.0:
        evidence.append("high-impact news inside the blackout window")

    try:
        open_ok, reason = client.market_open(symbol)
    except Exception as exc:  # noqa: BLE001
        open_ok, reason = False, f"market check failed: {exc}"
    liquidity_score = 100.0 if open_ok else 0.0
    components.append(
        {
            "name": "liquidity",
            "score": liquidity_score,
            "weight": HEALTH_WEIGHTS["liquidity"],
            "note": f"market open ({reason})" if open_ok else f"market closed: {reason}",
        }
    )
    if not open_ok:
        evidence.append(f"market closed: {reason}")

    health = sum(c["score"] * c["weight"] for c in components)
    health = round(max(0.0, min(100.0, health)), 1)

    if health >= 70.0:
        verdict = "tradeable"
    elif health >= 50.0:
        verdict = "cautious"
    else:
        verdict = "avoid"

    return {
        "symbol": symbol,
        "timeframe": tf,
        "health_score": health,
        "verdict": verdict,
        "regime": regime["regime"],
        "trend": trend.direction,
        "trend_confidence": round(trend.confidence * 100.0, 1),
        "abnormal_volatility": regime["abnormal_volatility"],
        "atr_percentile": regime["atr_percentile"],
        "session": session,
        "components": components,
        "evidence": evidence,
        "errors": errors,
    }


def scan_markets(
    symbols: list[str] | None = None,
    timeframe: str = "H1",
    lookback: int = 300,
    min_score: float = 0.0,
    max_results: int = 12,
) -> dict[str, Any]:
    """Rank multiple symbols by Market Health Score."""
    client = get_client()
    client.ensure_connected()
    if symbols:
        candidates = [s.upper() for s in symbols if s and s.strip()]
    else:
        try:
            all_symbols = client.symbols() or []
        except Exception as exc:  # noqa: BLE001
            all_symbols = []
            logger.debug("symbol list unavailable: %s", exc)
        candidates = [
            s
            for s in all_symbols
            if any(group in s.upper() for group in DEFAULT_SCAN_GROUPS)
        ]

    ranked: list[dict[str, Any]] = []
    failed: list[str] = []
    for symbol in candidates[:60]:
        try:
            report = market_health(symbol, timeframe=timeframe, lookback=lookback)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{symbol}: {exc}")
            continue
        if report.get("verdict") == "unavailable":
            failed.append(f"{symbol}: insufficient history")
            continue
        if report.get("health_score", 0.0) >= min_score:
            ranked.append(
                {
                    "symbol": symbol,
                    "health_score": report["health_score"],
                    "verdict": report.get("verdict", "unavailable"),
                    "regime": report.get("regime", "unknown"),
                    "trend": report.get("trend", "sideways"),
                    "abnormal_volatility": report.get("abnormal_volatility", False),
                    "atr_percentile": report.get("atr_percentile", 0.0),
                }
            )

    ranked.sort(key=lambda row: -row["health_score"])
    return {
        "timeframe": timeframe.upper(),
        "scanned": len(candidates),
        "ranked": ranked[:max_results],
        "failed": failed,
    }


def health_commentary(report: dict[str, Any]) -> str:
    """One-line narrative for the health report, for logs and coaching."""
    verdict = report.get("verdict", "unavailable")
    return (
        f"{report.get('symbol', '?')} {report.get('regime', '?')} "
        f"health {report.get('health_score', 0.0):.0f}/100 ({verdict})"
    )

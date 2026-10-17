"""Boom/Crash spike-drift strategy detector.

Detects oversized spikes in Boom/Crash indices and identifies pullback
entries with Fibonacci retracement levels, a stop beyond the spike extreme
plus an ATR buffer, a 1R partial-exit level and a chandelier trailing exit.

Exit model (research 2026-08-13, Structural Idea B): once unrealized profit
reaches +1R the trail arms and the stop becomes a chandelier exit at
highest-high-since-entry minus N x ATR (long) / lowest-low plus N x ATR
(short), N = 1.0-1.5 ATR. No fixed TP is placed on live watcher orders;
``tp_price`` is kept as an informational/backtest-baseline field only.

Signal flow mirrors ``backtest_boom_crash.detect_spike_at_index`` so that
live detection and historical backtests stay consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from mt5_mcp.common import BOOM_CRASH_SYMBOLS
from mt5_mcp.indicators import atr, rsi
from mt5_mcp.client import get_client
from mt5_mcp.strategy import trend_analysis

# ---- Configuration ------------------------------------------------------

# Default reward-to-risk ratio for the strategy (informational; the live
# watcher exits with the chandelier trail, not a fixed TP)
DEFAULT_RR = 1.5

# Minimum spike size (as a multiple of ATR) to qualify as a valid spike
MIN_SPIKE_ATR = 5.0

# Spike quality bands (2026-10-11 edge improvement)
# Reject spikes outside these bounds: too small = noise, too large = overextended
SPIKE_MAGNITUDE_MIN_ATR = 1.5  # Below this = noise, reject
SPIKE_MAGNITUDE_MAX_ATR = 5.0  # Above this = overextended, reject
SPIKE_QUALITY_SWEET_SPOT = (2.5, 4.0)  # Optimal range for confidence boost

# Fibonacci retracement levels for entries (expanded 2026-10-11)
# Dynamic selection: pick level closest to current price for better fill rate
RET_LEVELS = (0.382, 0.5, 0.618, 0.786)  # 38.2%, 50%, 61.8%, 78.6%

# Stop-loss distance beyond the spike extreme, in ATR units
# Volatility-regime aware (2026-10-11): high-freq indices use tighter SL
SL_ATR_BUFFER = 2.0
SL_ATR_BUFFER_HIGH_FREQ = 1.5  # For BOOM/CRASH 1000/500/300 (lower ATR, tighter stops)
SL_ATR_BUFFER_LOW_FREQ = 2.5   # For BOOM/CRASH 50/99/100/150/200 (higher ATR, wider stops)

# How far (in ATR units) the current price may be from the entry level
ENTRY_BUFFER_ATRS = 5.0

# Spike window length in M1 bars
LOOKBACK_BARS = 30

# ---- Chandelier trailing exit (research 2026-08-13, Structural Idea B) ---
# Arm the trail once unrealized profit reaches +1R; trail the stop at
# ``TRAIL_ATR_DISTANCE`` ATR behind the extreme since entry; no fixed TP.
# Configurable via settings.json -> "boom_crash" (see mt5_mcp.config).
DEFAULT_TRAIL_ARM_R = 1.0
DEFAULT_TRAIL_DISTANCE_ATR = 1.0
TRAIL_ARM_R = DEFAULT_TRAIL_ARM_R
TRAIL_ATR_DISTANCE = DEFAULT_TRAIL_DISTANCE_ATR
TRAIL_ATR_DISTANCE_MIN = 1.0
TRAIL_ATR_DISTANCE_MAX = 1.5

# 1R partial exit: close this fraction of the position when +1R is reached.
PARTIAL_EXIT_FRACTION = 0.5


# ---- Symbol mapping -----------------------------------------------------

# Volatility regimes (2026-10-11 edge improvement)
# High-freq: frequent small spikes, lower ATR (~0.5-2.0), tighter SL/TP
# Low-freq: infrequent large spikes, higher ATR (~5-7), wider SL/TP
HIGH_FREQ_SYMBOLS = {"BOOM1000", "BOOM500", "BOOM300", "CRASH1000", "CRASH500", "CRASH300"}
LOW_FREQ_SYMBOLS = {"BOOM200", "BOOM150", "BOOM100", "BOOM99", "BOOM50",
                    "CRASH200", "CRASH150", "CRASH100", "CRASH99", "CRASH50"}


def get_volatility_regime(symbol: str) -> str:
    """Classify symbol into volatility regime: 'high_freq' or 'low_freq'."""
    norm = (symbol or "").upper().replace(" ", "")
    if norm.startswith("BOOM"):
        key = "BOOM" + norm[4:].lstrip("0")
    elif norm.startswith("CRASH"):
        key = "CRASH" + norm[5:].lstrip("0")
    else:
        return "low_freq"  # default to conservative
    if key in HIGH_FREQ_SYMBOLS:
        return "high_freq"
    return "low_freq"


def _sl_buffer_for(symbol: str) -> float:
    """Return regime-appropriate SL ATR buffer."""
    return SL_ATR_BUFFER_HIGH_FREQ if get_volatility_regime(symbol) == "high_freq" else SL_ATR_BUFFER_LOW_FREQ


def _build_symbol_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for symbol in BOOM_CRASH_SYMBOLS:
        kind = "Boom" if symbol.startswith("BOOM") else "Crash"
        number = symbol[len(kind) :].lstrip("0") or "0"
        mapping[symbol] = f"{kind} {int(number)} Index"
    return mapping


MT5_SYMBOL_MAP: dict[str, str] = _build_symbol_map()


def mt5_symbol(api_symbol: str) -> str:
    """Convert an API symbol name (``BOOM900``) to its MT5 name (``Boom 900 Index``)."""
    api_symbol = (api_symbol or "").strip()
    upper = api_symbol.upper()
    if upper.endswith(" INDEX"):
        # Extract kind + number, return proper MT5 case
        kind = "Crash" if upper.startswith("CRASH") else "Boom"
        # Strip kind and " Index" suffix to get the number
        middle = upper[len(kind):-len(" INDEX")].strip()
        number = middle.lstrip("0") or "0"
        return f"{kind} {int(number)} Index"
    api_symbol = upper
    if api_symbol in MT5_SYMBOL_MAP:
        return MT5_SYMBOL_MAP[api_symbol]
    kind = "Boom" if api_symbol.startswith("BOOM") else "Crash"
    number = api_symbol[len(kind) :].lstrip("0") or "0"
    return f"{kind} {int(number)} Index"


# ---- Signal model -------------------------------------------------------

@dataclass
class SpikeSignal:
    symbol: str
    direction: str  # "long" / "short"
    spike_high: float
    spike_low: float
    spike_time: Optional[datetime]
    spike_age_candles: int
    spike_size: float
    atr: float
    drift_direction: str  # expected post-spike drift ("up" / "down")
    entry_price: float
    sl_price: float
    # Informational only: the fixed-TP exit is replaced by the chandelier
    # trail on live watcher positions (research 2026-08-13).
    tp_price: float
    tp_price_drift: float
    partial_tp_price: float
    trail_sl_price: float
    retracement: float
    current_price: float
    confidence: float
    reason: str
    rr_used: float


# ---- Data access --------------------------------------------------------

def _candles_any(symbol: str, count: int) -> list[dict]:
    """Fetch the newest ``count`` M1 candles for ``symbol`` as records."""
    try:
        client = get_client()
        client.ensure_connected()
        mt5_name = mt5_symbol(symbol)
        frame = client.rates(mt5_name, "M1", count)
    except Exception:
        return []
    if frame is None or len(frame) == 0:
        return []
    if isinstance(frame, pd.DataFrame):
        rows = frame.to_dict("records")
    else:
        rows = list(frame)
    out: list[dict] = []
    for row in rows:
        out.append(
            {
                "time": row.get("time"),
                "open": float(row.get("open")),
                "high": float(row.get("high")),
                "low": float(row.get("low")),
                "close": float(row.get("close")),
            }
        )
    return out


def _to_epoch(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ---- Core detection -----------------------------------------------------

def detect_spike(
    symbol: str,
    lookback_minutes: int = 60,
    spike_threshold_atr: float = 2.5,
    min_spike_ticks: int = 10,
    rr_override: Optional[float] = None,
) -> Optional[SpikeSignal]:
    """Detect a Boom/Crash spike pattern and return a trading signal.

    Parameters
    ----------
    symbol : str
        Trading symbol (e.g., ``BOOM900``, ``CRASH500``, ``Volatility 50 Index``).
    lookback_minutes : int
        Lookback window for spike detection in M1 bars.
    spike_threshold_atr : float
        Minimum spike size as a multiple of ATR to qualify.
    min_spike_ticks : int
        Minimum spike size in ticks (0.01 steps).
    rr_override : float, optional
        Override the default reward-to-risk ratio (1.5).

    Returns
    -------
    SpikeSignal or None
        A signal for valid patterns. A detected spike whose extreme has
        already been broken is returned watch-only with ``confidence == 0``.
    """
    symbol = (symbol or "").upper()
    # Allow BOOM, CRASH and Volatility indices; others are rejected.
    if not (symbol.startswith("BOOM") or symbol.startswith("CRASH")
            or symbol.startswith("Volatility")):
        return None

    lookback = max(lookback_minutes, 2)
    candles = _candles_any(symbol, lookback)
    if len(candles) < lookback:
        return None

    rows = candles[-lookback:]
    times = [_to_epoch(row["time"]) for row in rows]
    highs = [row["high"] for row in rows]
    lows = [row["low"] for row in rows]
    closes = [row["close"] for row in rows]

    df = pd.DataFrame(
        {
            "time": times,
            "open": [row["open"] for row in rows],
            "high": highs,
            "low": lows,
            "close": closes,
        }
    )
    atr_series = atr(df, period=14)
    current_atr_val = float(atr_series.iloc[-1]) if len(atr_series) else 0.0
    if current_atr_val <= 0:
        return None

    # Current close used for extreme-versus-close comparison.
    current_price = closes[-1]

    # Determine whether the spike was a new high or new low by comparing
    # the absolute distance from the pre-spike close (not current price,
    # which may have partially recovered). The larger move decides the
    # fade direction (the opposite extreme is traded).
    max_high = max(highs)
    min_low = min(lows)
    max_high_idx = int(max(range(lookback), key=lambda i: highs[i]))
    min_low_idx = int(min(range(lookback), key=lambda i: lows[i]))
    # Use the close before the spike candle as the reference.
    pre_spike_close = (
        closes[max_high_idx - 1]
        if abs(max_high - closes[0]) > abs(closes[0] - min_low)
        else closes[min_low_idx - 1]
    ) if (max_high_idx > 0 or min_low_idx > 0) else closes[0]
    high_dist = abs(max_high - pre_spike_close)
    low_dist = abs(pre_spike_close - min_low)
    spike_up_expected = high_dist > low_dist
    # spike_up_expected=True  → spike was a new high → fade short
    # spike_up_expected=False → spike was a new low  → fade long

    if spike_up_expected:
        spike_high = highs[max_high_idx]
        spike_low = min(lows[: max_high_idx + 1]) if max_high_idx > 0 else lows[0]
        direction = "short"
        drift_direction = "down"
    else:
        spike_low = lows[min_low_idx]
        spike_high = max(highs[: min_low_idx + 1]) if min_low_idx > 0 else highs[0]
        direction = "long"
        drift_direction = "up"

    spike_size = spike_high - spike_low
    spike_atr_ratio = spike_size / current_atr_val

    # Spike quality filter (2026-10-11): reject noise and overextended spikes
    if spike_atr_ratio < SPIKE_MAGNITUDE_MIN_ATR:
        return None  # noise: spike too small
    if spike_atr_ratio > SPIKE_MAGNITUDE_MAX_ATR:
        return None  # overextended: spike too large, mean reversion unlikely

    if spike_size < spike_threshold_atr * current_atr_val:
        return None
    if spike_size < min_spike_ticks * 0.01:
        return None

    extreme_idx = max_high_idx if spike_up_expected else min_low_idx
    spike_age_candles = lookback - 1 - extreme_idx
    current_price = closes[-1]
    spike_time = datetime.fromtimestamp(times[extreme_idx], tz=timezone.utc)

    # ---- Entry / SL / TP (mirrors backtest_boom_crash.detect_spike_at_index)
    # Dynamic entry zones (2026-10-11): test all Fib levels, pick closest to price
    sign = -1.0 if spike_up_expected else 1.0
    sl_buffer = _sl_buffer_for(symbol)  # regime-aware SL buffer

    if spike_up_expected:
        entry_levels = [(spike_high - spike_size * lvl, lvl) for lvl in RET_LEVELS]
        sl_price = spike_high + current_atr_val * sl_buffer
    else:
        entry_levels = [(spike_low + spike_size * lvl, lvl) for lvl in RET_LEVELS]
        sl_price = spike_low - current_atr_val * sl_buffer

    # Pick the retracement level closest to the current price (best fill probability)
    level, retracement = min(entry_levels, key=lambda x: abs(current_price - x[0]))

    risk = abs(sl_price - level)
    rr = rr_override if rr_override is not None else DEFAULT_RR
    # Informational fixed target (backtest baseline); live exits use the
    # chandelier trail below, so this TP is NOT placed by the watcher.
    tp_price = level + sign * risk * rr

    # Drift target: ~half the spike size beyond the entry. Informational /
    # disabled on live trades - the chandelier trail replaces fixed targets.
    tp_price_drift = level + sign * spike_size * 0.5

    # 1R partial exit (kept from the strategy spec) - close
    # PARTIAL_EXIT_FRACTION of the position when +1R is reached.
    partial_tp_price = level + sign * risk

    # Chandelier trail (research 2026-08-13): armed at +1R, stop trails at
    # TRAIL_ATR_DISTANCE ATR behind the extreme since entry, never moved
    # backward. At signal time the position does not exist yet, so this is
    # the reference stop the trail would set the moment +1R is reached
    # (extreme ~= entry +- risk at that instant). The live exit module
    # recomputes it from actual highs/lows while the position is open.
    trail_sl_price = level + sign * (risk - TRAIL_ATR_DISTANCE * current_atr_val)

    # Broken spike: extreme already taken out -> watch-only signal.
    broken = (spike_up_expected and current_price >= spike_high) or (
        not spike_up_expected and current_price <= spike_low
    )

    # Confidence: base 60 scaled by spike strength + confluence factors.
    # Factors: spike size, RSI extreme, spike age, trend alignment, spike quality.
    spike_score = min((spike_size / current_atr_val) * 3.0, 15.0)  # max +15

    # Spike quality score (2026-10-11): bonus for sweet-spot magnitude
    quality_score = 0.0
    if SPIKE_QUALITY_SWEET_SPOT[0] <= spike_atr_ratio <= SPIKE_QUALITY_SWEET_SPOT[1]:
        quality_score = 8.0  # optimal spike size
    elif spike_atr_ratio < SPIKE_QUALITY_SWEET_SPOT[0]:
        quality_score = max(0.0, (spike_atr_ratio - SPIKE_MAGNITUDE_MIN_ATR) /
                           (SPIKE_QUALITY_SWEET_SPOT[0] - SPIKE_MAGNITUDE_MIN_ATR) * 8.0)
    else:  # above sweet spot but below max
        quality_score = max(0.0, (SPIKE_MAGNITUDE_MAX_ATR - spike_atr_ratio) /
                           (SPIKE_MAGNITUDE_MAX_ATR - SPIKE_QUALITY_SWEET_SPOT[1]) * 8.0)

    # RSI extreme: spikes into overbought/oversold increase confidence
    rsi_val = 50.0
    try:
        rsi_series = rsi(df, period=14)
        if len(rsi_series):
            rsi_val = float(rsi_series.iloc[-1])
    except Exception:
        pass
    if spike_up_expected and rsi_val > 65:
        rsi_score = min((rsi_val - 65) * 0.3, 8.0)  # max +8 for RSI > 91
    elif not spike_up_expected and rsi_val < 35:
        rsi_score = min((35 - rsi_val) * 0.3, 8.0)
    else:
        rsi_score = 0.0

    # Spike age: younger spikes are higher quality
    age_score = max(0.0, (10 - spike_age_candles) * 0.5)  # max +5 at age 0

    # Trend alignment: spike against the trend is a fade (good), with trend is a continuation (less reliable)
    trend_dir = "sideways"
    try:
        trend_result = trend_analysis(df, fast=50, slow=200)
        trend_dir = trend_result.get("direction", "sideways")
    except Exception:
        pass
    trend_aligned = (
        (spike_up_expected and trend_dir == "bearish") or  # fade short into bearish trend
        (not spike_up_expected and trend_dir == "bullish")  # fade long into bullish trend
    )
    trend_score = 5.0 if trend_aligned else 0.0

    confidence = 60.0 + spike_score + rsi_score + age_score + trend_score + quality_score
    confidence = min(confidence, 95.0)

    if broken:
        confidence = 0.0
        reason = "watch-only: spike extreme broken"

    # Price must be inside the entry buffer to be actionable.
    elif abs(current_price - level) > current_atr_val * ENTRY_BUFFER_ATRS:
        confidence = 0.0
        reason = "watch-only: price outside entry buffer"

    else:
        regime = get_volatility_regime(symbol)
        quality_label = "optimal" if quality_score >= 7 else ("decent" if quality_score >= 4 else "marginal")
        reason = (
            f"{direction.upper()} after {spike_age_candles} bar "
            f"{'up' if spike_up_expected else 'down'} spike, "
            f"retrace {retracement:.0%}, ATR {current_atr_val:.3f}, "
            f"spike {spike_atr_ratio:.1f}xATR ({quality_label}), {regime}"
        )

    return SpikeSignal(
        symbol=symbol,
        direction=direction,
        spike_high=spike_high,
        spike_low=spike_low,
        spike_time=spike_time,
        spike_age_candles=spike_age_candles,
        spike_size=spike_size,
        atr=current_atr_val,
        drift_direction=drift_direction,
        entry_price=level,
        sl_price=sl_price,
        tp_price=tp_price,
        tp_price_drift=tp_price_drift,
        partial_tp_price=partial_tp_price,
        trail_sl_price=trail_sl_price,
        retracement=retracement,
        current_price=current_price,
        confidence=confidence,
        reason=reason,
        rr_used=rr,
    )


def scan_boom_crash_opportunities(
    symbols: Optional[list[str]] = None,
    min_confidence: float = 50.0,
    lookback_minutes: int = 60,
) -> list[SpikeSignal]:
    """Scan Boom/Crash symbols and return qualifying spike-drift signals."""
    if symbols is None:
        symbols = list(BOOM_CRASH_SYMBOLS)
    signals: list[SpikeSignal] = []
    for symbol in symbols:
        try:
            signal = detect_spike(
                symbol,
                lookback_minutes=lookback_minutes,
                min_spike_ticks=10,
            )
        except Exception:
            continue
        if signal is None:
            continue
        if signal.confidence > 0 and signal.confidence >= min_confidence:
            signals.append(signal)
    return sorted(signals, key=lambda s: s.confidence, reverse=True)


# ---- Market state -------------------------------------------------------

def get_market_state(symbol: str) -> dict[str, Any]:
    """Comprehensive state for a Boom/Crash symbol: price, trend, RSI, spike."""
    norm = symbol.strip().upper()
    mt5_name = mt5_symbol(norm)

    state: dict[str, Any] = {"symbol": norm, "mt5_symbol": mt5_name}

    try:
        client = get_client()
        client.ensure_connected()
        tick = client.tick(mt5_name)
        state["bid"] = float(tick.get("bid", 0.0))
        state["ask"] = float(tick.get("ask", 0.0))
        state["current_price"] = (float(tick.get("bid", 0.0)) + float(tick.get("ask", 0.0))) / 2.0
    except Exception as exc:
        state["price_error"] = str(exc)

    try:
        frame = client.rates(mt5_name, "M1", 120)
        if frame is not None and len(frame):
            atr_val = float(atr(frame, period=14).iloc[-1]) if len(atr(frame, period=14)) else 0.0
            rsi_val = float(rsi(frame["close"], period=14).iloc[-1]) if len(rsi(frame["close"])) else 50.0
            state["atr"] = atr_val
            state["rsi"] = round(rsi_val, 1)
    except Exception:
        pass

    try:
        trend = trend_analysis(mt5_name)
        if isinstance(trend, dict):
            state["trend"] = trend
    except Exception:
        pass

    signal = None
    try:
        signal = detect_spike(norm, lookback_minutes=60, min_spike_ticks=10)
    except Exception:
        signal = None
    if signal:
        state["spike"] = _format_signal(signal, full=False)
    else:
        state["spike"] = {"detected": False}

    state["scanned_at"] = datetime.now(timezone.utc).isoformat()
    return state


# ---- Helpers ------------------------------------------------------------

def _calculate_rr(rr_override: Optional[float] = None) -> float:
    """Calculate the reward-to-risk ratio to use."""
    return rr_override if rr_override is not None else DEFAULT_RR


def _format_signal(signal: SpikeSignal, full: bool = True) -> dict[str, Any]:
    """Serialize a SpikeSignal for API/tool output."""
    data: dict[str, Any] = {
        "symbol": signal.symbol,
        "mt5_symbol": mt5_symbol(signal.symbol),
        "direction": signal.direction,
        "spike_high": signal.spike_high,
        "spike_low": signal.spike_low,
        "spike_time": signal.spike_time.isoformat() if signal.spike_time else None,
        "spike_age_candles": signal.spike_age_candles,
        "spike_size": signal.spike_size,
        "atr": signal.atr,
        "drift_direction": signal.drift_direction,
        "entry_price": signal.entry_price,
        "sl_price": signal.sl_price,
        "tp_price": signal.tp_price,
        "retracement": signal.retracement,
        "current_price": signal.current_price,
        "confidence": signal.confidence,
        "reason": signal.reason,
        "rr": signal.rr_used,
    }
    if full:
        data.update(
            {
                "tp_price_drift": signal.tp_price_drift,
                "partial_tp_price": signal.partial_tp_price,
                "trail_sl_price": signal.trail_sl_price,
                "detected": signal.confidence > 0,
            }
        )
    return data


# ---- Main entry point ---------------------------------------------------

def run_strategy(
    symbol: str,
    rr: float = DEFAULT_RR,
    min_zone_score: float = 70.0,
    min_trade_score: float = 70.0,
    min_confidence: float = 90.0,
) -> Optional[dict]:
    """Run the boom/crash spike strategy for a given symbol.

    Returns a serialized signal dict for qualifying setups, else None.
    """
    signal = detect_spike(symbol, rr_override=rr)
    if signal is None:
        return None
    if signal.confidence < min_confidence:
        return None
    return _format_signal(signal)


# ---- MCP registration ---------------------------------------------------

def register(mcp) -> None:
    @mcp.tool(
        name="detect_spike",
        description="Detect a Boom/Crash spike-drift pattern: oversized spike, 38.2%/50% Fibonacci "
        "pullback entry, SL beyond the spike extreme plus 2 ATR, 1R partial exit and a chandelier "
        "trailing exit (armed at +1R, 1.0-1.5 ATR, no fixed TP). Returns entry/SL/TP/confidence "
        "or detected=false.",
    )
    def tool_detect_spike(
        symbol: str,
        lookback_minutes: int = 60,
        spike_threshold_atr: float = 2.5,
        min_spike_ticks: int = 10,
    ) -> dict:
        signal = detect_spike(
            symbol,
            lookback_minutes=lookback_minutes,
            spike_threshold_atr=spike_threshold_atr,
            min_spike_ticks=min_spike_ticks,
        )
        if signal:
            return _format_signal(signal)
        return {"detected": False, "symbol": (symbol or "").upper()}

    @mcp.tool(
        name="scan_boom_crash",
        description="Scan all Boom/Crash indices for spike-drift opportunities ranked by confidence. "
        "Returns entry/SL/TP/confidence for each qualifying symbol.",
    )
    def tool_scan_boom_crash(min_confidence: float = 50.0) -> dict:
        signals = scan_boom_crash_opportunities(
            list(BOOM_CRASH_SYMBOLS), min_confidence=min_confidence
        )
        return {
            "symbols_scanned": len(BOOM_CRASH_SYMBOLS),
            "signals": [_format_signal(s) for s in signals],
            "count": len(signals),
        }

    @mcp.tool(
        name="boom_crash_market_state",
        description="Comprehensive market state for a Boom/Crash symbol: current price, ATR, RSI, "
        "trend and any active spike signal.",
    )
    def tool_boom_crash_state(symbol: str) -> dict:
        return get_market_state(symbol)


# ---- When run as script -------------------------------------------------

if __name__ == "__main__":
    import sys as _sys

    _symbol = _sys.argv[1] if len(_sys.argv) > 1 else "BOOM900"
    _signal = detect_spike(_symbol)
    if _signal:
        print(f"Spike detected: {_signal.direction} {_signal.symbol}")
        print(f"  Entry: {_signal.entry_price}")
        print(f"  SL: {_signal.sl_price}")
        print(f"  TP: {_signal.tp_price}")
        print(f"  Confidence: {_signal.confidence}%")
        print(f"  Reason: {_signal.reason}")
    else:
        print(f"No valid spike pattern for {_symbol}")
"""Prop-firm pre-trade filters: spread, ADR, session, and symbol validation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

import numpy as np
import pandas as pd

from trading_ai.config.settings import settings, SESSIONS


@dataclass
class PreTradeCheckResult:
    allowed: bool
    reason: str = ""
    spread_pips: float = 0.0
    adr_pips: float = 0.0
    session: str = ""
    symbol: str = ""


def compute_adr_pips(df: pd.DataFrame, lookback: int = 20) -> float:
    """Compute Average Daily Range in pips from OHLC data (assumes H1 or higher)."""
    if df is None or len(df) < lookback:
        return 0.0
    # Daily range = high - low per day
    daily = df.resample("D").agg({"high": "max", "low": "min"})
    daily["range"] = daily["high"] - daily["low"]
    # Convert to pips: for JPY pairs 1 pip = 0.01, others 0.0001
    # We'll detect by symbol in the data or use a heuristic
    # Default to 5-digit pricing (0.0001 = 1 pip)
    sample_price = float(df["close"].iloc[-1])
    pip_size = 0.01 if sample_price < 10 else 0.0001
    adr = daily["range"].tail(lookback).mean() / pip_size
    return float(adr)


def current_spread_pips(bid: float, ask: float, symbol: str) -> float:
    """Calculate current spread in pips from bid/ask."""
    if bid <= 0 or ask <= 0 or ask <= bid:
        return 0.0
    pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
    return round((ask - bid) / pip_size, 1)


def get_active_session(utc_now: datetime) -> str:
    """Return the current active session name(s)."""
    t = utc_now.time()
    active = [name for name, win in SESSIONS.items() if win.contains(t)]
    return ",".join(active) if active else "off"


def check_pre_trade(
    symbol: str,
    bid: float,
    ask: float,
    df_h1: Optional[pd.DataFrame] = None,
    utc_now: Optional[datetime] = None,
) -> PreTradeCheckResult:
    """
    Full pre-trade validation for prop-firm compliance.
    
    Checks:
    1. Symbol in allowed list
    2. Spread <= max_spread_pips
    3. ADR within [min_adr_pips, max_adr_pips]
    4. Session filter (optional)
    """
    utc_now = utc_now or datetime.utcnow()
    
    # 1. Symbol check
    if symbol not in settings.prop_allowed_symbols:
        return PreTradeCheckResult(
            allowed=False,
            reason=f"Symbol {symbol} not in prop_allowed_symbols",
            symbol=symbol,
        )
    
    # 2. Spread check
    spread = current_spread_pips(bid, ask, symbol)
    if spread > settings.max_spread_pips:
        return PreTradeCheckResult(
            allowed=False,
            reason=f"Spread {spread:.1f} pips > max {settings.max_spread_pips}",
            spread_pips=spread,
            symbol=symbol,
        )
    
    # 3. ADR check (if H1 data provided)
    adr = 0.0
    if df_h1 is not None and hasattr(df_h1, "__len__") and len(df_h1) >= 20:
        adr = compute_adr_pips(df_h1)
        if adr < settings.min_adr_pips:
            return PreTradeCheckResult(
                allowed=False,
                reason=f"ADR {adr:.1f} pips < min {settings.min_adr_pips}",
                spread_pips=spread,
                adr_pips=adr,
                symbol=symbol,
            )
        if adr > settings.max_adr_pips:
            return PreTradeCheckResult(
                allowed=False,
                reason=f"ADR {adr:.1f} pips > max {settings.max_adr_pips}",
                spread_pips=spread,
                adr_pips=adr,
                symbol=symbol,
            )
    
    # 4. Session filter
    session = get_active_session(utc_now)
    if settings.session_filter_enabled and session == "off":
        return PreTradeCheckResult(
            allowed=False,
            reason="Outside allowed trading sessions",
            spread_pips=spread,
            adr_pips=adr,
            session=session,
            symbol=symbol,
        )
    
    return PreTradeCheckResult(
        allowed=True,
        reason="All checks passed",
        spread_pips=spread,
        adr_pips=adr,
        session=session,
        symbol=symbol,
    )


def filter_signals_by_prop_rules(
    signals: list,
    bid_ask: dict[str, tuple[float, float]],
    market_data: dict[str, pd.DataFrame],
    utc_now: Optional[datetime] = None,
) -> list:
    """Filter a list of signals through prop-firm pre-trade checks."""
    utc_now = utc_now or datetime.utcnow()
    filtered = []
    for sig in signals:
        sym = getattr(sig, "symbol", str(getattr(sig, "symbol", "")))
        if sym not in bid_ask:
            continue
        bid, ask = bid_ask[sym]
        df_h1 = market_data.get("H1") or market_data.get(sym + "_H1")
        check = check_pre_trade(sym, bid, ask, df_h1, utc_now)
        if check.allowed:
            filtered.append(sig)
    return filtered
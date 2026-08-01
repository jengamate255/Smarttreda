"""Shared Tkinter GUI helpers for the MT5 dashboards.

Both `dashboard.py` (Catppuccin) and `tradingview_gui.py` (TradingView)
duplicate the same boilerplate: libs path setup, matplotlib import guard,
MT5 connection, technical-analysis imports, alert engine, and DPI awareness.
This module centralises that so each GUI only keeps its unique styling/layout.
"""

from __future__ import annotations

import datetime
import os
import sys
from collections import deque

import numpy as np

# --- libs path (kept for backwards compat with bundled deps) ---
LIBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs")
if os.path.isdir(LIBS):
    sys.path.insert(0, LIBS)

# --- matplotlib import guard ---
try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure

    MATPLOTLIB_OK = True
except ImportError:
    FigureCanvasTkAgg = None  # type: ignore[assignment]
    Figure = None  # type: ignore[assignment]
    MATPLOTLIB_OK = False

import MetaTrader5 as mt5  # noqa: E402

from server import MT5Config, mt5_connection  # noqa: E402
from technical_analysis import (  # noqa: E402
    adx,
    analyze_trend,
    atr,
    bollinger_bands,
    ema,
    macd,
    pivot_points,
    rsi,
    sma,
    stochastic,
    support_resistance,
)

# Re-exported so GUIs can do `from gui_common import sma, ema, ...`
__all__ = [
    "MATPLOTLIB_OK",
    "FigureCanvasTkAgg",
    "Figure",
    "MT5Config",
    "mt5_connection",
    "mt5",
    "np",
    "sma",
    "ema",
    "rsi",
    "macd",
    "bollinger_bands",
    "atr",
    "stochastic",
    "adx",
    "analyze_trend",
    "support_resistance",
    "pivot_points",
    "AlertEngine",
    "connect_mt5",
    "enable_dpi_awareness",
    "pad_front",
]


class AlertEngine:
    """Shared alert engine used by both GUIs.

    Each condition is ``(name, check_fn)``. ``check_fn(state)`` returns truthy
    when the alert should fire. Alerts are edge-triggered: they fire once on
    rising edge and reset on falling edge.
    """

    def __init__(self, callback=None):
        self.callback = callback
        self.fired: dict[str, bool] = {}
        self.conditions: list[tuple[str, object]] = []

    def add(self, name: str, fn) -> None:
        self.conditions.append((name, fn))

    # Alias matching dashboard.py's AlertSystem.add_condition
    add_condition = add

    def check(self, state: dict) -> None:
        now = datetime.datetime.now().strftime("%H:%M:%S")
        for name, fn in self.conditions:
            try:
                hit = bool(fn(state))
            except Exception:
                hit = False
            if hit and not self.fired.get(name):
                self.fired[name] = True
                msg = f"[{now}] ALERT: {name}"
                if self.callback:
                    self.callback(msg)
            elif not hit:
                self.fired[name] = False

    # Alias matching dashboard.py's AlertSystem.check_all
    check_all = check


def connect_mt5(config: MT5Config | None = None, on_error=None) -> bool:
    """Connect to MT5 using the shared singleton connection.

    Returns True on success. On failure, calls ``on_error(message)`` if given
    (e.g. to show a Tkinter messagebox) and returns False.
    """
    cfg = config or MT5Config()
    if not mt5_connection.connect(cfg):
        if on_error:
            on_error("Failed to connect to MT5 terminal")
        return False
    return True


def enable_dpi_awareness() -> None:
    """Enable Windows per-monitor DPI awareness (best-effort)."""
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def pad_front(arr: np.ndarray, n: int) -> np.ndarray:
    """Pad a short indicator series to length ``n`` by repeating its first value."""
    if len(arr) >= n:
        return arr
    return np.pad(arr, (n - len(arr), 0), mode="edge")
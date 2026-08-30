"""Market structure analysis: swing detection, HH/HL/LH/LL classification, BOS and CHoCH."""

from __future__ import annotations

import numpy as np
import pandas as pd


class MarketStructure:
    """Detect and classify market structure from OHLC data."""

    # ── Swing Detection ──────────────────────────────────────────────────

    @staticmethod
    def find_swing_highs(df: pd.DataFrame, left: int = 5, right: int = 5) -> list[int]:
        """Fractal-pivot swing highs.

        A bar is a swing high when its high is the highest high among
        ``left`` bars to the left and ``right`` bars to the right.

        Returns
        -------
        list[int]
            Sorted bar indices that are swing highs.
        """
        highs = df["high"].values
        n = len(highs)
        swings: list[int] = []
        for i in range(left, n - right):
            window = highs[i - left: i + right + 1]
            if highs[i] == window.max() and np.sum(window == highs[i]) == 1:
                swings.append(i)
        return swings

    @staticmethod
    def find_swing_lows(df: pd.DataFrame, left: int = 5, right: int = 5) -> list[int]:
        """Fractal-pivot swing lows.

        A bar is a swing low when its low is the lowest low among
        ``left`` bars to the left and ``right`` bars to the right.

        Returns
        -------
        list[int]
            Sorted bar indices that are swing lows.
        """
        lows = df["low"].values
        n = len(lows)
        swings: list[int] = []
        for i in range(left, n - right):
            window = lows[i - left: i + right + 1]
            if lows[i] == window.min() and np.sum(window == lows[i]) == 1:
                swings.append(i)
        return swings

    # ── Structure Classification ─────────────────────────────────────────

    @staticmethod
    def classify_structure(
        df: pd.DataFrame,
        swing_highs: list[int],
        swing_lows: list[int],
    ) -> pd.Series:
        """Classify each bar as HH / HL / LH / LL / NEUTRAL.

        Compared against the most recent preceding swing of the same type:
        - High > prev swing high  → HH, else LH
        - Low  > prev swing low   → HL, else LL
        - Bars without a preceding comparison stay NEUTRAL.
        """
        n = len(df)
        result = np.full(n, "NEUTRAL", dtype=object)

        highs = df["high"].values
        lows = df["low"].values

        prev_sw_h: float | None = None
        prev_sw_l: float | None = None

        # Merge swing indices in order and walk forward
        events: list[tuple[int, str]] = []
        for idx in swing_highs:
            events.append((idx, "high"))
        for idx in swing_lows:
            events.append((idx, "low"))
        events.sort(key=lambda x: x[0])

        for bar_idx in range(n):
            # Update previous swings when we pass them
            for ev_idx, ev_type in events:
                if ev_idx > bar_idx:
                    break
                if ev_idx == bar_idx:
                    if ev_type == "high":
                        prev_sw_h = highs[bar_idx]
                    else:
                        prev_sw_l = lows[bar_idx]

            if prev_sw_h is not None and highs[bar_idx] > prev_sw_h:
                result[bar_idx] = "HH"
            elif prev_sw_h is not None:
                result[bar_idx] = "LH"

            if prev_sw_l is not None and lows[bar_idx] > prev_sw_l:
                # Only override NEUTRAL / LH / LL – keep HH if already set
                if result[bar_idx] in ("NEUTRAL", "LH", "LL"):
                    result[bar_idx] = "HL"
            elif prev_sw_l is not None:
                if result[bar_idx] == "NEUTRAL":
                    result[bar_idx] = "LL"

        return pd.Series(result, index=df.index, name="structure_state")

    @staticmethod
    def get_trend(structure_series: pd.Series) -> str:
        """Derive overall trend from a structure classification series.

        Uses the last 50 bars.  More HH/HL → BULLISH, more LH/LL → BEARISH,
        otherwise RANGING.
        """
        tail = structure_series.tail(50)
        bullish = ((tail == "HH") | (tail == "HL")).sum()
        bearish = ((tail == "LH") | (tail == "LL")).sum()
        if bullish > bearish * 1.2:
            return "BULLISH"
        if bearish > bullish * 1.2:
            return "BEARISH"
        return "RANGING"

    # ── Break of Structure ───────────────────────────────────────────────

    @staticmethod
    def detect_bos(
        df: pd.DataFrame,
        swing_highs: list[int],
        swing_lows: list[int],
    ) -> pd.Series:
        """Detect Break of Structure (BOS).

        Bullish BOS (1): price closes above the most recent swing high.
        Bearish BOS (-1): price closes below the most recent swing low.
        """
        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        sw_highs_map = set(swing_highs)
        sw_lows_map = set(swing_lows)

        last_sh: float | None = None  # last swing high price
        last_sl: float | None = None  # last swing low price

        for i in range(n):
            if i in sw_highs_map:
                last_sh = highs[i]
            if i in sw_lows_map:
                last_sl = lows[i]

            if last_sh is not None and closes[i] > last_sh:
                signal[i] = 1
            elif last_sl is not None and closes[i] < last_sl:
                signal[i] = -1

        return pd.Series(signal, index=df.index, name="bos_signal")

    # ── Change of Character ──────────────────────────────────────────────

    @staticmethod
    def detect_choch(
        df: pd.DataFrame,
        swing_highs: list[int],
        swing_lows: list[int],
    ) -> pd.Series:
        """Detect Change of Character (CHoCH).

        Bullish CHoCH (1): in a bearish sequence (LL/LH), price breaks above
        the most recent lower-high.
        Bearish CHoCH (-1): in a bullish sequence (HH/HL), price breaks below
        the most recent higher-low.
        """
        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        sw_highs_map = set(swing_highs)
        sw_lows_map = set(swing_lows)

        # Track the sequence state
        last_sh: float | None = None
        last_sl: float | None = None
        prev_sh: float | None = None
        prev_sl: float | None = None
        trend_bias: str = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL

        for i in range(n):
            if i in sw_highs_map:
                if last_sh is not None:
                    prev_sh = last_sh
                last_sh = highs[i]
                if prev_sh is not None and last_sh is not None:
                    if last_sh > prev_sh:
                        trend_bias = "BULLISH"
                    elif last_sh < prev_sh:
                        trend_bias = "BEARISH"

            if i in sw_lows_map:
                if last_sl is not None:
                    prev_sl = last_sl
                last_sl = lows[i]

            # Bullish CHoCH: bearish trend, price breaks above last swing high
            if trend_bias == "BEARISH" and last_sh is not None and closes[i] > last_sh:
                signal[i] = 1
                trend_bias = "BULLISH"
            # Bearish CHoCH: bullish trend, price breaks below last swing low
            elif trend_bias == "BULLISH" and last_sl is not None and closes[i] < last_sl:
                signal[i] = -1
                trend_bias = "BEARISH"

        return pd.Series(signal, index=df.index, name="choch_signal")

    # ── Feature Integration ──────────────────────────────────────────────

    def compute_structure_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute all structure-based features for an RL agent.

        Returns a DataFrame indexed like ``df`` with columns:
        - structure_state: int encoding (HH=0, HL=1, LH=2, LL=3, NEUTRAL=4)
        - bos_signal: -1 / 0 / 1
        - choch_signal: -1 / 0 / 1
        - trend_strength: rolling normalised ratio of bullish vs bearish structure
        """
        swing_highs = self.find_swing_highs(df)
        swing_lows = self.find_swing_lows(df)

        raw_state = self.classify_structure(df, swing_highs, swing_lows)
        bos = self.detect_bos(df, swing_highs, swing_lows)
        choch = self.detect_choch(df, swing_highs, swing_lows)

        encoding = {"HH": 0, "HL": 1, "LH": 2, "LL": 3, "NEUTRAL": 4}
        encoded = raw_state.map(encoding).fillna(4).astype(np.int8)

        # Trend strength: rolling count of bullish vs bearish over 50 bars
        is_bullish = raw_state.isin(["HH", "HL"]).astype(np.float64)
        is_bearish = raw_state.isin(["LH", "LL"]).astype(np.float64)
        win = min(50, len(df))
        bull_sum = is_bullish.rolling(win, min_periods=1).sum()
        bear_sum = is_bearish.rolling(win, min_periods=1).sum()
        total = bull_sum + bear_sum
        trend_strength = np.where(
            total > 0,
            (bull_sum - bear_sum) / total,  # [-1, 1]
            0.0,
        )

        out = pd.DataFrame(index=df.index)
        out["structure_state"] = encoded
        out["bos_signal"] = bos
        out["choch_signal"] = choch
        out["trend_strength"] = trend_strength.astype(np.float32)
        return out

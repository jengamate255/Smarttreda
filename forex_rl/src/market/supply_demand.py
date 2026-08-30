"""Supply & demand zone detection and feature engineering."""

from __future__ import annotations

import numpy as np
import pandas as pd


class SupplyDemandDetector:
    """Detect supply/demand zones and compute RL-ready features."""

    # ── Zone Detection ───────────────────────────────────────────────────

    @staticmethod
    def detect_demand_zones(
        df: pd.DataFrame,
        lookback: int = 100,
    ) -> list[dict]:
        """Find drop-base-rally demand zones.

        A demand zone is a consolidation (base) after a drop, followed by a
        rally that breaks above the base high.

        Returns a list of dicts with keys:
        top, bottom, strength, touches, age, fresh, width.
        """
        if len(df) < lookback:
            lookback = len(df)

        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        n = len(df)
        start = max(0, n - lookback)

        zones: list[dict] = []

        i = start + 1
        while i < n - 2:
            # ── Step 1: find a bearish (drop) candle ──
            drop_len = 0
            drop_start = i
            while i < n - 1 and closes[i] < opens[i]:
                drop_len += 1
                i += 1

            if drop_len == 0:
                i += 1
                continue

            # ── Step 2: find a base (small-body) section ──
            base_start = i
            base_high = -np.inf
            base_low = np.inf
            base_bars = 0
            while i < n - 1:
                body = abs(closes[i] - opens[i])
                rng = highs[i] - lows[i]
                if rng == 0 or body / rng < 0.5:
                    base_high = max(base_high, highs[i])
                    base_low = min(base_low, lows[i])
                    base_bars += 1
                    i += 1
                else:
                    break

            if base_bars == 0:
                continue

            # ── Step 3: find a rally after the base ──
            rally_start = i
            rally_high_idx = -1
            while i < n:
                if closes[i] > opens[i] and highs[i] > base_high:
                    rally_high_idx = i
                    break
                i += 1

            if rally_high_idx == -1:
                continue

            # ── Count touches ──
            zone_top = base_high
            zone_bot = base_low
            touches = 0
            for j in range(rally_start, n):
                if lows[j] <= zone_top and lows[j] >= zone_bot:
                    touches += 1

            age = n - rally_high_idx
            fresh = touches <= 1
            width = zone_top - zone_bot

            zones.append({
                "top": float(zone_top),
                "bottom": float(zone_bot),
                "strength": 0.0,  # computed later by zone_strength
                "touches": touches,
                "age": age,
                "fresh": fresh,
                "width": float(width),
            })

        return zones

    @staticmethod
    def detect_supply_zones(
        df: pd.DataFrame,
        lookback: int = 100,
    ) -> list[dict]:
        """Find rally-base-drop supply zones.

        A supply zone is a consolidation (base) after a rally, followed by a
        drop that breaks below the base low.
        """
        if len(df) < lookback:
            lookback = len(df)

        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        n = len(df)
        start = max(0, n - lookback)

        zones: list[dict] = []

        i = start + 1
        while i < n - 2:
            # ── Step 1: bullish (rally) candle ──
            rally_len = 0
            while i < n - 1 and closes[i] > opens[i]:
                rally_len += 1
                i += 1

            if rally_len == 0:
                i += 1
                continue

            # ── Step 2: base section ──
            base_high = -np.inf
            base_low = np.inf
            base_bars = 0
            while i < n - 1:
                body = abs(closes[i] - opens[i])
                rng = highs[i] - lows[i]
                if rng == 0 or body / rng < 0.5:
                    base_high = max(base_high, highs[i])
                    base_low = min(base_low, lows[i])
                    base_bars += 1
                    i += 1
                else:
                    break

            if base_bars == 0:
                continue

            # ── Step 3: drop after base ──
            drop_start = i
            drop_low_idx = -1
            while i < n:
                if closes[i] < opens[i] and lows[i] < base_low:
                    drop_low_idx = i
                    break
                i += 1

            if drop_low_idx == -1:
                continue

            zone_top = base_high
            zone_bot = base_low
            touches = 0
            for j in range(drop_start, n):
                if highs[j] >= zone_bot and highs[j] <= zone_top:
                    touches += 1

            age = n - drop_low_idx
            fresh = touches <= 1
            width = zone_top - zone_bot

            zones.append({
                "top": float(zone_top),
                "bottom": float(zone_bot),
                "strength": 0.0,
                "touches": touches,
                "age": age,
                "fresh": fresh,
                "width": float(width),
            })

        return zones

    # ── Zone Strength Scoring ────────────────────────────────────────────

    def zone_strength(
        self,
        zone: dict,
        current_price: float,
        atr: float = 1.0,
    ) -> float:
        """Score a zone from 1 to 100.

        Factors:
        - Touches (more → stronger, max at 5)
        - Freshness (untouched → stronger)
        - Width (narrower → stronger)
        - Age (recent → stronger)
        - Distance from current price (closer → stronger)
        """
        # Touches: 0 → 10, 5+ → 50
        touches = min(zone["touches"], 5)
        touch_score = 10 + touches * 8  # 10..50

        # Freshness: untouched → +25, each touch −5
        fresh_score = 25 if zone["fresh"] else max(0, 25 - zone["touches"] * 5)

        # Width: narrow zones score higher. Normalise by ATR.
        width_ratio = zone["width"] / atr if atr > 0 else 1.0
        width_score = max(0, 20 - width_ratio * 10)  # narrower → higher

        # Age: recent zones score higher. 1 bar → 15, 50 bars → ~0
        age_score = max(0, 15 - zone["age"] * 0.3)

        # Distance: closer → stronger. Use ATR-normalised distance.
        mid = (zone["top"] + zone["bottom"]) / 2
        dist = abs(current_price - mid) / atr if atr > 0 else 0
        dist_score = max(0, 20 - dist * 2)

        raw = touch_score + fresh_score + width_score + age_score + dist_score
        return float(np.clip(raw, 1, 100))

    # ── Feature Integration ──────────────────────────────────────────────

    def compute_sd_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute supply/demand features for the RL agent.

        Returns a DataFrame indexed like ``df`` with columns:
        - nearest_demand_dist: ATR-normalised distance to nearest demand zone
        - nearest_supply_dist: ATR-normalised distance to nearest supply zone
        - demand_zone_strength: strength of nearest demand zone (0–1)
        - supply_zone_strength: strength of nearest supply zone (0–1)
        - in_demand_zone: 1 if close inside a demand zone
        - in_supply_zone: 1 if close inside a supply zone
        """
        n = len(df)
        demand_zones = self.detect_demand_zones(df)
        supply_zones = self.detect_supply_zones(df)

        # Compute ATR(14) for normalisation
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values

        tr = np.maximum(
            highs - lows,
            np.maximum(
                np.abs(highs - np.roll(closes, 1)),
                np.abs(lows - np.roll(closes, 1)),
            ),
        )
        tr[0] = highs[0] - lows[0]
        atr = pd.Series(tr).rolling(14, min_periods=1).mean().values

        # Pre-score all zones with final ATR (conservative: uses bar's own ATR)
        for z in demand_zones + supply_zones:
            # approximate ATR at zone formation (use overall mean)
            z["strength"] = self.zone_strength(z, 0.0, atr.mean()) / 100.0

        nearest_demand_dist = np.full(n, np.nan, dtype=np.float32)
        nearest_supply_dist = np.full(n, np.nan, dtype=np.float32)
        demand_zone_strength = np.zeros(n, dtype=np.float32)
        supply_zone_strength = np.zeros(n, dtype=np.float32)
        in_demand = np.zeros(n, dtype=np.int8)
        in_supply = np.zeros(n, dtype=np.int8)

        for bar in range(n):
            price = closes[bar]
            bar_atr = atr[bar] if atr[bar] > 0 else 1e-8

            # ── Demand zones ──
            best_d_dist = np.inf
            best_d_str = 0.0
            for z in demand_zones:
                if z["bottom"] <= price <= z["top"]:
                    in_demand[bar] = 1
                    d = 0.0
                else:
                    d = min(abs(price - z["top"]), abs(price - z["bottom"]))
                if d < best_d_dist:
                    best_d_dist = d
                    best_d_str = z["strength"]

            nearest_demand_dist[bar] = best_d_dist / bar_atr if best_d_dist < np.inf else np.nan
            demand_zone_strength[bar] = best_d_str

            # ── Supply zones ──
            best_s_dist = np.inf
            best_s_str = 0.0
            for z in supply_zones:
                if z["bottom"] <= price <= z["top"]:
                    in_supply[bar] = 1
                    d = 0.0
                else:
                    d = min(abs(price - z["top"]), abs(price - z["bottom"]))
                if d < best_s_dist:
                    best_s_dist = d
                    best_s_str = z["strength"]

            nearest_supply_dist[bar] = best_s_dist / bar_atr if best_s_dist < np.inf else np.nan
            supply_zone_strength[bar] = best_s_str

        out = pd.DataFrame(index=df.index)
        out["nearest_demand_dist"] = nearest_demand_dist
        out["nearest_supply_dist"] = nearest_supply_dist
        out["demand_zone_strength"] = demand_zone_strength
        out["supply_zone_strength"] = supply_zone_strength
        out["in_demand_zone"] = in_demand
        out["in_supply_zone"] = in_supply
        return out

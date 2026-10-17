"""Price-action patterns from measurable candle properties.

Every detection stores its properties (body, wicks, ratios, volume/ATR
context) so ML can learn which *variants* matter, rather than consuming
opaque names.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ml_engine.config import PriceActionConfig


@dataclass(frozen=True)
class CandleProps:
    index: int
    body: float
    range_: float
    upper_wick: float
    lower_wick: float
    body_to_range: float
    close_position: float      # (close - low) / range; 1 = closed at high
    bull: bool
    volume_ratio: float         # volume / rolling avg volume
    atr_ratio: float            # range / ATR
    prev_range: float
    prev_body: float


@dataclass(frozen=True)
class PatternEvent:
    pattern: str                # bullish_engulfing | bearish_engulfing | pin_bar ...
    direction: str               # bullish | bearish
    index: int
    price: float
    props: dict = field(default_factory=dict)


def candle_props(
    df: pd.DataFrame, config: PriceActionConfig | None = None
) -> list[CandleProps]:
    """Measurable properties for every bar (all trailing/causal)."""
    cfg = config or PriceActionConfig()
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    n = len(df)

    body = np.abs(c - o)
    rng = h - l
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    with np.errstate(divide="ignore", invalid="ignore"):
        body_to_range = np.where(rng > 0, body / rng, 0.0)
        close_pos = np.where(rng > 0, (c - l) / rng, 0.5)

    vol_avg = pd.Series(v).rolling(cfg.atr_period * 2, min_periods=1).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        volume_ratio = np.where(vol_avg > 0, v / vol_avg, 1.0)

    # Wilder ATR for context
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))
    atr = pd.Series(tr).ewm(alpha=1.0 / cfg.atr_period, adjust=False, min_periods=1).mean().to_numpy()

    prev_range = np.concatenate([[rng[0]], rng[:-1]])
    prev_body = np.concatenate([[body[0]], body[:-1]])

    out: list[CandleProps] = []
    for i in range(n):
        out.append(
            CandleProps(
                index=i,
                body=float(body[i]),
                range_=float(rng[i]),
                upper_wick=float(upper[i]),
                lower_wick=float(lower[i]),
                body_to_range=float(body_to_range[i]),
                close_position=float(close_pos[i]),
                bull=bool(c[i] > o[i]),
                volume_ratio=float(volume_ratio[i]),
                atr_ratio=float(rng[i] / atr[i]) if atr[i] > 0 else 0.0,
                prev_range=float(prev_range[i]),
                prev_body=float(prev_body[i]),
            )
        )
    return out


def detect_price_action(
    df: pd.DataFrame, config: PriceActionConfig | None = None
) -> list[PatternEvent]:
    """Named patterns with full property payloads at their trigger bar."""
    cfg = config or PriceActionConfig()
    props = candle_props(df, cfg)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    events: list[PatternEvent] = []

    def base(i: int) -> dict:
        p = props[i]
        return {
            "body": p.body, "range": p.range_,
            "upper_wick": p.upper_wick, "lower_wick": p.lower_wick,
            "body_to_range": p.body_to_range, "close_position": p.close_position,
            "volume_ratio": p.volume_ratio, "atr_ratio": p.atr_ratio,
            "prev_range": p.prev_range, "prev_body": p.prev_body,
        }

    for i in range(1, len(df)):
        p = props[i]
        pp = props[i - 1]

        # Engulfing: body engulfs previous body, opposite colors, containment
        if (
            pp.body > 0
            and p.body >= cfg.engulf_body_mult * pp.body
            and p.body > cfg.engulf_body_range_frac * max(p.range_, 1e-12)
            and p.bull != pp.bull
        ):
            if p.bull and c[i] >= o[i - 1] and o[i] <= c[i - 1]:
                events.append(PatternEvent("bullish_engulfing", "bullish", i, float(c[i]), base(i)))
            elif not p.bull and c[i] <= o[i - 1] and o[i] >= c[i - 1]:
                events.append(PatternEvent("bearish_engulfing", "bearish", i, float(c[i]), base(i)))

        # Pin bar: small body, dominant wick >= 2x body and >= 50% of range
        if p.range_ > 0 and p.body <= cfg.pin_body_max_frac * p.range_:
            eps = 1e-12
            if (
                p.lower_wick >= cfg.pin_wick_body_mult * p.body + eps
                and p.lower_wick >= cfg.pin_wick_range_frac * p.range_
            ):
                events.append(PatternEvent("pin_bar", "bullish", i, float(c[i]), base(i)))
            elif (
                p.upper_wick >= cfg.pin_wick_body_mult * p.body + eps
                and p.upper_wick >= cfg.pin_wick_range_frac * p.range_
            ):
                events.append(PatternEvent("pin_bar", "bearish", i, float(c[i]), base(i)))

        # Inside bar (current range within previous range)
        if h[i] <= h[i - 1] and l[i] >= l[i - 1]:
            events.append(
                PatternEvent("inside_bar", "neutral", i, float(c[i]), base(i))
            )

        # Outside bar
        if h[i] > h[i - 1] and l[i] < l[i - 1]:
            events.append(
                PatternEvent("outside_bar", "neutral" if p.bull is None else ("bullish" if p.bull else "bearish"),
                             i, float(c[i]), base(i))
            )

        # Rejection candle: closes in opposite 25% of range after an
        # extended wick beyond 1 ATR (measurable "refusal")
        if p.atr_ratio >= 1.0:
            if p.upper_wick > 0.5 * p.range_ and p.close_position < 0.25:
                events.append(PatternEvent("rejection_candle", "bearish", i, float(c[i]), base(i)))
            elif p.lower_wick > 0.5 * p.range_ and p.close_position > 0.75:
                events.append(PatternEvent("rejection_candle", "bullish", i, float(c[i]), base(i)))

    return events


def events_at(events: list[PatternEvent], index: int, max_age: int = 1) -> list[PatternEvent]:
    """Patterns whose trigger bar is within ``max_age`` bars of ``index``."""
    return [e for e in events if 0 <= index - e.index <= max_age]

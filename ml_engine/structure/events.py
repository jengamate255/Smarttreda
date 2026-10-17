"""Structure event streams: HH/HL/LH/LL, BOS, CHoCH — all causal.

Event semantics (contract C3):
- HH/HL/LH/LL: a confirmed swing is labelled by comparing it to the previous
  confirmed swing of the same kind. The label becomes visible when the swing
  itself becomes visible (pivot + right).
- BOS (break of structure): the close of bar b breaks beyond the last
  *confirmed* swing high (bos_up) / low (bos_down) that was visible before b
  and not already broken. Emitted once per level.
- CHoCH (change of character): first break against the prevailing swing
  sequence — a bos_up while the last two confirmed swing highs are LH and
  swing lows are LL (i.e. bearish structure), and mirrored.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ml_engine.config import SwingConfig
from ml_engine.structure.swings import SwingEvent, swing_events


@dataclass(frozen=True)
class StructureEvent:
    kind: str
    index: int          # visibility bar
    ref_index: int      # referenced pivot/level bar
    price: float
    level: float
    time: pd.Timestamp


def _swing_labels(events: list[SwingEvent]) -> list[StructureEvent]:
    """HH/HL/LH/LL labels for confirmed swings, stamped at visibility."""
    out: list[StructureEvent] = []
    last_high: SwingEvent | None = None
    last_low: SwingEvent | None = None
    for e in events:
        if e.kind == "swing_high":
            if last_high is not None:
                kind = "hh" if e.price > last_high.price else "lh"
                out.append(
                    StructureEvent(
                        kind=kind, index=e.visible_index, ref_index=e.pivot_index,
                        price=e.price, level=last_high.price, time=e.time,
                    )
                )
            last_high = e
        else:
            if last_low is not None:
                kind = "hl" if e.price > last_low.price else "ll"
                out.append(
                    StructureEvent(
                        kind=kind, index=e.visible_index, ref_index=e.pivot_index,
                        price=e.price, level=last_low.price, time=e.time,
                    )
                )
            last_low = e
    return out


def structure_events(
    df: pd.DataFrame, config: SwingConfig | None = None
) -> list[StructureEvent]:
    """Full causal event stream: swing labels + BOS + CHoCH, time-ordered."""
    cfg = config or SwingConfig()
    swings = swing_events(df, cfg)
    events: list[StructureEvent] = list(_swing_labels(swings))
    events.extend(_bos_choch(df, swings))
    events.sort(key=lambda e: (e.index, e.ref_index, e.kind))
    return events


def _bos_choch(
    df: pd.DataFrame, swings: list[SwingEvent]
) -> list[StructureEvent]:
    closes = df["close"].to_numpy(dtype=float)
    times = df["time"].to_numpy()
    out: list[StructureEvent] = []

    # Visible-swing queues by kind
    highs = [e for e in swings if e.kind == "swing_high"]
    lows = [e for e in swings if e.kind == "swing_low"]

    broken_highs: set[int] = set()
    broken_lows: set[int] = set()

    hi_pos = 0
    lo_pos = 0
    # Track the current structure bias from swing labels:
    bias = 0  # +1 bullish sequence (HH+HL), -1 bearish (LH+LL), 0 unknown

    last_high_label = None  # ("hh"|"lh", SwingEvent)
    last_low_label = None

    n = len(df)
    for b in range(n):
        # Advance visible swing queues
        while hi_pos < len(highs) and highs[hi_pos].visible_index <= b:
            hi_pos += 1
        while lo_pos < len(lows) and lows[lo_pos].visible_index <= b:
            lo_pos += 1

        c = closes[b]
        # BOS up: close breaks the most recent visible, unbroken swing high
        for e in reversed(highs[:hi_pos]):
            if e.pivot_index in broken_highs:
                continue
            if c > e.price:
                broken_highs.add(e.pivot_index)
                out.append(
                    StructureEvent(
                        kind="bos_up", index=b, ref_index=e.pivot_index,
                        price=float(c), level=e.price,
                        time=pd.Timestamp(times[b]),
                    )
                )
                if last_high_label is not None and last_high_label[0] == "lh" and bias < 0:
                    out.append(
                        StructureEvent(
                            kind="choch_up", index=b, ref_index=e.pivot_index,
                            price=float(c), level=e.price,
                            time=pd.Timestamp(times[b]),
                        )
                    )
                bias = 1
            break  # only the most recent unbroken high per bar

        # BOS down
        for e in reversed(lows[:lo_pos]):
            if e.pivot_index in broken_lows:
                continue
            if c < e.price:
                broken_lows.add(e.pivot_index)
                out.append(
                    StructureEvent(
                        kind="bos_down", index=b, ref_index=e.pivot_index,
                        price=float(c), level=e.price,
                        time=pd.Timestamp(times[b]),
                    )
                )
                if last_low_label is not None and last_low_label[0] == "hl" and bias > 0:
                    out.append(
                        StructureEvent(
                            kind="choch_down", index=b, ref_index=e.pivot_index,
                            price=float(c), level=e.price,
                            time=pd.Timestamp(times[b]),
                        )
                    )
                bias = -1
            break

        # Update swing labels visible at this bar (for future bars' CHoCH)
        for e in swings:
            if e.visible_index == b:
                if e.kind == "swing_high":
                    if last_high_label is None:
                        last_high_label = ("hh", e)
                    else:
                        last_high_label = (
                            "hh" if e.price > last_high_label[1].price else "lh",
                            e,
                        )
                else:
                    if last_low_label is None:
                        last_low_label = ("hl", e)
                    else:
                        last_low_label = (
                            "hl" if e.price > last_low_label[1].price else "ll",
                            e,
                        )
    return out


def structure_state_at(
    events: list[StructureEvent], index: int
) -> dict[str, float]:
    """Causal structure snapshot for features: counts of labels and breaks
    visible up to ``index``, plus bias and last confirmed levels."""
    hh = hl = lh = ll = bos_up = bos_down = choch_up = choch_down = 0
    last_swing_high = None
    last_swing_low = None
    for e in events:
        if e.index > index:
            break
        if e.kind == "hh":
            hh += 1
            last_swing_high = e.level
        elif e.kind == "hl":
            hl += 1
            last_swing_low = e.level
        elif e.kind == "lh":
            lh += 1
            last_swing_high = e.level
        elif e.kind == "ll":
            ll += 1
            last_swing_low = e.level
        elif e.kind == "bos_up":
            bos_up += 1
        elif e.kind == "bos_down":
            bos_down += 1
        elif e.kind == "choch_up":
            choch_up += 1
        elif e.kind == "choch_down":
            choch_down += 1
    return {
        "hh": float(hh),
        "hl": float(hl),
        "lh": float(lh),
        "ll": float(ll),
        "bos_up": float(bos_up),
        "bos_down": float(bos_down),
        "choch_up": float(choch_up),
        "choch_down": float(choch_down),
        "last_swing_high": last_swing_high or float("nan"),
        "last_swing_low": last_swing_low or float("nan"),
    }

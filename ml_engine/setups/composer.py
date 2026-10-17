"""Setup composer: confluence of causal observations -> Setup objects.

A Setup is the fundamental ML unit (contract C5). Composition rule
(configurable): at the trigger bar t on the base timeframe,
- structure alignment: base-TF structure bias agrees with direction
  (BOS/CHoCH sequence or EMA trend), AND
- a trigger: price revisits a fresh/strong zone of the right type, OR a
  liquidity sweep in the right direction occurred recently, AND
- price-action confirmation within `confirmation_max_age_bars`.

Every setup records entry/stop/target so labels and risk are identical
across ML, backtest, and risk modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ml_engine.config import EngineConfig
from ml_engine.patterns.liquidity import detect_liquidity
from ml_engine.patterns.price_action import PatternEvent, detect_price_action
from ml_engine.patterns.zones import Zone, detect_zone_candidates, zone_strength
from ml_engine.structure.events import StructureEvent, structure_events


@dataclass
class Setup:
    setup_id: str
    symbol: str
    time: pd.Timestamp
    index: int
    direction: str          # "long" | "short"
    entry: float
    stop: float
    target: float
    risk_reward: float
    timeframes: list[str] = field(default_factory=list)
    structure_context: dict = field(default_factory=dict)
    zone_context: dict = field(default_factory=dict)
    liquidity_context: dict = field(default_factory=dict)
    price_action_context: dict = field(default_factory=dict)
    location_context: dict = field(default_factory=dict)
    market_regime: str = ""
    vol_regime: str = ""
    htf_bias: str = ""
    definition: str = ""    # name of the SetupDefinition that produced it
    feature_vector: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SetupDefinition:
    """Configurable setup recipe (prompt section 11: no hard-coded combos)."""

    name: str
    direction: str          # "long" | "short"
    trigger: str            # "zone" | "sweep" | "location" | "any"
    location_kinds: tuple[str, ...] = ()
    require_htf_alignment: bool = True
    require_bos: bool = False            # structure confirmation
    require_choch: bool = False
    confirmation_patterns: tuple[str, ...] = ()   # empty = any bullish/bearish
    stop_mode: str = "zone"              # "zone" | "sweep" | "location"
    min_rr: float = 1.5
    default_rr: float = 2.0
    zone_max_dist_atr: float = 1.5
    sweep_max_age_bars: int = 10
    confirmation_max_age_bars: int = 3


DEFAULT_DEFINITIONS: tuple[SetupDefinition, ...] = (
    SetupDefinition(
        name="demand_retest_long",
        direction="long",
        trigger="zone",
        require_htf_alignment=True,
        confirmation_patterns=("bullish_engulfing", "pin_bar", "rejection_candle"),
        stop_mode="zone",
    ),
    SetupDefinition(
        name="supply_retest_short",
        direction="short",
        trigger="zone",
        require_htf_alignment=True,
        confirmation_patterns=("bearish_engulfing", "pin_bar", "rejection_candle"),
        stop_mode="zone",
    ),
    SetupDefinition(
        name="sweep_reversal_long",
        direction="long",
        trigger="sweep",
        require_htf_alignment=True,
        require_choch=False,
        confirmation_patterns=("pin_bar", "bullish_engulfing", "outside_bar", "rejection_candle"),
        stop_mode="sweep",
    ),
    SetupDefinition(
        name="sweep_reversal_short",
        direction="short",
        trigger="sweep",
        require_htf_alignment=True,
        confirmation_patterns=("pin_bar", "bearish_engulfing", "outside_bar", "rejection_candle"),
        stop_mode="sweep",
    ),
    SetupDefinition(
        name="location_reaction_long",
        direction="long",
        trigger="location",
        location_kinds=("support", "pdl", "session_low", "swing_low", "consolidation"),
        confirmation_patterns=("bullish_engulfing", "pin_bar", "rejection_candle"),
        stop_mode="location",
    ),
    SetupDefinition(
        name="location_reaction_short",
        direction="short",
        trigger="location",
        location_kinds=("resistance", "pdh", "session_high", "swing_high", "consolidation"),
        confirmation_patterns=("bearish_engulfing", "pin_bar", "rejection_candle"),
        stop_mode="location",
    ),
)


def _structure_bias(events: list[StructureEvent], index: int) -> float:
    """+1 bullish, -1 bearish, 0 neutral — from events visible at index."""
    hh = hl = lh = ll = bos_up = bos_down = choch_up = choch_down = 0
    for e in events:
        if e.index > index:
            break
        if e.kind == "hh":
            hh += 1
        elif e.kind == "hl":
            hl += 1
        elif e.kind == "lh":
            lh += 1
        elif e.kind == "ll":
            ll += 1
        elif e.kind == "bos_up":
            bos_up += 1
        elif e.kind == "bos_down":
            bos_down += 1
        elif e.kind == "choch_up":
            choch_up += 1
        elif e.kind == "choch_down":
            choch_down += 1
    bull = (hh - lh) + (hl - ll) + 2 * (bos_up - bos_down) + 3 * (choch_up - choch_down)
    if bull > 1:
        return 1.0
    if bull < -1:
        return -1.0
    return 0.0


def compose_setups(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    cfg: EngineConfig,
    htf_bias: pd.Series | None = None,
    definitions: tuple[SetupDefinition, ...] | None = None,
) -> list[Setup]:
    """Scan the base frame and emit causally-valid setups.

    Definition-driven (contract C5): each SetupDefinition declares its
    trigger, location kinds, confirmation patterns, stop mode and RR floor.
    Every emitted setup records its full context (structure / zone /
    liquidity / price action / location / regime / htf bias) so the decision
    engine can explain why it fired.
    """
    n = len(df)
    if n < 100:
        return []

    defs = definitions or DEFAULT_DEFINITIONS
    events = structure_events(df, cfg.swing)
    zones = detect_zone_candidates(df, timeframe, cfg.zone)
    patterns = detect_price_action(df, cfg.price_action)
    liq_events = detect_liquidity(df, cfg.liquidity, cfg.swing)
    from ml_engine.locations import detect_locations, distance_to, nearest_location_at

    locations = detect_locations(df, timeframe, cfg)
    from ml_engine.features.regime import classify_regimes

    regime_frame = classify_regimes(df, cfg.regime)

    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    t = pd.to_datetime(df["time"])

    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))
    atr = (
        pd.Series(tr)
        .ewm(alpha=1.0 / cfg.feature.atr_period, adjust=False, min_periods=1)
        .mean()
        .to_numpy()
    )

    # Precompute per-zone causal state timelines once (O(zones * n)) so the
    # per-bar composition loop stays linear.
    zone_states: list[dict] = []
    for z in zones:
        touches = np.zeros(n, dtype=np.int32)
        broken_at = n
        count = 0
        for b in range(z.created_index + 1, n):
            if h[b] >= z.lower and l[b] <= z.upper:
                count += 1
            touches[b] = count
        for b in range(z.created_index + 1, n):
            a = atr[b] if np.isfinite(atr[b]) else 0.0
            if a <= 0:
                continue
            if z.type == "demand" and c[b] < z.lower - cfg.zone.breach_atr * a:
                broken_at = b
                break
            if z.type == "supply" and c[b] > z.upper + cfg.zone.breach_atr * a:
                broken_at = b
                break
        zone_states.append({"z": z, "touches": touches, "broken_at": broken_at})

    patterns_by_bar: dict[int, list[PatternEvent]] = {}
    for e in patterns:
        patterns_by_bar.setdefault(e.index, []).append(e)

    # Bos/choch recency per bar (structure confirmation)
    bos_at_bar: dict[int, str] = {}
    choch_at_bar: dict[int, str] = {}
    for e in events:
        if e.kind == "bos_up":
            bos_at_bar[e.index] = "up"
        elif e.kind == "bos_down":
            bos_at_bar[e.index] = "down"
        elif e.kind == "choch_up":
            choch_at_bar[e.index] = "up"
        elif e.kind == "choch_down":
            choch_at_bar[e.index] = "down"

    setups: list[Setup] = []

    for i in range(max(60, 2), n):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        bias = _structure_bias(events, i)
        htf_b = float(htf_bias.iloc[i]) if htf_bias is not None and np.isfinite(htf_bias.iloc[i]) else 0.0
        regime = str(regime_frame["composite_regime"].iloc[i])
        vol = str(regime_frame["vol_regime"].iloc[i])

        for d in defs:
            direction = d.direction
            want_bias = 1.0 if direction == "long" else -1.0
            # HTF alignment gate (context, veto only)
            if d.require_htf_alignment and htf_b != 0 and htf_b != want_bias:
                continue

            # ---- trigger resolution ------------------------------------
            zone_ctx: Zone | None = None
            zone_dist = None
            sweep = None
            loc = None
            loc_dist = None

            if d.trigger in ("zone", "any"):
                for st in zone_states:
                    z = st["z"]
                    if z.created_index > i or z.type != ("demand" if direction == "long" else "supply"):
                        continue
                    if st["broken_at"] <= i:
                        continue
                    touches_i = int(st["touches"][i])
                    zi = Zone(
                        zone_id=z.zone_id, type=z.type, tf=z.tf,
                        created_index=z.created_index, created_time=z.created_time,
                        upper=z.upper, lower=z.lower, width=z.width,
                        width_atr=z.width_atr, origin=z.origin,
                        departure_strength_atr=z.departure_strength_atr,
                        departure_velocity=z.departure_velocity,
                        volume_context=z.volume_context,
                        last_event_index=z.last_event_index,
                    )
                    zi.status = "tested" if touches_i > 0 else "fresh"
                    zi.retests = touches_i
                    zi.strength = zone_strength(zi)
                    if zi.strength < 40:
                        continue
                    dist = min(abs(c[i] - zi.lower), abs(c[i] - zi.upper))
                    if dist > d.zone_max_dist_atr * a:
                        continue
                    if zone_ctx is None or dist < zone_dist:
                        zone_ctx, zone_dist = zi, dist

            if d.trigger in ("sweep", "any"):
                for e in liq_events:
                    if e.index > i:
                        break
                    if (
                        e.kind == ("sweep_low" if direction == "long" else "sweep_high")
                        and 0 <= i - e.index <= d.sweep_max_age_bars
                        and (sweep is None or i - e.index < i - sweep.index)
                    ):
                        sweep = e

            if d.trigger == "location" or d.location_kinds:
                for kind in d.location_kinds:
                    nearest = nearest_location_at(locations, i, c[i], kind=kind)
                    if nearest is None or nearest.broken:
                        continue
                    dist = distance_to(nearest, c[i]) / a
                    if dist > d.zone_max_dist_atr:
                        continue
                    if loc is None or dist < loc_dist:
                        loc, loc_dist = nearest, dist

            triggered = (
                (zone_ctx is not None and d.trigger in ("zone", "any"))
                or (sweep is not None and d.trigger in ("sweep", "any"))
                or (loc is not None and d.trigger == "location")
            )
            if not triggered:
                continue

            # ---- structure confirmation (BOS / CHoCH) --------------------
            if d.require_bos or d.require_choch:
                ok = False
                side = "up" if direction == "long" else "down"
                for k in range(max(0, i - d.confirmation_max_age_bars), i + 1):
                    if d.require_bos and bos_at_bar.get(k) == side:
                        ok = True
                    if d.require_choch and choch_at_bar.get(k) == side:
                        ok = True
                if not ok:
                    continue

            # ---- price-action confirmation --------------------------------
            want = "bullish" if direction == "long" else "bearish"
            confirm = None
            for k in range(max(0, i - d.confirmation_max_age_bars), i + 1):
                for e in patterns_by_bar.get(k, []):
                    if e.pattern == "inside_bar":
                        continue
                    if d.confirmation_patterns and e.pattern not in d.confirmation_patterns:
                        continue
                    if e.direction in (want, "neutral"):
                        confirm = e
                        break
                if confirm is not None:
                    break
            if confirm is None:
                continue

            # ---- trade geometry ------------------------------------------
            if direction == "long":
                if d.stop_mode == "zone" and zone_ctx is not None:
                    entry = c[i]
                    stop = zone_ctx.lower - cfg.setup.stop_atr_buffer * a
                elif d.stop_mode == "sweep" and sweep is not None:
                    entry = c[i]
                    stop = min(l[i], sweep.reference_level) - cfg.setup.stop_atr_buffer * a
                elif loc is not None:
                    entry = c[i]
                    stop = min(l[i], loc.price_low) - cfg.setup.stop_atr_buffer * a
                else:
                    continue
                risk = entry - stop
                if risk <= 0:
                    continue
                target = entry + risk * d.default_rr
            else:
                if d.stop_mode == "zone" and zone_ctx is not None:
                    entry = c[i]
                    stop = zone_ctx.upper + cfg.setup.stop_atr_buffer * a
                elif d.stop_mode == "sweep" and sweep is not None:
                    entry = c[i]
                    stop = max(h[i], sweep.reference_level) + cfg.setup.stop_atr_buffer * a
                elif loc is not None:
                    entry = c[i]
                    stop = max(h[i], loc.price_high) + cfg.setup.stop_atr_buffer * a
                else:
                    continue
                risk = stop - entry
                if risk <= 0:
                    continue
                target = entry - risk * d.default_rr

            rr = abs(target - entry) / risk
            if rr < d.min_rr:
                continue

            setups.append(
                Setup(
                    setup_id=f"{symbol}-{timeframe}-{d.name}-{i}",
                    symbol=symbol,
                    time=pd.Timestamp(t.iloc[i]),
                    index=i,
                    direction=direction,
                    entry=float(entry),
                    stop=float(stop),
                    target=float(target),
                    risk_reward=float(rr),
                    timeframes=[timeframe],
                    structure_context={"bias": bias, "htf_bias": htf_b},
                    zone_context=(
                        {
                            "zone_id": zone_ctx.zone_id,
                            "strength": zone_ctx.strength,
                            "status": zone_ctx.status,
                            "width_atr": zone_ctx.width_atr,
                            "departure_strength_atr": zone_ctx.departure_strength_atr,
                            "volume_context": zone_ctx.volume_context,
                        }
                        if zone_ctx is not None
                        else {}
                    ),
                    liquidity_context=(
                        {
                            "kind": sweep.kind,
                            "magnitude_atr": sweep.magnitude_atr,
                            "age_bars": i - sweep.index,
                            "reference_level": sweep.reference_level,
                        }
                        if sweep is not None
                        else {}
                    ),
                    price_action_context=(
                        {
                            "pattern": confirm.pattern,
                            "direction": confirm.direction,
                            "props": confirm.props,
                        }
                    ),
                    location_context=(
                        {
                            "kind": loc.kind,
                            "price_high": loc.price_high,
                            "price_low": loc.price_low,
                            "strength": loc.strength,
                            "retests": loc.retests,
                            "dist_atr": loc_dist,
                        }
                        if loc is not None
                        else {}
                    ),
                    market_regime=regime,
                    vol_regime=vol,
                    htf_bias="bullish" if htf_b > 0 else ("bearish" if htf_b < 0 else "neutral"),
                    definition=d.name,
                )
            )
    return setups

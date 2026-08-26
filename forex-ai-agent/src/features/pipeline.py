"""Feature pipeline: assembles all feature families plus multi-timeframe context.

MTF alignment (anti-leakage): HTF features are computed on the HTF frame with
the same causal indicators, then merged onto base bars with merge_asof using
direction="backward" — an HTF bar's features are only visible to base bars with
timestamp >= that HTF bar's open time. This prevents future information from
higher timeframes leaking into base-timeframe rows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.features.price_action import build_price_action
from src.features.sessions import session_features
from src.features.structure import build_structure
from src.features.supply_demand import build_supply_demand
from src.features.technical import build_technical

DEFAULT_DROP = [
    "open", "high", "low", "close", "tick_volume", "real_volume", "spread",
    "swing_high", "swing_low", "swing_high_confirmed", "swing_low_confirmed",
    "hour", "day_of_week",
]

TF_SECONDS = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400}


def build_features(
    df: pd.DataFrame,
    cfg: dict | None = None,
    htf_frames: dict[str, pd.DataFrame] | None = None,
    base_timeframe: str = "M15",
) -> pd.DataFrame:
    """Build the full causal feature matrix.

    htf_frames: {timeframe: candle_frame} with columns time/open/high/low/close.
    """
    cfg = cfg or {}
    base = df.copy()
    base["time"] = pd.to_datetime(base["time"], utc=True)
    base = base.sort_values("time").reset_index(drop=True)

    frames: list[pd.DataFrame] = [base.reset_index(drop=True)]
    frames.append(build_technical(base, cfg.get("features", {})))
    frames.append(build_price_action(base))
    frames.append(build_structure(base, int(cfg.get("features", {}).get("swing_window", 3))))
    frames.append(session_features(base, cfg.get("sessions", {})))

    sd_cfg = dict(cfg.get("supply_demand", {}))
    sd_cfg["swing_window"] = cfg.get("features", {}).get("swing_window", 3)
    frames.append(build_supply_demand(base, sd_cfg))

    if htf_frames:
        frames.append(_htf_context(base, htf_frames, cfg, base_timeframe))

    out = pd.concat(frames, axis=1)
    out = out.loc[:, ~out.columns.duplicated()]
    return out


def _htf_context(
    base: pd.DataFrame,
    htf_frames: dict[str, pd.DataFrame],
    cfg: dict,
    base_timeframe: str,
) -> pd.DataFrame:
    htf_cfg = cfg.get("features", {}).get("htf_context", {}).get(base_timeframe, {})
    ctx = pd.DataFrame(index=base.index)
    base_time = base[["time"]].sort_values("time")

    for htf_name, frame in htf_frames.items():
        role = htf_cfg.get(htf_name, "context")
        htf = frame.copy()
        htf["time"] = pd.to_datetime(htf["time"], utc=True)
        htf = htf.sort_values("time")
        htf["time"] = htf["time"].dt.floor(f"{TF_SECONDS[htf_name]}s")

        htf_tech = build_technical(htf, cfg.get("features", {}))
        htf_full = pd.concat([htf.reset_index(drop=True), htf_tech], axis=1)
        keep = [c for c in htf_full.columns if c not in DEFAULT_DROP]
        htf_full = htf_full[keep].drop_duplicates(subset="time", keep="last")

        aligned = pd.merge_asof(
            base_time,
            htf_full,
            on="time",
            direction="backward",
            allow_exact_matches=True,
        )
        for col in keep:
            if col == "time":
                continue
            ctx[f"{htf_name.lower()}_{role}_{col}"] = aligned[col].to_numpy()
    return ctx


def attach_labels(
    features: pd.DataFrame,
    df: pd.DataFrame,
    cfg: dict | None = None,
    symbol: str = "EURUSD",
) -> pd.DataFrame:
    from src.labels.trade_outcome import simulate_labels

    labels = simulate_labels(df, cfg=cfg, symbol=symbol)
    out = features.copy()
    for col in labels.columns:
        out[col] = labels[col].to_numpy()
    return out

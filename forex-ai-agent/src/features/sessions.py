"""Session features (UTC based, per-bar)."""
from __future__ import annotations

import pandas as pd


def session_features(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or {}
    asian = cfg.get("asian", [0, 8])
    london = cfg.get("london", [7, 16])
    new_york = cfg.get("new_york", [12, 21])
    overlap_sessions = cfg.get("overlap_sessions", ["london", "new_york"])

    t = pd.to_datetime(df["time"], utc=True)
    hour = t.dt.hour
    dow = t.dt.dayofweek

    def in_session(h: pd.Series, bounds: list) -> pd.Series:
        return (h >= bounds[0]) & (h < bounds[1])

    out = pd.DataFrame(index=df.index)
    out["hour"] = hour
    out["day_of_week"] = dow
    out["is_weekend"] = (dow >= 5).astype(int)
    out["session_asian"] = in_session(hour, asian).astype(int)
    out["session_london"] = in_session(hour, london).astype(int)
    out["session_new_york"] = in_session(hour, new_york).astype(int)

    overlap = pd.Series(True, index=df.index)
    for s in overlap_sessions:
        if s == "asian":
            overlap &= in_session(hour, asian)
        elif s == "london":
            overlap &= in_session(hour, london)
        elif s == "new_york":
            overlap &= in_session(hour, new_york)
    out["session_overlap"] = overlap.astype(int)
    return out

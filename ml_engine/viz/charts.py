"""Chart rendering: candles + detections + trade plan overlays.

Used for conceptual validation of detectors (numerical tests cannot catch
every pattern-detection bug; eyeballing annotated charts can).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def plot_detections(
    df: pd.DataFrame,
    title: str,
    out_path: Path | str,
    swings: list | None = None,
    structure_events: list | None = None,
    zones: list | None = None,
    setups: list | None = None,
    last_n: int = 300,
) -> Path:
    """Render the last ``last_n`` bars with all detection overlays."""
    df = df.tail(last_n).reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(16, 8))

    for i, row in df.iterrows():
        color = "#26a69a" if row["close"] >= row["open"] else "#ef5350"
        ax.plot([i, i], [row["low"], row["high"]], color=color, lw=0.7, zorder=1)
        ax.plot(
            [i, i],
            [min(row["open"], row["close"]), max(row["open"], row["close"])],
            color=color, lw=3.0, zorder=2,
        )

    offset = len(df) - min(last_n, len(df))
    # Swings
    if swings:
        for e in swings:
            pos = e.pivot_index - offset
            if 0 <= pos < len(df):
                if e.kind == "swing_high":
                    ax.scatter(pos, df["high"].iloc[pos] * 1.001, marker="v", color="blue", s=30, zorder=3)
                else:
                    ax.scatter(pos, df["low"].iloc[pos] * 0.999, marker="^", color="purple", s=30, zorder=3)

    # Structure events
    if structure_events:
        for e in structure_events:
            pos = e.index - offset
            if not (0 <= pos < len(df)):
                continue
            y = df["close"].iloc[pos]
            if e.kind == "bos_up":
                ax.annotate("BOS↑", (pos, y), color="green", fontsize=8)
            elif e.kind == "bos_down":
                ax.annotate("BOS↓", (pos, y), color="red", fontsize=8)
            elif e.kind == "choch_up":
                ax.annotate("CHoCH↑", (pos, y * 1.001), color="darkgreen", fontsize=8)
            elif e.kind == "choch_down":
                ax.annotate("CHoCH↓", (pos, y * 0.999), color="darkred", fontsize=8)

    # Zones
    if zones:
        for z in zones:
            start = max(0, z.created_index - offset)
            end = len(df)
            color = "green" if z.type == "demand" else "red"
            ax.axhspan(z.lower, z.upper, xmin=start / len(df), xmax=end / len(df),
                       alpha=0.12, color=color, zorder=0)

    # Setups
    if setups:
        for s in setups:
            pos = s.index - offset
            if not (0 <= pos < len(df)):
                continue
            marker = "^" if s.direction == "long" else "v"
            color = "green" if s.direction == "long" else "red"
            ax.scatter(pos, s.entry, marker=marker, color=color, s=120, edgecolors="black", zorder=5)
            ax.plot([pos, pos], [s.entry, s.stop], color=color, lw=1.5, ls="--", zorder=4)
            ax.plot([pos, pos + 10], [s.entry, s.target], color=color, lw=1.5, zorder=4)

    step = max(1, len(df) // 12)
    ticks = list(range(0, len(df), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([str(df["time"].iloc[t])[:16] for t in ticks], rotation=30, fontsize=8)
    ax.set_title(title)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return Path(out_path)

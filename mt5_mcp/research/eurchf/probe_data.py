"""Probe EURCHF data availability + symbol info (read-only)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mt5_mcp.backtest import load_frames  # noqa: E402
from mt5_mcp.client import get_client  # noqa: E402
from mt5_mcp.common import pip_size, pip_value_per_lot  # noqa: E402
from mt5_mcp.indicators import atr  # noqa: E402


def main() -> None:
    client = get_client()
    client.ensure_connected()
    info = client.symbol_info("EURCHF")
    print("info keys:", sorted(info.keys())[:40])
    for k in ("digits", "point", "spread", "trade_tick_size", "trade_tick_value",
              "volume_min", "volume_max", "volume_step"):
        print(f"  {k} = {info.get(k)}")
    print("  pip_size =", pip_size(info))
    print("  pip_value_per_lot =", pip_value_per_lot(info))

    frames = load_frames("EURCHF", ["H1", "H4"], 6000)
    for tf, fr in frames.items():
        print(f"{tf}: {len(fr)} bars, {fr['time'].iloc[0]} .. {fr['time'].iloc[-1]}")
        print("  columns:", list(fr.columns))
    h1 = frames["H1"]
    a = atr(h1, 14)
    pip = pip_size(info)
    print(f"H1 ATR(14): last={a.iloc[-1]/pip:.1f} pips, "
          f"median={a.median()/pip:.1f} pips, mean={a.mean()/pip:.1f} pips")
    rng = (h1["high"] - h1["low"]) / pip
    print(f"H1 bar range: median={rng.median():.1f} pips")
    print("hours present:", sorted(set(h1["time"].dt.hour)))


if __name__ == "__main__":
    main()

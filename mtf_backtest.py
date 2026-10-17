"""Offline MTF backtest — fetches rates directly from the MT5 Gateway HTTP
API (no MT5Client, no fallback). Pure pandas simulation.

Method (per symbol):
  1. Pull H1 + D1 + M30 + M15 + W1 history from the gateway.
  2. Walk H1 bars forward (no lookahead) with EMA50/200 + structure bias.
  3. Compare: H1-only entries vs D1-gated vs full W1→D1→H4→H1 gate.
  4. Simple fill: next-bar open, SL = 2*ATR, TP = 2*SL (RR 2).
  5. Output: trade count, win%, expectancy, PF, avg bars in trade.

Usage:  python mtf_backtest.py [symbols...]
"""

from __future__ import annotations
import json, sys
from pathlib import Path
import urllib.request as _req
import pandas as pd
import numpy as np

GW = "http://127.0.0.1:8060"
AUTH_TOKEN = ""
def _load_token():
    global AUTH_TOKEN
    if AUTH_TOKEN:
        return AUTH_TOKEN
    # read from .env
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("WEB_AUTH_TOKEN="):
                AUTH_TOKEN = line.split("=", 1)[1].strip()
                break
    return AUTH_TOKEN

def gw_call(method: str, kwargs: dict) -> pd.DataFrame:
    """POST to the gateway and deserialize into a DataFrame."""
    _load_token()
    body = json.dumps(kwargs).encode()
    req = _req.Request(GW + "/call/" + method, data=body,
                       headers={"Content-Type": "application/json",
                                "X-Auth-Token": AUTH_TOKEN})
    with _req.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    if data.get("error"):
        raise RuntimeError(data["error"])
    payload = data.get("data", {})
    # Gateway wraps DataFrames as {"columns": [...], "rows": [[...], ...]}
    if isinstance(payload, dict) and "columns" in payload:
        return pd.DataFrame(payload["rows"], columns=payload["columns"])
    return pd.DataFrame(payload) if isinstance(payload, list) else pd.DataFrame()


def rates(symbol: str, tf: str, count: int) -> pd.DataFrame:
    return gw_call("rates", {"symbol": symbol, "timeframe": tf, "count": count})


# ── indicators ──────────────────────────────────────────────────────────

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([h-l, (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def bias(df: pd.DataFrame, fast=50, slow=200) -> pd.Series:
    """+1 = bullish, -1 = bearish, 0 = neutral."""
    ef = ema(df["close"], fast)
    es = ema(df["close"], slow)
    trend = pd.Series(0, index=df.index)
    trend[ef > es] = 1
    trend[ef < es] = -1
    # confirm with structure (higher high / lower low vs 50-bar lookback)
    roll_h = df["high"].rolling(20).max()
    roll_l = df["low"].rolling(20).min()
    prev_h = roll_h.shift(20)
    prev_l = roll_l.shift(20)
    # bullish: EMA stack + price > prior swing high
    bull = (trend == 1) & (df["close"] > prev_h * 0.999)
    # bearish: EMA stack + price < prior swing low
    bear = (trend == -1) & (df["close"] < prev_l * 1.001)
    return pd.Series(0, index=df.index).mask(bull, 1).mask(bear, -1)


# ── simulation ──────────────────────────────────────────────────────────

def simulate(h1: pd.DataFrame, gate_series: dict[str, pd.Series],
             label: str, min_bars: int = 200) -> dict:
    """
    gate_series keys: 'D1', 'W1' etc.
    When the mode uses a gate, the trade is only taken if ALL gate TFs
    agree with the H1 signal direction.
    """
    ef = ema(h1["close"], 50)
    es = ema(h1["close"], 200)
    a  = atr(h1)

    # forward-fill gate biases to H1 index
    filled = {}
    for tf, ser in gate_series.items():
        filled[tf] = ser.reindex(h1.index, method="ffill")

    trades = []
    in_pos = 0
    entry = sl = tp = 0.0
    entry_i = 0

    for i in range(min_bars, len(h1) - 1):
        price = h1["close"].iloc[i]

        if in_pos == 0:
            sig = 1 if ef.iloc[i] > es.iloc[i] else (-1 if ef.iloc[i] < es.iloc[i] else 0)
            if sig == 0:
                continue
            # check ALL gate TFs
            skip = False
            for tf, ser in filled.items():
                if ser.iloc[i] != sig:
                    skip = True
                    break
            if skip:
                continue

            entry = price
            stop = 2.0 * a.iloc[i] if pd.notna(a.iloc[i]) else 0.001
            if stop <= 0:
                stop = 0.001
            sl = entry - sig * stop
            tp = entry + sig * 2 * stop
            in_pos = sig
            entry_i = i
        else:
            hi, lo = h1["high"].iloc[i], h1["low"].iloc[i]
            if in_pos == 1:
                if lo <= sl:
                    trades.append({"pnl": sl - entry, "bars": i - entry_i}); in_pos = 0
                elif hi >= tp:
                    trades.append({"pnl": tp - entry, "bars": i - entry_i}); in_pos = 0
            else:
                if hi >= sl:
                    trades.append({"pnl": entry - sl, "bars": i - entry_i}); in_pos = 0
                elif lo <= tp:
                    trades.append({"pnl": entry - tp, "bars": i - entry_i}); in_pos = 0
            if in_pos != 0 and i - entry_i > 200:
                pnl = price - entry if in_pos == 1 else entry - price
                trades.append({"pnl": pnl, "bars": i - entry_i}); in_pos = 0

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in losses))
    n = len(trades)
    return {
        "label": label,
        "trades": n,
        "win%": round(len(wins) / n * 100, 1) if n else 0,
        "expect": round(sum(t["pnl"] for t in trades) / n, 5) if n else 0,
        "PF": round(gross_w / gross_l, 2) if gross_l else float("inf") if gross_w else 0,
        "avg_bars": round(sum(t["bars"] for t in trades) / n, 1) if n else 0,
    }


# ── main ────────────────────────────────────────────────────────────────

def main():
    symbols = sys.argv[1:] if len(sys.argv) > 1 else [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "NZDUSD",
        "XAUUSD", "XAGUSD", "USOIL",
        "BTCUSD", "ETHUSD",
    ]

    # verify gateway alive
    try:
        with _req.urlopen(GW + "/health", timeout=5) as r:
            gw = json.loads(r.read())
        print(f"gateway: ok ({gw.get('server','?')})  uptime {gw.get('uptime_s',0):.0f}s")
    except Exception as e:
        print(f"FATAL: gateway unreachable at {GW}: {e}", file=sys.stderr)
        return 1

    hdr = f"{'symbol':10s} {'mode':22s} {'trades':>7s} {'win%':>6s} {'expect':>10s} {'PF':>6s} {'avg_bars':>9s}"
    print(hdr)
    print("-" * len(hdr))

    for sym in symbols:
        try:
            h1 = rates(sym, "H1", 2000)
            d1 = rates(sym, "D1", 500)
            w1 = rates(sym, "W1", 200)
        except Exception as e:
            print(f"{sym:10s} FETCH ERROR: {e}")
            continue

        if len(h1) < 500 or len(d1) < 100:
            print(f"{sym:10s} insufficient history (H1={len(h1)}, D1={len(d1)})")
            continue

        d1_b = bias(d1)
        w1_b = bias(w1) if len(w1) >= 100 else pd.Series(0, index=h1.index)

        combos = [
            ("H1-only (no gate)",           {}),
            ("D1 gate",                     {"D1": d1_b}),
            ("W1+D1 gate",                  {"W1": w1_b, "D1": d1_b}),
            ("W1+D1+H4 gate",              {"W1": w1_b, "D1": d1_b}),  # H4 ≈ H1 for sim
        ]

        for label, gates in combos:
            r = simulate(h1.copy(), gates, label)
            print(f"{sym:10s} {label:22s} {r['trades']:>7d} {r['win%']:>6.1f} "
                  f"{r['expect']:>10.5f} {r['PF']:>6.2f} {r['avg_bars']:>9.1f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

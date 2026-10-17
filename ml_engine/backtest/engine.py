"""Event-driven backtester over labeled setups with a cost model.

Execution assumptions (explicit, pessimistic where ambiguous):
- Entry at the NEXT bar's open after the setup trigger.
- BUY fills at open + half_spread + slippage; SELL at open - half_spread -
  slippage (spread paid on both sides, conservative).
- Same-bar SL+TP ambiguity: SL fills first.
- Costs: spread (both sides), slippage (both sides), commission per lot RT.
- One position at a time per symbol (configurable), first-come-first-served.
- No future information: the setup row only carries features/labels that the
  pipeline already guarantees are causal; execution only walks forward.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ml_engine.config import BacktestConfig


@dataclass
class Fill:
    setup_id: str
    entry_index: int
    entry_time: pd.Timestamp
    direction: str
    entry_price: float
    stop: float
    target: float
    lots: float
    risk_percent: float


@dataclass
class ClosedTrade:
    setup_id: str
    direction: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    stop: float
    target: float
    lots: float
    pnl: float
    r_multiple: float
    exit_reason: str
    bars_held: int


def _pnl(direction: str, entry: float, exit_price: float, lots: float,
         pip_size: float, pip_value: float, commission_per_lot: float) -> float:
    move = (exit_price - entry) if direction == "long" else (entry - exit_price)
    pips = move / pip_size
    gross = pips * pip_value * lots
    commission = commission_per_lot * lots
    return float(gross - commission)


def backtest_setups(
    df: pd.DataFrame,
    setups: pd.DataFrame,
    probabilities: pd.Series | None = None,
    config: BacktestConfig | None = None,
    risk_policy=None,
    equity: float = 10_000.0,
) -> list[ClosedTrade]:
    """Run setups through bar-by-bar execution with costs.

    setups: DataFrame with setup_id, index, direction, entry, stop, target.
    probabilities: optional aligned probability series (risk sizing).
    """
    cfg = config or BacktestConfig()
    from ml_engine.risk.sizing import SizingPolicy, size_for_probability

    policy = risk_policy or SizingPolicy()
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    t = pd.to_datetime(df["time"])
    n = len(df)
    half_spread = cfg.spread_points * cfg.point / 2.0
    slip = cfg.slippage_points * cfg.point

    trades: list[ClosedTrade] = []
    busy_until = -1
    setups = setups.sort_values("index").reset_index(drop=True)

    for row_i, s in setups.iterrows():
        i = int(s["index"])
        if i + 1 >= n:
            continue
        if i <= busy_until:
            continue  # overlapping setups skipped (single position)

        p = (
            float(probabilities.iloc[row_i])
            if probabilities is not None
            else float("nan")
        )
        if probabilities is not None and p < cfg.min_probability:
            continue

        risk_pct = size_for_probability(p, policy)
        stop_dist = abs(float(s["entry"]) - float(s["stop"]))
        lots = max(0.01, min(10.0, round(
            (equity * risk_pct / 100.0) / (stop_dist / cfg.pip_size * cfg.pip_value_per_lot), 2
        )))
        # cost-adjusted entry at next open
        entry_bar = i + 1
        base = o[entry_bar]
        if s["direction"] == "long":
            entry = base + half_spread + slip
        else:
            entry = base - half_spread - slip

        stop = float(s["stop"])
        target = float(s["target"])
        direction = str(s["direction"])

        exit_price = None
        exit_bar = None
        reason = "timeout"
        # walk forward from the bar after entry
        max_bars = 200
        end = min(n, entry_bar + 1 + max_bars)
        for b in range(entry_bar, end):
            hi = h[b]
            lo = l[b]
            hit_sl = lo <= stop if direction == "long" else hi >= stop
            hit_tp = hi >= target if direction == "long" else lo >= target
            if hit_sl and hit_tp:
                exit_price, exit_bar, reason = stop, b, "sl_ambiguity_first"
                break
            if hit_sl:
                exit_price, exit_bar, reason = stop, b, "sl"
                break
            if hit_tp:
                exit_price, exit_bar, reason = target, b, "tp"
                break
        if exit_price is None:
            exit_bar = min(n - 1, entry_bar + max_bars)
            exit_price = base  # approximation: close of window
            # use actual close of exit bar
            exit_price = float(df["close"].to_numpy()[exit_bar])
            reason = "timeout"

        # Slippage on exit; spread paid on exit leg as well (conservative)
        if direction == "long":
            fill_exit = exit_price - half_spread - slip
        else:
            fill_exit = exit_price + half_spread + slip

        pnl = _pnl(
            direction, entry, fill_exit, lots,
            cfg.pip_size, cfg.pip_value_per_lot, cfg.commission_per_lot,
        )
        risk_amount = equity * risk_pct / 100.0
        r_mult = pnl / risk_amount if risk_amount > 0 else 0.0

        trades.append(
            ClosedTrade(
                setup_id=str(s["setup_id"]),
                direction=direction,
                entry_time=t.iloc[entry_bar],
                exit_time=t.iloc[exit_bar],
                entry_price=float(entry),
                exit_price=float(fill_exit),
                stop=stop,
                target=target,
                lots=float(lots),
                pnl=float(pnl),
                r_multiple=float(r_mult),
                exit_reason=reason,
                bars_held=int(exit_bar - entry_bar),
            )
        )
        busy_until = exit_bar

    return trades


def trades_frame(trades: list[ClosedTrade]) -> pd.DataFrame:
    return pd.DataFrame([t.__dict__ for t in trades])

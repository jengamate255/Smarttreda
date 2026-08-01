"""Monte Carlo simulation of a trading month.

Resamples the actual trading days of a month (bootstrap with replacement)
and re-runs the equity sequence thousands of times under the prop firm
compliance rules:

* risk per trade: 0.5% of current equity (R-multiple compounding)
* daily loss limit (default 3%): stop trading for the day when hit
* maximum drawdown (default 8% from peak): account blown when hit

Trades come from backtest result JSONs (backtest/results/month/*.json).

Usage:
    python monte_carlo.py                          # month dir default
    python monte_carlo.py --trades backtest/results/month --sims 5000
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

DEFAULT_TRADES = Path(__file__).resolve().parent / "backtest" / "results" / "month"


def load_trades(trades_dir: Path, since: str = "", symbols: set[str] | None = None) -> list[dict]:
    """Flatten backtest result JSONs into trade dicts with R multiples."""
    trades: list[dict] = []
    for f in sorted(Path(trades_dir).glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        symbol = str(data.get("params", {}).get("symbol") or f.stem)
        if symbols and symbol not in symbols:
            continue
        for raw in data.get("trades", []):
            if since and str(raw.get("entry_time", ""))[:10] < since:
                continue
            trade = dict(raw)
            trade["symbol"] = symbol
            risk_amount = float(trade.get("risk_amount") or 0.0)
            if risk_amount > 0:
                trade["r"] = float(trade.get("pnl") or 0.0) / risk_amount
            else:
                trade["r"] = (
                    float(trade.get("rr") or 0.0)
                    if float(trade.get("pnl") or 0.0) > 0
                    else -1.0
                )
            trades.append(trade)
    return trades


def _day_key(entry_time: str) -> str:
    return str(entry_time)[:10]


def _weekday_count(start: str, end: str) -> int:
    d0 = datetime.strptime(start[:10], "%Y-%m-%d")
    d1 = datetime.strptime(end[:10], "%Y-%m-%d")
    count = 0
    day = d0
    while day <= d1:
        if day.weekday() < 5:
            count += 1
        day = datetime.fromordinal(day.toordinal() + 1)
    return count


def build_day_pool(trades: list[dict]) -> tuple[list[list[dict]], int]:
    """Group trades by day; return (day_pool, month_weekdays).

    The pool covers every weekday in the trade window; days without trades
    are empty lists so resampled months can have quiet stretches.
    """
    by_day: dict[str, list[dict]] = {}
    for t in trades:
        by_day.setdefault(_day_key(t["entry_time"]), []).append(t)
    days = sorted(by_day)
    month_weekdays = min(_weekday_count(days[0], days[-1]), 23) if days else 0
    calendar: dict[str, list[dict]] = {}
    day = datetime.strptime(days[0], "%Y-%m-%d")
    end = datetime.strptime(days[-1], "%Y-%m-%d")
    while day <= end:
        if day.weekday() < 5:
            calendar[day.strftime("%Y-%m-%d")] = by_day.get(day.strftime("%Y-%m-%d"), [])
        day = datetime.fromordinal(day.toordinal() + 1)
    return list(calendar.values()), month_weekdays


def _trade_r(trade: dict) -> float:
    r = trade.get("r")
    if r is not None:
        return float(r)
    risk_amount = float(trade.get("risk_amount") or 0.0)
    pnl = float(trade.get("pnl") or 0.0)
    if risk_amount > 0:
        return pnl / risk_amount
    return float(trade.get("rr") or 0.0) if pnl > 0 else -1.0


def simulate_month(
    day_pool: list[list[dict]],
    month_weekdays: int,
    sims: int = 5000,
    start_balance: float = 10000.0,
    risk_percent: float = 0.5,
    daily_loss_percent: float = 3.0,
    max_drawdown_percent: float = 8.0,
    seed: int = 42,
) -> dict:
    rng = np.random.default_rng(seed)
    n_days = len(day_pool)
    finals: list[float] = []
    max_dds: list[float] = []
    n_trades_list: list[int] = []
    positives = 0
    blown = 0
    daily_hits = 0
    sims_with_trades = 0
    risk_frac = risk_percent / 100.0
    daily_frac = daily_loss_percent / 100.0
    dd_stop = 1.0 - max_drawdown_percent / 100.0

    for _ in range(sims):
        equity = start_balance
        peak = start_balance
        max_dd = 0.0
        month_trades = 0
        day_limit_hit = False
        sim_blown = False
        for _ in range(month_weekdays):
            day_trades = day_pool[rng.integers(0, n_days)]
            if not day_trades:
                continue
            day_start = equity
            day_pnl = 0.0
            for trade in day_trades:
                risk = risk_frac * equity
                equity += _trade_r(trade) * risk
                day_pnl += _trade_r(trade) * risk
                month_trades += 1
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak)
                if equity <= start_balance * dd_stop:
                    sim_blown = True
                    break
            if sim_blown:
                break
            if day_pnl <= -daily_frac * day_start:
                day_limit_hit = True
                break
        if sim_blown:
            blown += 1
        elif month_trades:
            sims_with_trades += 1
        if day_limit_hit:
            daily_hits += 1
        if equity > start_balance:
            positives += 1
        finals.append((equity - start_balance) / start_balance * 100.0)
        max_dds.append(max_dd * 100.0)
        n_trades_list.append(month_trades)

    finals_arr = np.asarray(finals)
    dds_arr = np.asarray(max_dds)
    trades_arr = np.asarray(n_trades_list)

    def pct(a: np.ndarray, q: float) -> float:
        return float(np.percentile(a, q))

    return {
        "sims": sims,
        "start_balance": start_balance,
        "risk_percent": risk_percent,
        "daily_loss_percent": daily_loss_percent,
        "max_drawdown_percent": max_drawdown_percent,
        "final_return_pct": {
            "p05": round(pct(finals_arr, 5), 2),
            "p25": round(pct(finals_arr, 25), 2),
            "p50": round(pct(finals_arr, 50), 2),
            "p75": round(pct(finals_arr, 75), 2),
            "p95": round(pct(finals_arr, 95), 2),
            "mean": round(float(finals_arr.mean()), 2),
        },
        "max_drawdown_pct": {
            "p50": round(pct(dds_arr, 50), 2),
            "p90": round(pct(dds_arr, 90), 2),
            "p99": round(pct(dds_arr, 99), 2),
            "worst": round(float(dds_arr.max()), 2),
        },
        "trades_per_month": {
            "p50": round(float(np.median(trades_arr)), 1),
            "p95": round(pct(trades_arr, 95), 1),
            "max": int(trades_arr.max()),
        },
        "prob_positive_month": round(positives / sims, 3),
        "prob_blown_account": round(blown / sims, 3),
        "prob_daily_limit_hit": round(daily_hits / sims, 3),
        "expected_value_per_month": round(float(finals_arr.mean()) / 100.0 * start_balance, 2),
        "seed": seed,
    }


def report(trades: list[dict], stats: dict) -> None:
    r_values = [t["r"] for t in trades]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r <= 0]
    symbols = sorted({t["symbol"] for t in trades})
    print("=" * 62)
    print("MONTE CARLO - past month (bootstrap of trading days)")
    print("=" * 62)
    print(f"trades: {len(trades)}   symbols: {', '.join(symbols)}")
    print(f"  win rate {len(wins) / len(trades) * 100:.1f}%   "
          f"avg win {np.mean(wins):+.2f}R   avg loss {np.mean(losses):+.2f}R   "
          f"expectancy {np.mean(r_values):+.2f}R")
    print(f"actual window net: {sum(t['pnl'] for t in trades):+.2f} "
          f"({sum(t['pnl'] for t in trades) / stats['start_balance'] * 100:+.2f}%)")
    print("-" * 62)
    f = stats["final_return_pct"]
    print(f"{stats['sims']:,} simulations, 0.5% risk/trade, "
          f"daily -{stats['daily_loss_percent']:.0f}% halt, -{stats['max_drawdown_percent']:.0f}% stop")
    print(f"monthly return  p5 {f['p05']:+.2f}%  p25 {f['p25']:+.2f}%  "
          f"p50 {f['p50']:+.2f}%  p75 {f['p75']:+.2f}%  p95 {f['p95']:+.2f}%  "
          f"(mean {f['mean']:+.2f}%)")
    d = stats["max_drawdown_pct"]
    print(f"max drawdown   p50 {d['p50']:.2f}%  p90 {d['p90']:.2f}%  "
          f"p99 {d['p99']:.2f}%  worst {d['worst']:.2f}%")
    t = stats["trades_per_month"]
    print(f"trades/month   median {t['p50']}  p95 {t['p95']}  max {t['max']}")
    print("-" * 62)
    print(f"P(positive month)     {stats['prob_positive_month'] * 100:.1f}%")
    print(f"P(daily limit hit)    {stats['prob_daily_limit_hit'] * 100:.1f}%")
    print(f"P(blown -{stats['max_drawdown_percent']:.0f}%)          {stats['prob_blown_account'] * 100:.1f}%")
    print(f"expected value/month  {stats['expected_value_per_month']:+.2f} "
          f"({stats['expected_value_per_month'] / stats['start_balance'] * 100:+.2f}%)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades", default=str(DEFAULT_TRADES))
    parser.add_argument("--since", default="", help="only trades on/after YYYY-MM-DD")
    parser.add_argument("--symbols", default="", help="comma-separated symbol whitelist")
    parser.add_argument("--sims", type=int, default=5000)
    parser.add_argument("--balance", type=float, default=10000.0)
    parser.add_argument("--risk", type=float, default=0.5)
    parser.add_argument("--daily-loss", type=float, default=3.0)
    parser.add_argument("--max-dd", type=float, default=8.0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    trades = load_trades(
        Path(args.trades),
        since=args.since,
        symbols={s.upper() for s in args.symbols.split(",") if s.strip()}
        or None,
    )
    if not trades:
        print(f"no trades found in {args.trades}", file=sys.stderr)
        return 2
    day_pool, month_weekdays = build_day_pool(trades)
    stats = simulate_month(
        day_pool,
        month_weekdays,
        sims=args.sims,
        start_balance=args.balance,
        risk_percent=args.risk,
        daily_loss_percent=args.daily_loss,
        max_drawdown_percent=args.max_dd,
        seed=args.seed,
    )
    report(trades, stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

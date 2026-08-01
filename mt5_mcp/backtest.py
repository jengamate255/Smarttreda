"""Bar-by-bar backtest engine for the price-action strategy (lookahead-safe).

Run from the package directory (flat imports, like the server):

    python backtest.py --symbols EURUSD,GBPUSD --all
    python backtest.py --symbols EURUSD --trend-tf H4 --zone-tfs H1,H4 --bars 3000

The engine walks the confirmation-timeframe bars one by one. At each bar
close it evaluates the strategy using ONLY data up to that timestamp
(time-ordered slices via binary search), enters on the next bar's open and
simulates SL/TP exits bar by bar. This guarantees no lookahead bias.

Price model (documented, conservative):
    * bar OHLC are treated as bid prices
    * BUY  enters at open + spread, exits at bid (SL/TP evaluated on raw lows/highs)
    * SELL enters at open (bid), exits at bid - spread (spread is paid on exit)
    * if SL and TP are both touched in the same bar the SL wins

Data note: the live SIM terminal caches ~80k M5 bars (~9 months of H1), so
backtests are limited by what MT5 can return for each timeframe.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from client import get_client  # noqa: E402
from common import TIMEFRAME_MT5, pip_size, pip_value_per_lot  # noqa: E402
from indicators import atr  # noqa: E402
from price_action import detect_patterns  # noqa: E402
from strategy import (  # noqa: E402
    DEMAND,
    SUPPLY,
    _best_confirmation,
    _calc_lots,
    _choose_entry_math,
    _select_zone,
    analyze_trend,
)
from supply_demand import detect_zones  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent / "backtest" / "results"

MAX_HOLD_BARS = 200  # close the position if neither SL nor TP hits in N bars


@dataclass
class BacktestParams:
    symbol: str
    trend_timeframe: str = "H4"
    zone_timeframes: list[str] = field(default_factory=lambda: ["H1", "H4"])
    confirmation_timeframe: str = "H1"
    trend_fast_ema: int = 50
    trend_slow_ema: int = 200
    min_zone_score: float = 70.0
    min_trade_score: float = 70.0
    min_confidence_percent: float = 60.0
    lookback: int = 400
    risk_percent: float = 0.5
    tp_min: float = 80.0
    tp_max: float = 100.0
    sl_min: float = 20.0
    sl_max: float = 30.0
    min_rr: float = 3.0
    spread_points: float = 15.0
    start_balance: float = 10000.0
    bars: int = 5000
    timeout_bars: int = MAX_HOLD_BARS


@dataclass
class TradeRecord:
    entry_time: str
    exit_time: str
    action: str
    entry: float
    sl: float
    tp: float
    lots: float
    sl_pips: float
    tp_pips: float
    rr: float
    exit_price: float
    pnl: float
    reason: str
    trend_confidence: float
    zone_strength: float
    confirmation: str
    ml_score: float = 0.0


@dataclass
class BacktestResult:
    params: dict[str, Any]
    n_trades: int
    wins: int
    losses: int
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float
    win_rate: float
    expectancy: float
    total_return_pct: float
    max_drawdown_pct: float
    avg_rr: float
    start: str
    end: str
    trades: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def default_variants(symbols: list[str]) -> list[BacktestParams]:
    """The "all strategies" grid: trend TF x zone TF sets x zone strength."""
    variants: list[BacktestParams] = []
    for symbol in symbols:
        for trend_tf in ("H1", "H4"):
            for zone_tfs in (["H1", "H4"], ["H4", "D1"], ["D1"]):
                for min_zone_score in (40, 70):
                    variants.append(
                        BacktestParams(
                            symbol=symbol,
                            trend_timeframe=trend_tf,
                            zone_timeframes=list(zone_tfs),
                            min_zone_score=float(min_zone_score),
                        )
                    )
    return variants


def _trade_score_formulas(
    trend_conf: float, zone_strength: float, confirm_conf: float, rr: float, min_rr: float
) -> tuple[float, float]:
    rr_score = min(1.0, rr / min_rr) if min_rr else 1.0
    trade_score = (
        trend_conf * 0.25
        + (zone_strength / 100.0) * 0.35
        + confirm_conf * 0.25
        + rr_score * 0.15
    ) * 100.0
    confidence = (
        trend_conf * 0.3
        + (zone_strength / 100.0) * 0.35
        + confirm_conf * 0.35
    ) * 100.0
    return trade_score, confidence


class Backtester:
    """Evaluates the strategy bar-by-bar on pre-fetched frames."""

    def __init__(self, params: BacktestParams, frames: dict[str, pd.DataFrame], info: dict) -> None:
        self.params = params
        self.frames = frames
        self.info = info
        self.pip = pip_size(info)
        self.pip_value = pip_value_per_lot(info)
        self.errors: list[str] = []
        self._zone_cache: dict[str, tuple[int, list[Any], float]] = {}
        self._trend_cache: tuple[int, Any] | None = None
        missing = [
            tf for tf in self._needed_tfs() if tf not in frames or len(frames[tf]) < 50
        ]
        if missing:
            self.errors.append(f"insufficient data for timeframe(s): {', '.join(missing)}")

    def _needed_tfs(self) -> list[str]:
        tfs = [self.params.trend_timeframe, self.params.confirmation_timeframe]
        tfs.extend(self.params.zone_timeframes)
        return sorted(set(tf.upper() for tf in tfs))

    def run(self) -> BacktestResult:
        p = self.params
        result = BacktestResult(
            params=asdict(p),
            n_trades=0,
            wins=0,
            losses=0,
            gross_profit=0.0,
            gross_loss=0.0,
            net_pnl=0.0,
            profit_factor=0.0,
            win_rate=0.0,
            expectancy=0.0,
            total_return_pct=0.0,
            max_drawdown_pct=0.0,
            avg_rr=0.0,
            start="",
            end="",
            errors=list(self.errors),
        )
        if self.errors:
            return result

        base = self.frames[p.confirmation_timeframe.upper()]
        base_times = base["time"].values.astype("datetime64[s]")
        positions: dict[str, np.ndarray] = {}
        for tf, frame in self.frames.items():
            positions[tf] = np.searchsorted(
                frame["time"].values.astype("datetime64[s]"), base_times, side="right"
            )

        warmup = min(max(p.lookback, p.trend_slow_ema) + 60, len(base_times))
        equity = p.start_balance
        peak = equity
        max_dd = 0.0
        trades: list[TradeRecord] = []
        i = warmup
        limit = min(len(base_times), warmup + p.bars)
        if limit <= warmup:
            return result
        result.start = str(base_times[warmup])
        result.end = str(base_times[limit - 1])

        while i < limit:
            trade = self._evaluate_at(base, base_times, positions, i, equity)
            if trade is not None:
                trades.append(trade)
                equity += trade.pnl
                peak = max(peak, equity)
                dd = (peak - equity) / peak * 100.0 if peak else 0.0
                max_dd = max(max_dd, dd)
                i = self._bar_index_for(base_times, trade.exit_time) + 1
            else:
                i += 1

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        result.n_trades = len(trades)
        result.wins = len(wins)
        result.losses = len(losses)
        result.gross_profit = round(gross_profit, 2)
        result.gross_loss = round(gross_loss, 2)
        result.net_pnl = round(sum(t.pnl for t in trades), 2)
        result.profit_factor = round(gross_profit / gross_loss, 3) if gross_loss else 0.0
        result.win_rate = round(len(wins) / len(trades) * 100.0, 1) if trades else 0.0
        result.expectancy = round(sum(t.pnl for t in trades) / len(trades), 2) if trades else 0.0
        result.total_return_pct = round((equity - p.start_balance) / p.start_balance * 100.0, 2)
        result.max_drawdown_pct = round(max_dd, 2)
        result.avg_rr = round(sum(t.rr for t in trades) / len(trades), 2) if trades else 0.0
        result.trades = [asdict(t) for t in trades]
        return result

    def _bar_index_for(self, base_times: np.ndarray, exit_time: str) -> int:
        idx = int(
            np.searchsorted(base_times, np.datetime64(exit_time, "s"), side="left")
        )
        return max(0, min(idx, len(base_times) - 1))

    def _frame_slice(self, tf: str, base_time: np.datetime64) -> pd.DataFrame:
        frame = self.frames[tf]
        pos = int(
            np.searchsorted(
                frame["time"].values.astype("datetime64[s]"), base_time, side="right"
            )
        )
        return frame.iloc[:pos]

    def _select_best_zone(
        self, zones: list[Any], zone_type: str, price: float, atr_val: float
    ) -> tuple[Any | None, float]:
        p = self.params
        best_zone: Any = None
        best_proximity = 0.0
        for zone in zones:
            if zone.type != zone_type or zone.broken:
                continue
            chosen, proximity = _select_zone(
                [zone], zone_type, price, atr_val, p.min_zone_score
            )
            if chosen is not None and proximity >= best_proximity:
                best_zone, best_proximity = chosen, proximity
        return best_zone, best_proximity

    def _evaluate_at(
        self,
        base: pd.DataFrame,
        base_times: np.ndarray,
        positions: dict[str, np.ndarray],
        i: int,
        equity: float,
    ) -> TradeRecord | None:
        p = self.params
        trend_pos = int(positions[p.trend_timeframe][i])
        if (
            self._trend_cache is None or self._trend_cache[0] != trend_pos
        ):
            trend_frame = self.frames[p.trend_timeframe].iloc[:trend_pos]
            if len(trend_frame) < max(p.trend_fast_ema, p.trend_slow_ema) + 10:
                self._trend_cache = (trend_pos, None)
            else:
                self._trend_cache = (
                    trend_pos,
                    analyze_trend(trend_frame, p.trend_fast_ema, p.trend_slow_ema),
                )
        trend = self._trend_cache[1]
        if trend is None or trend.direction not in ("bullish", "bearish"):
            return None
        action = "BUY" if trend.direction == "bullish" else "SELL"
        zone_type = DEMAND if action == "BUY" else SUPPLY

        signal_close = float(base.iloc[i].close)

        best_zone = None
        best_proximity = 0.0
        zone_atrs: list[float] = []
        for tf in p.zone_timeframes:
            tf = tf.upper()
            pos = int(positions[tf][i])
            cached = self._zone_cache.get(tf)
            if cached is not None and cached[0] == pos:
                zones, atr_val = cached[1], cached[2]
            else:
                frame = self.frames[tf].iloc[:pos]
                tail = frame.iloc[max(0, len(frame) - p.lookback):]
                if len(tail) < 60:
                    continue
                atr_val = float(atr(tail).iloc[-1]) or 1e-9
                zones = detect_zones(
                    tail, lookback=min(len(tail), p.lookback), min_strength=p.min_zone_score
                )
                self._zone_cache[tf] = (pos, zones, atr_val)
            zone_atrs.append(atr_val)
            chosen, proximity = self._select_best_zone(
                zones, zone_type, signal_close, atr_val
            )
            if chosen is not None and proximity >= best_proximity:
                best_zone, best_proximity = chosen, proximity

        if best_zone is None:
            return None
        zone_atr = max(zone_atrs) if zone_atrs else 1e-9

        confirm_frame = self.frames[p.confirmation_timeframe].iloc[
            : int(positions[p.confirmation_timeframe][i])
        ]
        conf_tail = confirm_frame.iloc[max(0, len(confirm_frame) - 300):]
        if len(conf_tail) < 60:
            return None
        patterns = detect_patterns(conf_tail, check_last=1)
        confirmation = _best_confirmation(patterns, trend.direction)
        if confirmation is None:
            return None

        if i + 1 >= len(base):
            return None
        next_open = float(base.iloc[i + 1].open)
        spread = p.spread_points * self.pip
        entry_price = next_open + spread if action == "BUY" else next_open

        entry, sl, tp, sl_pips, tp_pips, _ = _choose_entry_math(
            action,
            entry_price,
            best_zone,
            zone_atr,
            self.pip,
            p.sl_min,
            p.tp_min,
            p.tp_max,
            p.min_rr,
        )
        if entry is None:
            return None
        rr = tp_pips / sl_pips if sl_pips else 0.0
        trade_score, confidence = _trade_score_formulas(
            trend.confidence, float(best_zone.strength), confirmation.confidence, rr, p.min_rr
        )
        if trade_score < p.min_trade_score or confidence < p.min_confidence_percent:
            return None

        lots, _, _ = _calc_lots(
            p.symbol, equity, p.risk_percent, sl_pips, self.pip_value, self.info
        )
        if lots is None:
            return None

        exit_price, (exit_kind, exit_ts) = self._simulate_exit(
            base, base_times, i + 1, action, entry, sl, tp
        )
        pnl = self._pnl(action, entry, exit_price, lots)
        try:
            from ml_ranker import rank_signal

            entry_hour = float(str(base_times[i])[11:13])
            ml_score = rank_signal(
                trend.confidence,
                float(best_zone.strength),
                confirmation.confidence,
                rr,
                sl_pips,
                entry_hour,
            )
        except Exception:  # noqa: BLE001
            ml_score = 0.0
        return TradeRecord(
            entry_time=str(base_times[i]),
            exit_time=exit_ts,
            action=action,
            entry=round(entry, 6),
            sl=round(sl, 6),
            tp=round(tp, 6),
            lots=round(lots, 2),
            sl_pips=round(sl_pips, 1),
            tp_pips=round(tp_pips, 1),
            rr=round(rr, 2),
            exit_price=round(exit_price, 6),
            pnl=round(pnl, 2),
            reason=exit_kind,
            trend_confidence=round(trend.confidence, 3),
            zone_strength=float(best_zone.strength),
            confirmation=f"{confirmation.pattern} ({confirmation.direction})",
            ml_score=round(ml_score, 3),
        )

    def _simulate_exit(
        self,
        base: pd.DataFrame,
        base_times: np.ndarray,
        start: int,
        action: str,
        entry: float,
        sl: float,
        tp: float,
    ) -> tuple[float, tuple[str, str]]:
        p = self.params
        limit = min(len(base), start + p.timeout_bars)
        for j in range(start, limit):
            bar = base.iloc[j]
            hit_sl = bar.low <= sl if action == "BUY" else bar.high >= sl
            hit_tp = bar.high >= tp if action == "BUY" else bar.low <= tp
            if hit_sl:
                return float(sl), ("SL", str(base_times[j]))
            if hit_tp:
                return float(tp), ("TP", str(base_times[j]))
        last = base.iloc[limit - 1]
        return float(last.close), ("timeout", str(base_times[limit - 1]))

    def _pnl(self, action: str, entry: float, exit_price: float, lots: float) -> float:
        p = self.params
        per_point = self.pip_value / self.pip
        if action == "BUY":
            return (exit_price - entry) * per_point * lots
        exit_used = exit_price - p.spread_points * self.pip
        return (entry - exit_used) * per_point * lots


def load_frames(symbol: str, tfs: list[str], bars: int) -> dict[str, pd.DataFrame]:
    client = get_client()
    client.ensure_connected()
    frames: dict[str, pd.DataFrame] = {}
    for tf in tfs:
        tf = tf.upper()
        if tf not in TIMEFRAME_MT5:
            continue
        try:
            frames[tf] = client.rates(symbol, tf, bars)
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN no {tf} data for {symbol}: {exc}", file=sys.stderr)
    return frames


def _variant_name(p: BacktestParams) -> str:
    return (
        f"{p.symbol}_{p.trend_timeframe}_"
        f"{'+'.join(p.zone_timeframes)}_z{int(p.min_zone_score)}"
        f"_rr{p.min_rr:g}_tp{p.tp_min:g}-{p.tp_max:g}"
    )


def save_result(result: BacktestResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_variant_name(BacktestParams(**result.params))}.json"
    path.write_text(
        json.dumps(asdict(result), indent=2, default=str), encoding="utf-8"
    )
    return path


def rebuild_summary(out_dir: Path) -> Path:
    rows: list[dict[str, Any]] = []
    for f in sorted(out_dir.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        rows.append(
            {
                "symbol": data["params"]["symbol"],
                "trend_tf": data["params"]["trend_timeframe"],
                "zone_tfs": "+".join(data["params"]["zone_timeframes"]),
                "min_zone_score": data["params"]["min_zone_score"],
                "min_rr": data["params"]["min_rr"],
                "tp_min": data["params"]["tp_min"],
                "tp_max": data["params"]["tp_max"],
                "trades": data["n_trades"],
                "win_rate": data["win_rate"],
                "profit_factor": data["profit_factor"],
                "net_pnl": data["net_pnl"],
                "total_return_pct": data["total_return_pct"],
                "max_drawdown_pct": data["max_drawdown_pct"],
                "expectancy": data["expectancy"],
                "avg_rr": data["avg_rr"],
                "start": data["start"],
                "end": data["end"],
            }
        )
    path = out_dir / "summary.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["symbol"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the price-action strategy")
    parser.add_argument("--symbols", default="EURUSD", help="comma-separated symbols")
    parser.add_argument("--all", action="store_true", help="run the full parameter grid")
    parser.add_argument("--trend-tf", default="H4")
    parser.add_argument("--zone-tfs", default="H1,H4", help="comma-separated timeframes")
    parser.add_argument("--confirm-tf", default="H1")
    parser.add_argument("--min-zone-score", type=float, default=70.0)
    parser.add_argument("--risk", type=float, default=0.5)
    parser.add_argument("--tp-min", type=float, default=80.0)
    parser.add_argument("--tp-max", type=float, default=100.0)
    parser.add_argument("--sl-min", type=float, default=20.0)
    parser.add_argument("--min-rr", type=float, default=3.0)
    parser.add_argument("--spread-points", type=float, default=15.0)
    parser.add_argument("--bars", type=int, default=5000)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.all:
        variants = default_variants(symbols)
    else:
        variants = [
            BacktestParams(
                symbol=s,
                trend_timeframe=args.trend_tf.upper(),
                zone_timeframes=[tf.upper() for tf in args.zone_tfs.split(",") if tf.strip()],
                confirmation_timeframe=args.confirm_tf.upper(),
                min_zone_score=args.min_zone_score,
                risk_percent=args.risk,
                tp_min=args.tp_min,
                tp_max=args.tp_max,
                sl_min=args.sl_min,
                min_rr=args.min_rr,
                spread_points=args.spread_points,
                bars=args.bars,
            )
            for s in symbols
        ]

    print(f"Running {len(variants)} backtest variant(s)...\n")
    for p in variants:
        name = _variant_name(p)
        started = time.perf_counter()
        print(f"[{name}] fetching {p.symbol} {p.bars} bars...", flush=True)
        tfs = sorted(
            {p.trend_timeframe, p.confirmation_timeframe, *p.zone_timeframes}
        )
        frames = load_frames(p.symbol, tfs, p.bars)
        info: dict[str, Any] = {}
        try:
            info = get_client().symbol_info(p.symbol)
        except Exception:  # noqa: BLE001
            pass
        bt = Backtester(p, frames, info)
        result = bt.run()
        save_result(result, Path(args.out))
        line = (
            f"  {result.n_trades:4d} trades | win {result.win_rate:5.1f}% | "
            f"PF {result.profit_factor:5.2f} | ret {result.total_return_pct:+7.2f}% | "
            f"maxDD {result.max_drawdown_pct:5.2f}% | exp {result.expectancy:+7.2f} | "
            f"{time.perf_counter() - started:5.1f}s"
        )
        if result.errors:
            line += f" | ERRORS: {'; '.join(result.errors)}"
        print(line)

    summary = rebuild_summary(Path(args.out))
    print(f"\nSummary written to {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

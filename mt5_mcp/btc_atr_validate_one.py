"""Temporary BTCUSD ATR-scaled backtest runner.

This deliberately lives beside (rather than inside) ``backtest.py``.  It
reuses the lookahead-safe bar walker, zone detector, trend/confirmation rules,
lot sizing, spread model, and exit simulator, but replaces the fixed-pip
SL/TP calculation with:

    SL = ATR * multiplier
    TP = SL * RR

or, for ``--stop-mode zone``,

    SL = max(ATR * multiplier, zone distance + 0.2 * ATR)

Run one configuration per process.  The MT5 Python module has previously
crashed when several heavy variants were run in one interpreter.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from backtest import (
    BacktestParams,
    Backtester,
    TradeRecord,
    _trade_score_formulas,
    get_client,
    load_frames,
)
from mt5_mcp.indicators import atr
from mt5_mcp.price_action import detect_patterns
from mt5_mcp.strategy import (
    DEMAND,
    SUPPLY,
    _best_confirmation,
    _calc_lots,
    _select_zone,
    analyze_trend,
)


DEFAULT_OUT = Path(__file__).resolve().parent / "backtest" / "results" / "btc_atr_validation"


@dataclass
class ATRBacktestParams(BacktestParams):
    atr_sl_mult: float = 1.5
    stop_mode: str = "atr"


class ATRBacktester(Backtester):
    """Backtester with ATR/zone-distance stops and RR-derived targets."""

    params: ATRBacktestParams

    def _evaluate_at(
        self,
        base: pd.DataFrame,
        base_times: Any,
        positions: dict[str, Any],
        i: int,
        equity: float,
    ) -> TradeRecord | None:
        p = self.params
        trend_pos = int(positions[p.trend_timeframe][i])
        if self._trend_cache is None or self._trend_cache[0] != trend_pos:
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
        best_zone_atr = 0.0
        for tf in p.zone_timeframes:
            tf = tf.upper()
            pos = int(positions[tf][i])
            cached = self._zone_cache.get(tf)
            if cached is not None and cached[0] == pos:
                zones, atr_val = cached[1], cached[2]
            else:
                frame = self.frames[tf].iloc[:pos]
                tail = frame.iloc[max(0, len(frame) - p.lookback) :]
                if len(tail) < 60:
                    continue
                atr_val = float(atr(tail).iloc[-1]) or 1e-9
                from supply_demand import detect_zones

                zones = detect_zones(
                    tail,
                    lookback=min(len(tail), p.lookback),
                    min_strength=p.min_zone_score,
                )
                self._zone_cache[tf] = (pos, zones, atr_val)

            chosen, proximity = self._select_best_zone(
                zones, zone_type, signal_close, atr_val
            )
            if chosen is not None and proximity >= best_proximity:
                best_zone = chosen
                best_proximity = proximity
                best_zone_atr = atr_val

        if best_zone is None or best_zone_atr <= 0:
            return None

        confirm_frame = self.frames[p.confirmation_timeframe].iloc[
            : int(positions[p.confirmation_timeframe][i])
        ]
        conf_tail = confirm_frame.iloc[max(0, len(confirm_frame) - 300) :]
        if len(conf_tail) < 60:
            return None
        patterns = detect_patterns(conf_tail, check_last=1)
        confirmation = _best_confirmation(patterns, trend.direction)
        if confirmation is None or i + 1 >= len(base):
            return None

        next_open = float(base.iloc[i + 1].open)
        spread = p.spread_points * self.pip
        entry = next_open + spread if action == "BUY" else next_open

        atr_distance = float(p.atr_sl_mult) * best_zone_atr
        if p.stop_mode == "zone":
            if action == "BUY":
                zone_distance = max(0.0, entry - float(best_zone.lower))
            else:
                zone_distance = max(0.0, float(best_zone.upper) - entry)
            atr_distance = max(atr_distance, zone_distance + 0.2 * best_zone_atr)
        if atr_distance <= 0:
            return None

        rr = float(p.min_rr)
        target_distance = rr * atr_distance
        if action == "BUY":
            sl = entry - atr_distance
            tp = entry + target_distance
        else:
            sl = entry + atr_distance
            tp = entry - target_distance

        sl_pips = atr_distance / self.pip
        tp_pips = target_distance / self.pip
        trade_score, confidence = _trade_score_formulas(
            trend.confidence,
            float(best_zone.strength),
            confirmation.confidence,
            rr,
            p.min_rr,
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one ATR-scaled BTCUSD backtest")
    parser.add_argument("--trend-tf", default="H4")
    parser.add_argument("--zone-tfs", default="H1,H4")
    parser.add_argument("--confirm-tf", default="H1")
    parser.add_argument("--min-zone-score", type=float, required=True)
    parser.add_argument("--atr-sl-mult", type=float, required=True)
    parser.add_argument("--min-rr", type=float, required=True)
    parser.add_argument("--stop-mode", choices=("atr", "zone"), default="atr")
    parser.add_argument("--lookback", type=int, default=400)
    parser.add_argument("--bars", type=int, default=1500)
    parser.add_argument("--spread-points", type=float, default=15.0)
    parser.add_argument("--risk", type=float, default=0.5)
    parser.add_argument(
        "--end-time",
        default="2026-07-31T23:00:00",
        help="inclusive UTC cutoff; extra history is fetched before trimming",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    return parser


def _result_path(out_dir: Path, p: ATRBacktestParams) -> Path:
    zone = "+".join(p.zone_timeframes)
    return out_dir / (
        f"{p.symbol}_{p.trend_timeframe}_{zone}_z{int(p.min_zone_score)}"
        f"_atr{p.atr_sl_mult:g}_rr{p.min_rr:g}_{p.stop_mode}"
        f"_b{p.bars}_lb{p.lookback}.json"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    trend_tf = args.trend_tf.upper()
    confirm_tf = args.confirm_tf.upper()
    zone_tfs = [tf.strip().upper() for tf in args.zone_tfs.split(",") if tf.strip()]
    p = ATRBacktestParams(
        symbol="BTCUSD",
        trend_timeframe=trend_tf,
        zone_timeframes=zone_tfs,
        confirmation_timeframe=confirm_tf,
        min_zone_score=args.min_zone_score,
        # Fixed-pip fields are retained only for the base dataclass schema;
        # ATRBacktester does not use them for SL/TP.
        sl_min=0.0,
        sl_max=0.0,
        tp_min=0.0,
        tp_max=0.0,
        min_rr=args.min_rr,
        atr_sl_mult=args.atr_sl_mult,
        stop_mode=args.stop_mode,
        lookback=args.lookback,
        bars=args.bars,
        spread_points=args.spread_points,
        risk_percent=args.risk,
    )

    client = get_client()
    client.ensure_connected()
    info = client.symbol_info("BTCUSD")
    required = (
        "digits",
        "point",
        "trade_tick_size",
        "trade_tick_value",
        "volume_min",
        "volume_step",
    )
    missing = [key for key in required if info.get(key) in (None, "")]
    if missing:
        raise RuntimeError(f"BTCUSD symbol_info is missing required fields: {missing}")

    cutoff = pd.Timestamp(args.end_time) if args.end_time else None
    fetch_bars = args.bars + 500 if cutoff is not None else args.bars
    tfs = sorted({trend_tf, confirm_tf, *zone_tfs})
    started = time.perf_counter()
    print(
        json.dumps(
            {
                "params": asdict(p),
                "symbol_info": {
                    key: info.get(key)
                    for key in (
                        "name",
                        "digits",
                        "point",
                        "trade_tick_size",
                        "trade_tick_value",
                        "volume_min",
                        "volume_step",
                        "volume_max",
                        "trade_contract_size",
                    )
                },
            },
            sort_keys=True,
        ),
        flush=True,
    )

    frames = load_frames("BTCUSD", tfs, fetch_bars)
    for tf in tfs:
        frame = frames.get(tf)
        if frame is None or frame.empty:
            raise RuntimeError(f"No {tf} data returned for BTCUSD")
        if cutoff is not None:
            frame = frame.loc[frame["time"] <= cutoff].tail(args.bars).reset_index(drop=True)
            frames[tf] = frame
        if frame.empty:
            raise RuntimeError(f"No {tf} data remains before cutoff {cutoff}")
        print(f"data {tf}: {len(frame)} bars {frame.time.iloc[0]} -> {frame.time.iloc[-1]}", flush=True)

    result = ATRBacktester(p, frames, info).run()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _result_path(out_dir, p)
    path.write_text(json.dumps(asdict(result), indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "n_trades": result.n_trades,
                "wins": result.wins,
                "losses": result.losses,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "net_pnl": result.net_pnl,
                "total_return_pct": result.total_return_pct,
                "max_drawdown_pct": result.max_drawdown_pct,
                "expectancy": result.expectancy,
                "avg_rr": result.avg_rr,
                "start": result.start,
                "end": result.end,
                "result": str(path),
                "seconds": round(time.perf_counter() - started, 1),
                "errors": result.errors,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

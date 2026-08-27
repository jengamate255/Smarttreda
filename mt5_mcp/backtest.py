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
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mt5_mcp.client import get_client  # noqa: E402
from mt5_mcp.common import TIMEFRAME_MT5, pip_size, pip_value_per_lot  # noqa: E402
from mt5_mcp.indicators import atr, ema, swing_highs, swing_lows  # noqa: E402
from mt5_mcp.price_action import detect_patterns  # noqa: E402
from mt5_mcp.strategy import (  # noqa: E402
    DEMAND,
    SUPPLY,
    _best_confirmation,
    _calc_lots,
    _choose_entry_math,
    _select_zone,
)
from mt5_mcp.supply_demand import detect_zones  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent / "backtest" / "results"
FRAME_CACHE_DIR = Path(__file__).resolve().parent / "backtest" / "frame_cache"
FRAME_TTL_SECONDS = 900.0

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
    slippage_points: float = 1.0  # per-side slippage in price-action points
    commission_per_lot: float = 7.0  # round-trip commission in account currency per lot
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
    total_cost: float = 0.0  # spread + slippage + commission in account currency


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
    total_slippage: float = 0.0
    total_commission: float = 0.0
    total_spread_cost: float = 0.0


class _TrendFast:
    """Minimal trend view used by the engine (direction + confidence)."""

    __slots__ = ("direction", "confidence")

    def __init__(self, direction: str, confidence: float) -> None:
        self.direction = direction
        self.confidence = confidence


def default_variants(symbols: list[str], bars: int = 5000) -> list[BacktestParams]:
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
                            bars=bars,
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

    def _precompute(self) -> None:
        """One-time per-TF arrays. All indicators here are causal/recursive
        (ewm/rolling with min_periods=1, confirmed pivots), so the full-frame
        value at index k is identical to the value the prefix ``[:k+1]``
        would produce. This makes every per-bar look-up O(1) with exact
        results vs. the previous per-bar re-computation."""
        p = self.params
        self._atr_series: dict[str, pd.Series] = {}
        self._atr_full: dict[str, np.ndarray] = {}
        self._ema_fast: dict[str, np.ndarray] = {}
        self._ema_slow: dict[str, np.ndarray] = {}
        self._sw_highs: dict[str, np.ndarray] = {}
        self._sw_lows: dict[str, np.ndarray] = {}
        self._highs: dict[str, np.ndarray] = {}
        self._lows: dict[str, np.ndarray] = {}
        for tf, frame in self.frames.items():
            atr_series = atr(frame)
            self._atr_series[tf] = atr_series
            self._atr_full[tf] = atr_series.to_numpy(dtype=float)
            self._ema_fast[tf] = ema(frame["close"], p.trend_fast_ema).to_numpy(dtype=float)
            self._ema_slow[tf] = ema(frame["close"], p.trend_slow_ema).to_numpy(dtype=float)
            self._sw_highs[tf] = np.asarray(swing_highs(frame), dtype=np.int64)
            self._sw_lows[tf] = np.asarray(swing_lows(frame), dtype=np.int64)
            self._highs[tf] = frame["high"].to_numpy(dtype=float)
            self._lows[tf] = frame["low"].to_numpy(dtype=float)

    def _trend_at(self, tf: str, pos: int) -> _TrendFast | None:
        """Exact replica of analyze_trend on the prefix ``frame.iloc[:pos]``
        using precomputed arrays (see _precompute)."""
        p = self.params
        if pos < max(p.trend_fast_ema, p.trend_slow_ema) + 10:
            return None
        e_fast = float(self._ema_fast[tf][pos - 1])
        e_slow = float(self._ema_slow[tf][pos - 1])
        atr_val = float(self._atr_full[tf][pos - 1])
        if not atr_val:
            atr_val = 1e-9
        right = 5
        highs = self._highs[tf]
        lows = self._lows[tf]
        hs = self._sw_highs[tf]
        ls = self._sw_lows[tf]
        hs = hs[hs + right < pos]
        ls = ls[ls + right < pos]
        hh = lh = hl = ll = 0
        for k in range(1, len(hs)):
            if highs[hs[k]] > highs[hs[k - 1]]:
                hh += 1
            else:
                lh += 1
        for k in range(1, len(ls)):
            if lows[ls[k]] > lows[ls[k - 1]]:
                hl += 1
            else:
                ll += 1
        up_strength = hh + hl
        down_strength = lh + ll
        separation = (e_fast - e_slow) / atr_val

        if e_fast > e_slow and up_strength >= down_strength and up_strength > 0:
            direction = "bullish"
        elif e_fast < e_slow and down_strength >= up_strength and down_strength > 0:
            direction = "bearish"
        else:
            direction = "sideways"

        structure_bonus = 0.0
        if direction == "bullish":
            structure_bonus = min(0.2, 0.1 + 0.05 * min(up_strength, 4))
        elif direction == "bearish":
            structure_bonus = min(0.2, 0.1 + 0.05 * min(down_strength, 4))
        confidence = max(
            0.0, min(1.0, 0.5 + min(0.3, abs(separation) * 0.06) + structure_bonus)
        )
        return _TrendFast(direction=direction, confidence=round(confidence, 3))

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

        self._precompute()

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
        result.total_slippage = round(sum(p.slippage_points * self.pip * (self.pip_value / self.pip) * t.lots * 2 for t in trades), 2)
        result.total_commission = round(sum(p.commission_per_lot * t.lots for t in trades), 2)
        result.total_spread_cost = round(sum(p.spread_points * self.pip * (self.pip_value / self.pip) * t.lots for t in trades), 2)
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
        if self._trend_cache is None or self._trend_cache[0] != trend_pos:
            self._trend_cache = (trend_pos, self._trend_at(p.trend_timeframe, trend_pos))
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
                atr_val = float(self._atr_full[tf][pos - 1]) or 1e-9
                zones = detect_zones(
                    tail,
                    lookback=min(len(tail), p.lookback),
                    min_strength=p.min_zone_score,
                    atr_series=self._atr_series[tf]
                    .iloc[max(0, len(frame) - p.lookback) : len(frame)]
                    .reset_index(drop=True),
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
        conf_pos = int(positions[p.confirmation_timeframe][i])
        patterns = detect_patterns(
            conf_tail,
            check_last=1,
            atr_now=float(self._atr_full[p.confirmation_timeframe.upper()][conf_pos - 1])
            or 1.0,
        )
        confirmation = _best_confirmation(patterns, trend.direction)
        if confirmation is None:
            return None

        if i + 1 >= len(base):
            return None
        next_open = float(base.iloc[i + 1].open)
        spread = p.spread_points * self.pip
        slippage = p.slippage_points * self.pip
        if action == "BUY":
            entry_price = next_open + spread + slippage
        else:
            entry_price = next_open - slippage  # SELL at bid - slippage

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
        # compute cost breakdown
        per_point = self.pip_value / self.pip
        spread_cost = p.spread_points * self.pip * per_point * lots
        slippage_cost = 2 * p.slippage_points * self.pip * per_point * lots  # entry + exit
        commission_cost = p.commission_per_lot * lots
        total_cost = spread_cost + slippage_cost + commission_cost
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
            total_cost=round(total_cost, 2),
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
        slippage = p.slippage_points * self.pip
        if action == "BUY":
            # exit at bid - slippage
            raw = (exit_price - slippage - entry) * per_point * lots
        else:
            # exit at ask (bid + spread)
            exit_used = exit_price + p.spread_points * self.pip + slippage
            raw = (entry - exit_used) * per_point * lots
        # subtract round-trip commission
        commission = p.commission_per_lot * lots
        return raw - commission


def _cache_paths(symbol: str, tf: str, bars: int) -> tuple[Path, Path]:
    return (
        FRAME_CACHE_DIR / f"{symbol}_{tf}_{bars}.parquet",
        FRAME_CACHE_DIR / f"{symbol}_{tf}_{bars}.meta.json",
    )


def _cache_read(path: Path, meta_path: Path) -> tuple[pd.DataFrame, dict] | None:
    try:
        if not (path.exists() and meta_path.exists()):
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return pd.read_parquet(path), meta
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN frame cache read failed ({path.name}): {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def _cache_write(path: Path, meta_path: Path, frame: pd.DataFrame) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
        meta = {"fetched_at": time.time(), "last_time": str(frame["time"].iloc[-1])}
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN frame cache write failed ({path.name}): {exc}", file=sys.stderr)


def _cache_still_fresh(
    symbol: str, tf: str, frame: pd.DataFrame, meta_path: Path
) -> bool:
    """Cheap one-bar fetch to validate a stale cache entry."""
    try:
        client = get_client()
        client.ensure_connected()
        last = client.rates(symbol, tf, 1)
        if len(last) == 0:
            return True
        if str(last["time"].iloc[-1]) == str(frame["time"].iloc[-1]):
            meta = {"fetched_at": time.time(), "last_time": str(frame["time"].iloc[-1])}
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def load_frames(
    symbol: str,
    tfs: list[str],
    bars: int,
    use_cache: bool = True,
    allow_mt5: bool = True,
    ttl: float = FRAME_TTL_SECONDS,
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV frames, reusing a parquet cache across runs and variants.

    * ``use_cache`` enables the disk cache (``backtest/frame_cache``).
    * ``allow_mt5=False`` reads only the cache (used by parallel sweep
      workers; the main process prefetches beforehand).
    * Cache entries younger than ``ttl`` seconds are used as-is; stale ones
      are revalidated against MT5 with a one-bar fetch when MT5 is available,
      otherwise the stale frame is reused with a warning.
    """
    frames: dict[str, pd.DataFrame] = {}
    for tf in tfs:
        tf = tf.upper()
        if tf not in TIMEFRAME_MT5:
            continue
        path, meta_path = _cache_paths(symbol, tf, bars)
        cached = _cache_read(path, meta_path) if use_cache else None
        if cached is not None:
            frame, meta = cached
            age = time.time() - float(meta.get("fetched_at", 0.0))
            if age <= ttl:
                frames[tf] = frame
                continue
            if not allow_mt5:
                print(
                    f"  WARN using stale cached {symbol} {tf} (worker has no MT5)",
                    file=sys.stderr,
                )
                frames[tf] = frame
                continue
            if _cache_still_fresh(symbol, tf, frame, meta_path):
                frames[tf] = frame
                continue
        if not allow_mt5:
            continue
        try:
            client = get_client()
            client.ensure_connected()
            frame = client.rates(symbol, tf, bars)
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN no {tf} data for {symbol}: {exc}", file=sys.stderr)
            if cached is not None:
                frames[tf] = cached[0]
            continue
        if use_cache:
            _cache_write(path, meta_path, frame)
        frames[tf] = frame
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


def _variant_tfs(p: BacktestParams) -> list[str]:
    tfs = {p.trend_timeframe.upper(), p.confirmation_timeframe.upper()}
    tfs.update(tf.upper() for tf in p.zone_timeframes)
    return sorted(tfs)


def _result_line(name: str, result: BacktestResult, data_s: float, compute_s: float) -> str:
    line = (
        f"  {name}: {result.n_trades:4d} trades | win {result.win_rate:5.1f}% | "
        f"PF {result.profit_factor:5.2f} | ret {result.total_return_pct:+7.2f}% | "
        f"maxDD {result.max_drawdown_pct:5.2f}% | exp {result.expectancy:+7.2f} | "
        f"[data {data_s:4.1f}s + compute {compute_s:4.1f}s]"
    )
    if result.errors:
        line += f" | ERRORS: {'; '.join(result.errors)}"
    return line


def _run_variant_job(job: dict) -> dict:
    """Worker entry (module-level so it pickles on Windows spawn)."""
    p = BacktestParams(**job["params"])
    started = time.perf_counter()
    frames = load_frames(
        p.symbol,
        _variant_tfs(p),
        p.bars,
        use_cache=job["use_cache"],
        allow_mt5=False,
        ttl=job["ttl"],
    )
    data_elapsed = time.perf_counter() - started
    bt = Backtester(p, frames, job.get("info") or {})
    result = bt.run()
    save_result(result, Path(job["out_dir"]))
    return {
        "name": _variant_name(p),
        "result": result,
        "data_s": data_elapsed,
        "compute_s": time.perf_counter() - started - data_elapsed,
    }


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
    parser.add_argument(
        "--jobs",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help="parallel worker processes for the sweep (default: 4)",
    )
    parser.add_argument(
        "--no-frame-cache", action="store_true", help="always fetch fresh frames from MT5"
    )
    parser.add_argument(
        "--frame-ttl", type=float, default=FRAME_TTL_SECONDS,
        help="cache freshness window in seconds",
    )
    args = parser.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.all:
        variants = default_variants(symbols, bars=args.bars)
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
    if not variants:
        print("No variants to run.")
        return 1

    use_cache = not args.no_frame_cache

    # Prefetch phase: each unique (symbol, tf set, bars) is fetched once per
    # run, plus symbol info, so variants never touch MT5 themselves.
    fetch_groups: dict[tuple[str, int], set[str]] = {}
    for p in variants:
        fetch_groups.setdefault((p.symbol, p.bars), set()).update(_variant_tfs(p))

    fetch_started = time.perf_counter()
    info_cache: dict[str, dict[str, Any]] = {}
    for (symbol, bars), tfs in fetch_groups.items():
        print(f"  [{symbol}] fetching {sorted(tfs)} {bars} bars...", flush=True)
        load_frames(
            symbol,
            sorted(tfs),
            bars,
            use_cache=use_cache,
            allow_mt5=True,
            ttl=args.frame_ttl,
        )
        if symbol not in info_cache:
            try:
                info_cache[symbol] = get_client().symbol_info(symbol)
            except Exception:  # noqa: BLE001
                info_cache[symbol] = {}
    fetch_elapsed = time.perf_counter() - fetch_started
    print(f"Prefetched {len(fetch_groups)} frame group(s) in {fetch_elapsed:5.1f}s")
    print(f"Running {len(variants)} backtest variant(s)...\n")

    run_started = time.perf_counter()
    if args.jobs > 1 and len(variants) > 1:
        jobs = [
            {
                "params": asdict(p),
                "info": info_cache.get(p.symbol, {}),
                "out_dir": args.out,
                "use_cache": use_cache,
                "ttl": args.frame_ttl,
            }
            for p in variants
        ]
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(_run_variant_job, jobs))
        for done in sorted(results, key=lambda r: r["name"]):
            print(_result_line(done["name"], done["result"], done["data_s"], done["compute_s"]))
        wall = time.perf_counter() - run_started
        total_compute = sum(r["compute_s"] for r in results)
        print(
            f"\nParallel run: wall {wall:5.1f}s | total compute {total_compute:5.1f}s "
            f"({args.jobs} workers)"
        )
    else:
        for p in variants:
            name = _variant_name(p)
            started = time.perf_counter()
            frames = load_frames(
                p.symbol,
                _variant_tfs(p),
                p.bars,
                use_cache=use_cache,
                allow_mt5=True,
                ttl=args.frame_ttl,
            )
            data_elapsed = time.perf_counter() - started
            bt = Backtester(p, frames, info_cache.get(p.symbol, {}))
            result = bt.run()
            save_result(result, Path(args.out))
            compute_elapsed = time.perf_counter() - started - data_elapsed
            print(_result_line(name, result, data_elapsed, compute_elapsed))

    summary = rebuild_summary(Path(args.out))
    print(f"\nSummary written to {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Walk-forward validation engine for the price-action strategy.

Splits historical data into rolling train/test windows, optimizes on the
training window, and evaluates on the unseen test window. Reports aggregated
out-of-sample (OOS) metrics to detect overfitting.

Usage:
    from mt5_mcp.walk_forward import WalkForwardEngine, WalkForwardParams
    engine = WalkForwardEngine(WalkForwardParams(symbol="EURUSD"))
    result = engine.run()
"""

from __future__ import annotations

import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from mt5_mcp.backtest import BacktestParams, Backtester, BacktestResult, load_frames

#: Fresh frames prefetched once by WalkForwardEngine.run(); fold workers reuse this.
_PREFETCHED_FRAMES: dict[str, Any] = {}


@dataclass
class WalkForwardParams:
    """Walk-forward configuration."""
    symbol: str = "EURUSD"
    # window sizes in bars (confirmation TF)
    train_bars: int = 2000  # ~83 days of H1
    test_bars: int = 500    # ~21 days of H1
    step_bars: int = 500    # advance 21 days between folds
    # optimization grid (what to sweep over the training window)
    min_zone_scores: list[float] = field(default_factory=lambda: [40.0, 70.0])
    min_rrs: list[float] = field(default_factory=lambda: [2.0, 3.0])
    # fixed params for the sweep
    trend_timeframe: str = "H4"
    zone_timeframes: list[str] = field(default_factory=lambda: ["H1", "H4"])
    bars_total: int = 10000  # total data to fetch
    risk_percent: float = 0.5
    slippage_points: float = 1.0
    commission_per_lot: float = 7.0
    spread_points: float = 15.0
    # pass-through to BacktestParams
    tp_min: float = 80.0
    tp_max: float = 100.0
    sl_min: float = 20.0
    sl_max: float = 30.0


@dataclass
class FoldResult:
    """Result of a single walk-forward fold."""
    fold: int
    train_start_idx: int
    train_end_idx: int
    test_start_idx: int
    test_end_idx: int
    best_params: dict[str, Any]
    train: BacktestResult
    test: BacktestResult


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward results."""
    symbol: str
    n_folds: int
    folds: list[dict]
    # aggregated OOS metrics
    oos_trades: int = 0
    oos_wins: int = 0
    oos_losses: int = 0
    oos_win_rate: float = 0.0
    oos_profit_factor: float = 0.0
    oos_net_pnl: float = 0.0
    oos_expectancy: float = 0.0
    oos_total_return_pct: float = 0.0
    oos_max_drawdown_pct: float = 0.0
    oos_avg_rr: float = 0.0
    # overfitting detection
    is_ratio: float = 0.0  # in-sample / out-of-sample PF ratio (1.0 = no overfit)
    degradation_pct: float = 0.0  # how much OOS PF degrades vs IS PF
    consistency_score: float = 0.0  # % of folds with positive OOS PnL
    # cost breakdown
    oos_total_slippage: float = 0.0
    oos_total_commission: float = 0.0
    elapsed_s: float = 0.0
    errors: list[str] = field(default_factory=list)


def _run_single_fold(args: tuple) -> FoldResult:
    """Run one walk-forward fold (train + test). Called in a worker process."""
    (
        fold_idx, symbol, train_start, train_bars, test_bars,
        trend_tf, zone_tfs, min_zone_score, min_rr, risk_pct,
        slippage, commission, spread, tp_min, tp_max, sl_min, sl_max,
    ) = args

    # train window
    train_params = BacktestParams(
        symbol=symbol,
        trend_timeframe=trend_tf,
        zone_timeframes=zone_tfs,
        min_zone_score=min_zone_score,
        min_trade_score=50.0,
        min_confidence_percent=50.0,
        min_rr=min_rr,
        tp_min=tp_min,
        tp_max=tp_max,
        sl_min=sl_min,
        sl_max=sl_max,
        risk_percent=risk_pct,
        slippage_points=slippage,
        commission_per_lot=commission,
        spread_points=spread,
        bars=train_bars,
    )

    # test window
    test_params = BacktestParams(
        symbol=symbol,
        trend_timeframe=trend_tf,
        zone_timeframes=zone_tfs,
        min_zone_score=min_zone_score,
        min_trade_score=50.0,
        min_confidence_percent=50.0,
        min_rr=min_rr,
        tp_min=tp_min,
        tp_max=tp_max,
        sl_min=sl_min,
        sl_max=sl_max,
        risk_percent=risk_pct,
        slippage_points=slippage,
        commission_per_lot=commission,
        spread_points=spread,
        bars=test_bars,
    )

    try:
        # load frames for train window
        frames = _PREFETCHED_FRAMES if _PREFETCHED_FRAMES else load_frames(
            symbol, sorted(set(
                [trend_tf] + zone_tfs + ["H1"]
            )), train_bars + test_bars + 500,
            use_cache=True, allow_mt5=True, ttl=0.0,
        )

        if not frames:
            return FoldResult(
                fold=fold_idx,
                train_start_idx=train_start,
                train_end_idx=train_start + train_bars,
                test_start_idx=train_start + train_bars,
                test_end_idx=train_start + train_bars + test_bars,
                best_params={},
                train=BacktestResult(params={}, n_trades=0, wins=0, losses=0, gross_profit=0, gross_loss=0, net_pnl=0, profit_factor=0, win_rate=0, expectancy=0, total_return_pct=0, max_drawdown_pct=0, avg_rr=0, start="", end="", errors=["no data"]),
                test=BacktestResult(params={}, n_trades=0, wins=0, losses=0, gross_profit=0, gross_loss=0, net_pnl=0, profit_factor=0, win_rate=0, expectancy=0, total_return_pct=0, max_drawdown_pct=0, avg_rr=0, start="", end="", errors=["no data"]),
            )

        # train
        train_bt = Backtester(train_params, frames, {})
        train_result = train_bt.run()

        # test
        test_bt = Backtester(test_params, frames, {})
        test_result = test_bt.run()

        return FoldResult(
            fold=fold_idx,
            train_start_idx=train_start,
            train_end_idx=train_start + train_bars,
            test_start_idx=train_start + train_bars,
            test_end_idx=train_start + train_bars + test_bars,
            best_params={"min_zone_score": min_zone_score, "min_rr": min_rr},
            train=train_result,
            test=test_result,
        )
    except Exception as exc:
        return FoldResult(
            fold=fold_idx,
            train_start_idx=train_start,
            train_end_idx=train_start + train_bars,
            test_start_idx=train_start + train_bars,
            test_end_idx=train_start + train_bars + test_bars,
            best_params={},
            train=BacktestResult(params={}, n_trades=0, wins=0, losses=0, gross_profit=0, gross_loss=0, net_pnl=0, profit_factor=0, win_rate=0, expectancy=0, total_return_pct=0, max_drawdown_pct=0, avg_rr=0, start="", end="", errors=[str(exc)]),
            test=BacktestResult(params={}, n_trades=0, wins=0, losses=0, gross_profit=0, gross_loss=0, net_pnl=0, profit_factor=0, win_rate=0, expectancy=0, total_return_pct=0, max_drawdown_pct=0, avg_rr=0, start="", end="", errors=[str(exc)]),
        )


class WalkForwardEngine:
    """Runs walk-forward optimization and validation."""

    def __init__(self, params: WalkForwardParams) -> None:
        self.params = params

    def run(self) -> WalkForwardResult:
        t0 = time.time()
        p = self.params
        result = WalkForwardResult(symbol=p.symbol, n_folds=0, folds=[])

        # compute fold boundaries
        folds = []
        start = 0
        fold_idx = 0
        while start + p.train_bars + p.test_bars <= p.bars_total:
            folds.append((fold_idx, start))
            start += p.step_bars
            fold_idx += 1

        if not folds:
            result.errors.append("not enough data for even one fold")
            result.elapsed_s = round(time.time() - t0, 2)
            return result

        result.n_folds = len(folds)

        # Prefetch fresh MT5 frames once (ttl=0 forces refetch; allow_mt5=True).
        need_bars = p.train_bars + p.test_bars + 500
        tfs = sorted(set([p.trend_timeframe] + list(p.zone_timeframes) + ["H1"]))
        print(f"  prefetch fresh {p.symbol} {tfs} {need_bars} bars...", flush=True)
        _PREFETCHED_FRAMES.clear()
        _PREFETCHED_FRAMES.update(
            load_frames(p.symbol, tfs, need_bars, use_cache=True, allow_mt5=True, ttl=0.0)
        )
        if not _PREFETCHED_FRAMES:
            result.errors.append("fresh MT5 prefetch returned no frames")
            result.elapsed_s = round(time.time() - t0, 2)
            return result

        # build task args
        tasks = []
        for fi, start_idx in folds:
            # sweep over the grid on the training window
            best_score = -1.0
            best_params = (p.min_zone_scores[0], p.min_rrs[0])
            for mz in p.min_zone_scores:
                for mrr in p.min_rrs:
                    tasks.append((
                        fi, p.symbol, start_idx, p.train_bars, p.test_bars,
                        p.trend_timeframe, p.zone_timeframes, mz, mrr,
                        p.risk_percent, p.slippage_points, p.commission_per_lot,
                        p.spread_points, p.tp_min, p.tp_max, p.sl_min, p.sl_max,
                    ))

        # Sequential only: ProcessPool crashes on Windows spawn (handle closed).
        all_folds: list[FoldResult] = []
        for i, task in enumerate(tasks, 1):
            if i % 8 == 0 or i == len(tasks):
                print(f"  fold task {i}/{len(tasks)}...", flush=True)
            all_folds.append(_run_single_fold(task))

        # group by fold index and pick best IS result per fold
        by_fold: dict[int, list[FoldResult]] = {}
        for fr in all_folds:
            by_fold.setdefault(fr.fold, []).append(fr)

        fold_results: list[FoldResult] = []
        for fi in sorted(by_fold):
            candidates = by_fold[fi]
            # pick the candidate with highest IS profit factor
            best = max(candidates, key=lambda c: c.train.profit_factor)
            fold_results.append(best)

        # aggregate OOS metrics
        oos_trades = oos_wins = oos_losses = 0
        oos_gross_profit = oos_gross_loss = 0.0
        oos_pnls = []
        is_pfs = []
        oos_pfs = []
        total_slippage = total_commission = 0.0

        for fr in fold_results:
            t_test = fr.test
            oos_trades += t_test.n_trades
            oos_wins += t_test.wins
            oos_losses += t_test.losses
            oos_gross_profit += t_test.gross_profit
            oos_gross_loss += t_test.gross_loss
            oos_pnls.append(t_test.net_pnl)
            total_slippage += t_test.total_slippage
            total_commission += t_test.total_commission
            if fr.train.profit_factor > 0:
                is_pfs.append(fr.train.profit_factor)
            if t_test.profit_factor > 0:
                oos_pfs.append(t_test.profit_factor)

            fold_results_dict = {
                "fold": fr.fold,
                "best_params": fr.best_params,
                "train_trades": fr.train.n_trades,
                "train_wr": fr.train.win_rate,
                "train_pf": fr.train.profit_factor,
                "train_net": fr.train.net_pnl,
                "test_trades": t_test.n_trades,
                "test_wr": t_test.win_rate,
                "test_pf": t_test.profit_factor,
                "test_net": t_test.net_pnl,
            }
            result.folds.append(fold_results_dict)

        result.oos_trades = oos_trades
        result.oos_wins = oos_wins
        result.oos_losses = oos_losses
        result.oos_win_rate = round(oos_wins / oos_trades * 100, 1) if oos_trades else 0.0
        result.oos_profit_factor = round(oos_gross_profit / oos_gross_loss, 3) if oos_gross_loss else 0.0
        result.oos_net_pnl = round(sum(oos_pnls), 2)
        result.oos_expectancy = round(sum(oos_pnls) / oos_trades, 2) if oos_trades else 0.0
        result.oos_total_slippage = round(total_slippage, 2)
        result.oos_total_commission = round(total_commission, 2)

        # max DD on OOS equity curve
        equity = p.risk_percent  # start at 1x risk
        peak = equity
        max_dd = 0.0
        for pnl in oos_pnls:
            equity += pnl
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100 if peak else 0
            max_dd = max(max_dd, dd)
        result.oos_max_drawdown_pct = round(max_dd, 2)

        # overfitting detection
        avg_is_pf = sum(is_pfs) / len(is_pfs) if is_pfs else 0.0
        avg_oos_pf = sum(oos_pfs) / len(oos_pfs) if oos_pfs else 0.0
        result.is_ratio = round(avg_oos_pf / avg_is_pf, 3) if avg_is_pf else 0.0
        result.degradation_pct = round((1 - avg_oos_pf / avg_is_pf) * 100, 1) if avg_is_pf else 0.0
        positive_folds = sum(1 for pnl in oos_pnls if pnl > 0)
        result.consistency_score = round(positive_folds / len(oos_pnls) * 100, 1) if oos_pnls else 0.0

        result.elapsed_s = round(time.time() - t0, 2)
        return result

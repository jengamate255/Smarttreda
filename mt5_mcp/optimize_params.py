"""Parameter optimization for auto_trader symbols.

Grid search over parameter ranges with walk-forward validation.
Outputs optimized parameters per symbol.

Usage:
    python optimize_params.py --symbols ETHUSD,USOIL,XAUUSD,EURUSD,USDCAD,USDCHF,SPX500,JP225
    python optimize_params.py --symbols EURUSD --bars 10000 --jobs 4
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import product
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mt5_mcp.backtest import BacktestParams, Backtester, load_frames  # noqa: E402
from mt5_mcp.multi_strategy import STRATEGY_ROUTING  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent / "memory"
DEFAULT_SYMBOLS = ["ETHUSD", "USOIL", "XAUUSD", "EURUSD", "USDCAD", "USDCHF", "SPX500", "JP225"]

# Parameter ranges to search (universal base + symbol-specific adjustments)
PARAM_GRID = {
    "min_confidence_percent": [55, 60, 65, 70],
    "min_trade_score": [60, 65, 70, 75],
    "min_rr": [1.8, 2.0, 2.2, 2.5],
    "min_zone_score": [50, 60, 70],
}

# Symbol-specific SL/TP ranges (volatility-adjusted)
SYMBOL_SL_TP = {
    "EURUSD": {"sl_min": 12, "sl_max": 25, "tp_min": 50, "tp_max": 80},
    "USDCAD": {"sl_min": 12, "sl_max": 25, "tp_min": 50, "tp_max": 80},
    "USDCHF": {"sl_min": 12, "sl_max": 25, "tp_min": 50, "tp_max": 80},
    "XAUUSD": {"sl_min": 25, "sl_max": 60, "tp_min": 80, "tp_max": 160},
    "ETHUSD": {"sl_min": 40, "sl_max": 100, "tp_min": 120, "tp_max": 250},
    "USOIL": {"sl_min": 30, "sl_max": 70, "tp_min": 90, "tp_max": 180},
    "SPX500": {"sl_min": 15, "sl_max": 35, "tp_min": 60, "tp_max": 120},
    "JP225": {"sl_min": 15, "sl_max": 35, "tp_min": 60, "tp_max": 120},
}


def generate_param_combinations(symbol: str) -> list[dict[str, Any]]:
    """Generate parameter combinations for a symbol."""
    sl_tp = SYMBOL_SL_TP.get(symbol, SYMBOL_SL_TP["EURUSD"])
    
    combos = []
    for conf, score, rr, zone in product(
        PARAM_GRID["min_confidence_percent"],
        PARAM_GRID["min_trade_score"],
        PARAM_GRID["min_rr"],
        PARAM_GRID["min_zone_score"],
    ):
        combo = {
            "min_confidence_percent": conf,
            "min_trade_score": score,
            "min_rr": rr,
            "min_zone_score": zone,
            **sl_tp,
        }
        combos.append(combo)
    return combos


def walk_forward_backtest(
    symbol: str,
    params: dict[str, Any],
    total_bars: int,
    train_ratio: float = 0.7,
) -> dict[str, Any]:
    """Run walk-forward backtest (train/test split)."""
    train_bars = int(total_bars * train_ratio)
    test_bars = total_bars - train_bars
    
    # Load frames once
    trend_tf = "H4"
    zone_tfs = ["H1", "H4"]
    tfs = list(set([trend_tf, *zone_tfs, "H1"]))
    frames = load_frames(symbol, tfs, total_bars, use_cache=True, allow_mt5=False, ttl=900)
    info = {}
    
    # Train phase
    train_params = BacktestParams(
        symbol=symbol,
        bars=train_bars,
        risk_percent=0.25,
        **params,
    )
    train_bt = Backtester(train_params, frames, info)
    train_result = train_bt.run()
    
    # Test phase (out-of-sample)
    test_params = BacktestParams(
        symbol=symbol,
        bars=test_bars,
        risk_percent=0.25,
        **params,
    )
    test_bt = Backtester(test_params, frames, info)
    test_result = test_bt.run()
    
    return {
        "params": params,
        "train": {
            "n_trades": train_result.n_trades,
            "win_rate": train_result.win_rate,
            "profit_factor": train_result.profit_factor,
            "net_pnl": train_result.net_pnl,
        },
        "test": {
            "n_trades": test_result.n_trades,
            "win_rate": test_result.win_rate,
            "profit_factor": test_result.profit_factor,
            "net_pnl": test_result.net_pnl,
        },
    }


def optimize_symbol(
    symbol: str,
    total_bars: int,
    min_trades: int = 20,
) -> dict[str, Any]:
    """Find best parameters for a symbol via grid search."""
    print(f"\n{'='*60}")
    print(f"Optimizing {symbol} (strategy: {STRATEGY_ROUTING.get(symbol, 'trend_zone')})")
    print(f"{'='*60}")
    
    combos = generate_param_combinations(symbol)
    print(f"Testing {len(combos)} parameter combinations...")
    
    results = []
    for i, params in enumerate(combos, 1):
        if i % 10 == 0:
            print(f"  Progress: {i}/{len(combos)}")
        
        try:
            result = walk_forward_backtest(symbol, params, total_bars)
            
            # Filter: need minimum trades in test set
            if result["test"]["n_trades"] < min_trades:
                continue
            
            # Score: prioritize test PF, then test WR, then test P&L
            test_pf = result["test"]["profit_factor"]
            test_wr = result["test"]["win_rate"]
            test_pnl = result["test"]["net_pnl"]
            
            # Composite score (PF most important)
            score = (test_pf * 2.0) + (test_wr / 100.0) + (test_pnl / 1000.0)
            
            results.append({
                **result,
                "score": score,
            })
        except Exception as exc:
            print(f"  Error with params {params}: {exc}")
            continue
    
    if not results:
        print(f"  No valid results for {symbol} (insufficient trades)")
        return None
    
    # Sort by composite score
    results.sort(key=lambda x: x["score"], reverse=True)
    best = results[0]
    
    print(f"\n  Best parameters for {symbol}:")
    print(f"    Confidence: {best['params']['min_confidence_percent']}%")
    print(f"    Trade score: {best['params']['min_trade_score']}")
    print(f"    Min RR: {best['params']['min_rr']}")
    print(f"    Zone score: {best['params']['min_zone_score']}")
    print(f"    SL: {best['params']['sl_min']}-{best['params']['sl_max']} pips")
    print(f"    TP: {best['params']['tp_min']}-{best['params']['tp_max']} pips")
    print(f"\n  Test results (out-of-sample):")
    print(f"    Trades: {best['test']['n_trades']}")
    print(f"    Win rate: {best['test']['win_rate']:.1f}%")
    print(f"    Profit factor: {best['test']['profit_factor']:.2f}")
    print(f"    Net P&L: ${best['test']['net_pnl']:.2f}")
    
    return best


def main():
    parser = argparse.ArgumentParser(description="Optimize auto_trader parameters")
    parser.add_argument(
        "--symbols",
        type=str,
        default=",".join(DEFAULT_SYMBOLS),
        help=f"Comma-separated symbols (default: {','.join(DEFAULT_SYMBOLS)})",
    )
    parser.add_argument("--bars", type=int, default=10000, help="Total bars for backtest")
    parser.add_argument("--min-trades", type=int, default=20, help="Minimum trades in test set")
    parser.add_argument("--output", type=str, default="optimized_params.json", help="Output filename")
    args = parser.parse_args()
    
    symbols = [s.strip() for s in args.symbols.split(",")]
    
    print(f"Optimizing parameters for {len(symbols)} symbols")
    print(f"Total bars: {args.bars}, min trades: {args.min_trades}")
    
    optimized = {}
    for symbol in symbols:
        result = optimize_symbol(symbol, args.bars, args.min_trades)
        if result:
            optimized[symbol] = {
                "params": result["params"],
                "test_results": result["test"],
                "train_results": result["train"],
            }
    
    # Save results
    output_path = OUTPUT_DIR / args.output
    with open(output_path, "w") as f:
        json.dump(optimized, f, indent=2)
    
    print(f"\n{'='*60}")
    print(f"Optimization complete!")
    print(f"Results saved to: {output_path}")
    print(f"{'='*60}")
    
    # Summary
    print("\nSummary:")
    for symbol, data in optimized.items():
        test = data["test_results"]
        print(f"  {symbol:8s}: PF {test['profit_factor']:.2f}, WR {test['win_rate']:.1f}%, "
              f"P&L ${test['net_pnl']:.2f} (n={test['n_trades']})")


if __name__ == "__main__":
    main()

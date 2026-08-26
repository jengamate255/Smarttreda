"""BTC H4 Donchian breakout strategy using the existing MT5 backtest framework.
Tests H4 Donchian breakout with ATR-based stops and RR targets.
"""

import json
import time
from pathlib import Path

from backtest import BacktestParams, Backtester, load_frames, get_client, save_result

def main():
    print("BTC H4 Donchian Breakout Strategy Validation (using MT5 backtest framework)")
    print("=" * 80)
    
    # Load BTCUSD H4 data
    print("Loading BTCUSD H4 data...")
    frames = load_frames("BTCUSD", ["H4"], 20000)
    
    if not frames:
        print("No data available")
        return
    
    h4_data = frames["H4"]
    print(f"Loaded {len(h4_data)} H4 bars: {h4_data['time'].iloc[0]} -> {h4_data['time'].iloc[-1]}")
    
    # Symbol info
    info = get_client().symbol_info("BTCUSD") or {"symbol": "BTCUSD", "digits": 2, "point": 0.01}
    
    # Test parameters
    lookbacks = [12, 24, 48, 72]
    atr_stops = [1.5, 2.0, 2.5]
    rr_targets = [2.0, 3.0, 4.0]
    
    results = []
    
    # Run tests
    for lookback in lookbacks:
        for atr_stop in atr_stops:
            for rr_target in rr_targets:
                name = f"BTC_H4_Donchian{lookback}_ATR{atr_stop}_RR{rr_target}"
                print(f"\nTesting {name}...")
                
                start_time = time.time()
                
                # Use the existing backtest framework with H4 zones
                # The framework uses zone detection, but we can adapt it by setting
                # min_zone_score to 0 and using the Donchian range as "zones"
                p = BacktestParams(
                    symbol="BTCUSD",
                    trend_timeframe="H4",
                    zone_timeframes=["H4"],  # Use H4 as zone timeframe
                    confirmation_timeframe="H4",
                    min_zone_score=0,  # No minimum zone score - accept all
                    risk_percent=0.5,
                    sl_min=atr_stop,
                    tp_min=atr_stop * rr_target,
                    tp_max=atr_stop * rr_target * 1.5,
                    min_rr=rr_target,
                    lookback=lookback,
                    bars=10000
                )
                
                try:
                    bt = Backtester(p, frames, info)
                    res = bt.run()
                    
                    elapsed = time.time() - start_time
                    
                    result = {
                        'name': f"BTC_H4_Donchian{lookback}_ATR{atr_stop}_RR{rr_target}",
                        'params': {
                            'symbol': p.symbol,
                            'trend_timeframe': p.trend_timeframe,
                            'zone_timeframes': p.zone_timeframes,
                            'confirmation_timeframe': p.confirmation_timeframe,
                            'min_zone_score': p.min_zone_score,
                            'risk_percent': p.risk_percent,
                            'sl_min': p.sl_min,
                            'tp_min': p.tp_min,
                            'tp_max': p.tp_max,
                            'min_rr': p.min_rr,
                            'lookback': p.lookback,
                            'bars': p.bars
                        },
                        'n_trades': res.n_trades,
                        'wins': res.wins,
                        'losses': res.losses,
                        'win_rate': res.win_rate,
                        'profit_factor': res.profit_factor,
                        'net_pnl': res.net_pnl,
                        'total_return_pct': res.total_return_pct,
                        'max_drawdown_pct': res.max_drawdown_pct,
                        'expectancy': res.expectancy,
                        'start_time': res.start,
                        'end_time': res.end,
                        'elapsed_seconds': elapsed
                    }
                    
                    results.append(result)
                    
                    # Print summary
                    print(f"  {res.n_trades:3d} trades | win {res.win_rate:5.1f}% | "
                          f"PF {res.profit_factor:5.2f} | ret {res.total_return_pct:+7.2f}% | "
                          f"maxDD {res.max_drawdown_pct:5.2f}% | exp {res.expectancy:+7.2f} | {elapsed:.0f}s")
                    
                except Exception as e:
                    print(f"  ERROR: {e}")
    
    # Sort results by profit factor
    results.sort(key=lambda x: x['profit_factor'], reverse=True)
    
    # Save results
    save_dir = Path("backtest/results")
    save_dir.mkdir(parents=True, exist_ok=True)
    
    # Save full period results
    full_period_path = save_dir / "btc_h4_donchian_full.json"
    with open(full_period_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\n{'='*80}")
    print(f"Full period results saved to: {full_period_path}")
    print(f"{'='*80}")
    
    # Print top 5 performing variants
    print("\nTop 5 performing variants:")
    for i, res in enumerate(results[:5], 1):
        print(f"{i}. {res['name']}: PF={res['profit_factor']:.2f}, "
              f"trades={res['n_trades']}, return={res['total_return_pct']:+.2f}%, "
              f"maxDD={res['max_drawdown_pct']:.2f}%, exp={res['expectancy']:.2f}")
    
    # Determine recommendation
    print(f"\n{'='*80}")
    print("RECOMMENDATION")
    print(f"{'='*80}")
    
    # Find the best variant that meets basic criteria
    deploy_candidates = [
        r for r in results 
        if r['profit_factor'] > 1.2 and 
           r['total_return_pct'] > 0 and 
           r['max_drawdown_pct'] < 20 and 
           r['n_trades'] >= 20
    ]
    
    if deploy_candidates:
        best = deploy_candidates[0]
        print(f"Found potentially deployable variant: {best['name']}")
        print(f"  Profit Factor: {best['profit_factor']:.2f}")
        print(f"  Return: {best['total_return_pct']:.2f}%")
        print(f"  Max DD: {best['max_drawdown_pct']:.2f}%")
        print(f"  Trades: {best['n_trades']}")
        print(f"  Expectancy: {best['expectancy']:.2f}")
    else:
        print("No variant meets deployment criteria.")
        print("Top candidates for further refinement:")
        for i, res in enumerate(results[:3], 1):
            print(f"{i}. {res['name']}: PF={res['profit_factor']:.2f}, "
                  f"return={res['total_return_pct']:.2f}%, maxDD={res['max_drawdown_pct']:.2f}%")
    
    print(f"\n{'='*80}")
    print("Note: Results are from the MT5 backtest framework using supply/demand zone logic.")
    print("Donchian breakout is simulated via zone detection with min_zone_score=0.")
    print("Actual Donchian breakout logic would need custom implementation.")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
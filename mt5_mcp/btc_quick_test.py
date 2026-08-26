"""Quick BTC H4 Donchian breakout test with limited bars."""

import json
import time
from pathlib import Path

from backtest import BacktestParams, Backtester, load_frames, get_client

def main():
    print("Quick BTC H4 Donchian Breakout Test")
    print("=" * 80)
    
    # Load fewer bars for speed
    print("Loading BTCUSD H4 data (3000 bars)...")
    frames = load_frames("BTCUSD", ["H4"], 3000)
    
    if not frames:
        print("No data available")
        return
    
    h4_data = frames["H4"]
    print(f"Loaded {len(h4_data)} H4 bars")
    
    info = get_client().symbol_info("BTCUSD") or {"symbol": "BTCUSD", "digits": 2, "point": 0.01}
    
    # Test just a few key combinations
    tests = [
        {"lookback": 48, "atr_stop": 2.0, "rr": 3.0},
        {"lookback": 48, "atr_stop": 2.0, "rr": 2.0},
        {"lookback": 24, "atr_stop": 2.0, "rr": 3.0},
        {"lookback": 72, "atr_stop": 2.0, "rr": 3.0},
        {"lookback": 48, "atr_stop": 1.5, "rr": 2.0},
    ]
    
    results = []
    
    for test in tests:
        name = f"H4_Donchian{test['lookback']}_ATR{test['atr_stop']}_RR{test['rr']}"
        print(f"\nTesting {name}...")
        
        start_time = time.time()
        
        p = BacktestParams(
            symbol="BTCUSD",
            trend_timeframe="H4",
            zone_timeframes=["H4"],
            confirmation_timeframe="H4",
            min_zone_score=0,
            risk_percent=0.5,
            sl_min=test['atr_stop'],
            tp_min=test['atr_stop'] * test['rr'],
            tp_max=test['atr_stop'] * test['rr'] * 1.5,
            min_rr=test['rr'],
            lookback=test['lookback'],
            bars=5000
        )
        
        try:
            bt = Backtester(p, frames, info)
            res = bt.run()
            elapsed = time.time() - start_time
            
            result = {
                'name': f"BTC_H4_Donchian{test['lookback']}_ATR{test['atr_stop']}_RR{test['rr']}",
                'n_trades': res.n_trades,
                'wins': res.wins,
                'losses': res.losses,
                'win_rate': res.win_rate,
                'profit_factor': res.profit_factor,
                'net_pnl': res.net_pnl,
                'total_return_pct': res.total_return_pct,
                'max_drawdown_pct': res.max_drawdown_pct,
                'expectancy': res.expectancy,
                'elapsed_seconds': elapsed
            }
            
            results.append(result)
            print(f"  {res.n_trades:3d} trades | win {res.win_rate:5.1f}% | "
                  f"PF {res.profit_factor:5.2f} | ret {res.total_return_pct:+7.2f}% | "
                  f"maxDD {res.max_drawdown_pct:5.2f}% | exp {res.expectancy:+7.2f} | {elapsed:.0f}s")
            
        except Exception as e:
            print(f"  ERROR: {e}")
    
    # Sort by profit factor
    results.sort(key=lambda x: x['profit_factor'], reverse=True)
    
    print(f"\n{'='*80}")
    print("RESULTS")
    print(f"{'='*80}")
    for i, res in enumerate(results, 1):
        print(f"{i}. {res['name']}: PF={res['profit_factor']:.2f}, "
              f"trades={res['n_trades']}, return={res['total_return_pct']:+.2f}%, "
              f"maxDD={res['max_drawdown_pct']:.2f}%, exp={res['expectancy']:.2f}")

if __name__ == "__main__":
    main()
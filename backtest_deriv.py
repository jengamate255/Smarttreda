import os
os.environ["MT5_MCP_ACCOUNT"] = "DERIV"

from mt5_mcp.backtest import Backtester, BacktestParams, load_frames
from mt5_mcp.config import get_settings
from mt5_mcp.mt5_client import MT5Client

s = get_settings()
deriv_cfg = s.deriv_mt5

# Priority symbols to backtest
priority = [
    'Boom 1000 Index', 'Crash 1000 Index',
    'Boom 500 Index', 'Crash 500 Index', 
    'Boom 300 Index', 'Crash 300 Index',
    'Boom 100 Index', 'Crash 100 Index',
    'Boom 50 Index', 'Crash 50 Index',
]

print("=" * 70)
print("DERIV BOOM/CRASH BACKTEST - H1/H4 Timeframe")
print("=" * 70)

# First, connect to get symbol info
client = MT5Client(deriv_cfg)
if not client.connect():
    print("Failed to connect to DERIV")
    exit(1)

for sym in priority:
    try:
        print(f"\n--- {sym} ---")
        
        # Get symbol info
        info = client.symbol_info(sym)
        if not info:
            print(f"  Symbol not found")
            continue
        
        # Create params
        params = BacktestParams(
            symbol=sym,
            trend_timeframe="H4",
            zone_timeframes=["H1", "H4"],
            confirmation_timeframe="H1",
            bars=5000,
            lookback=400,
            min_zone_score=70.0,
            min_trade_score=70.0,
            min_confidence_percent=60.0,
            risk_percent=0.5,
            min_rr=3.0,
            spread_points=15.0,
        )
        
        # Load frames
        tfs = params._needed_tfs() if hasattr(params, '_needed_tfs') else ["H1", "H4"]
        # Use the Backtester's _needed_tfs method
        from mt5_mcp.backtest import Backtester
        bt_dummy = Backtester.__new__(Backtester)
        bt_dummy.params = params
        needed_tfs = bt_dummy._needed_tfs()
        
        print(f"  Loading frames for {needed_tfs}...")
        frames = load_frames(sym, needed_tfs, params.bars, use_cache=True, allow_mt5=True)
        
        # Run backtest
        bt = Backtester(params, frames, info)
        result = bt.run()
        
        if result and result.n_trades > 0:
            print(f"  Trades: {result.n_trades}")
            print(f"  Win Rate: {result.win_rate:.1f}%")
            print(f"  Net P&L: ${result.total_pnl:.2f}")
            print(f"  Profit Factor: {result.profit_factor:.2f}")
            print(f"  Max DD: {result.max_drawdown:.2f}%")
            print(f"  Expectancy: ${result.expectancy:.2f}")
        else:
            print(f"  No trades")
            
    except Exception as e:
        print(f"  Error: {e}")
        import traceback
        traceback.print_exc()

client.shutdown()
print("\n" + "=" * 70)
print("Backtest complete")
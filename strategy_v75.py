import os
os.environ["MT5_MCP_ACCOUNT"] = "DERIV"

from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import get_settings
from mt5_mcp.backtest import Backtester, BacktestParams
from mt5_mcp.config import load_frames
import pandas as pd
import numpy as np

s = get_settings()
deriv_cfg = s.deriv_mt5
client = MT5Client(deriv_cfg)

if not client.connect():
    print("Failed to connect")
    exit(1)

sym = 'Volatility 75 Index'
print("=" * 60)
print("VOLATILITY 75 INDEX - TRADING STRATEGY & BACKTEST")
print("=" * 60)

# Check symbol
if not client.symbol_exists(sym):
    print("Symbol does not exist")
    exit(1)

# Get rates for backtest
rates = client.rates(sym, 'H1', 500)
if rates is None or len(rates) < 100:
    print("Insufficient data")
    exit(1)

df = pd.DataFrame(rates)
df['time'] = pd.to_datetime(df['time'], unit='s')

# Create BacktestParams
params = BacktestParams(
    symbol=sym,
    trend_timeframe='H4',
    zone_timeframes=['H1', 'H4'],
    confirmation_timeframe='H1',
    min_zone_score=50,
    min_trade_score=50,
    min_confidence_percent=60,
    risk_percent=0.5,
    tp_min=80.0,
    tp_max=100.0,
    sl_min=20.0,
    sl_max=30.0,
    min_rr=3.0,
    spread_points=15.0,
    slippage_points=1.0,
    commission_per_lot=7.0,
    start_balance=10000.0,
    bars=5000,
    timeout_bars=100,
    use_trailing_stop=True,
    trailing_arm_r=1.0,
    trailing_atr_mult=1.2,
    use_partial_tp=True,
    partial_tp_r=1.0,
    partial_tp_pct=0.5,
    use_adaptive_timeout=True,
    adaptive_timeout_atr=0.3,
    adaptive_timeout_bars=80,
    use_vol_spread=True,
    vol_spread_base=1.0,
    vol_spread_max=3.0,
    max_consecutive_losses=0,
    cooldown_bars=20,
    regime_filter='',
    regime_adx_threshold=25.0,
    regime_atr_high_pct=80.0,
    regime_atr_low_pct=20.0,
    sizing='fixed',
    kelly_fraction=0.5,
    correlation_group=''

# Load frames
frames = load_frames(sym, ['H1', 'H4'], 5000, use_cache=True, allow_mt5=True)

# Create backtester
info = client.symbol_info(sym)
bt = Backtester(params, frames, info)

# Run backtest
result = bt.run()

print("=" * 60)
print("BACKTEST RESULTS - VOLATILITY 75 INDEX")
print("=" * 60)
print(f"Trades: {result.n_trades}")
print(f"Win Rate: {result.win_rate:.1f}%")
print(f"Net P&L: ${result.total_pnl:.2f}")
print(f"Profit Factor: {result.profit_factor:.2f}")
print(f"Max DD: {result.max_drawdown:.2f}%")
print(f"Expectancy: ${result.expectancy:.2f}")
print(f"Total Return: {result.total_return_pct:.2f}%")

# Trade details
if result.trades:
    print("\nTrade Details:")
    for i, trade in enumerate(result.trades[:10]):
        print(f"  Trade {i+1}: Entry={trade.entry:.5f}, Exit={trade.exit_price:.5f}, "
              f"Action={trade.action}, P&L={trade.pnl:.2f}, Duration={trade.duration:.0f}h")

# Parameter sensitivity
print("\n" + "=" * 60)
print("PARAMETER SENSITIVITY: ZONE SCORE")
print("=" * 60)
for zone_score in [50, 60, 70, 80]:
    params2 = BacktestParams(
        symbol=sym,
        trend_timeframe='H4',
        zone_timeframes=['H1', 'H4'],
        confirmation_timeframe='H1',
        min_zone_score=zone_score,
        min_trade_score=50,
        min_confidence_percent=60,
        risk_percent=0.5,
        tp_min=80.0,
        tp_max=100.0,
        sl_min=20.0,
        sl_max=30.0,
        min_rr=3.0,
        spread_points=15.0,
        slippage_points=1.0,
        commission_per_lot=7.0,
        start_balance=10000.0,
        bars=5000,
    )
    bt2 = Backtester(params2, frames, info)
    result2 = bt2.run()
    print(f"  Zone Score {zone_score:2d}: Trades={result2.n_trades:3d} | PF={result2.profit_factor:.2f} | "
          f"WR={result2.win_rate:.1f}% | P&L=${result2.total_pnl:.2f}")

client.shutdown()
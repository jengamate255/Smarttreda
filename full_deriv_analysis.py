import os
os.environ["MT5_MCP_ACCOUNT"] = "DERIV"

import MetaTrader5 as mt5
from mt5_mcp.config import get_settings
import pandas as pd
import numpy as np

s = get_settings()
deriv_cfg = s.deriv_mt5

if not mt5.initialize(path=deriv_cfg.terminal_path, timeout=15000):
    print("MT5 init failed:", mt5.last_error())
    exit(1)

print("=" * 70)
print("TECHNICAL ANALYSIS — ALL DERIV PAIRS")
print("=" * 70)

symbols = mt5.symbols_get()
deriv_syms = [s.name for s in symbols if 'Boom' in s.name or 'Crash' in s.name or 'Volatility' in s.name]
print(f"Found {len(deriv_syms)} Deriv synthetic symbols")
print()

for sym_name in sorted(deriv_syms):
    info = mt5.symbol_info(sym_name)
    if info is None or not info.visible:
        mt5.symbol_select(sym_name, True)
        info = mt5.symbol_info(sym_name)
    if info is None:
        continue

    rates = mt5.copy_rates_from_pos(sym_name, mt5.TIMEFRAME_H1, 0, 200)
    if rates is None or len(rates) < 50:
        continue

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')

    # Indicators
    df['sma_10'] = df['close'].rolling(10).mean()
    df['sma_20'] = df['close'].rolling(20).mean()
    df['sma_50'] = df['close'].rolling(50).mean()
    df['ema_12'] = df['close'].ewm(span=12).mean()
    df['ema_26'] = df['close'].ewm(span=26).mean()

    # RSI
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))

    # ATR
    df['tr'] = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift()),
        abs(df['low'] - df['close'].shift())
    ], axis=1).max(axis=1)
    df['atr_14'] = df['tr'].rolling(14).mean()

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    # Trend
    if latest['sma_10'] > latest['sma_20'] > latest['sma_50']:
        trend = "STRONG BULL"
    elif latest['sma_10'] > latest['sma_20']:
        trend = "BULL"
    elif latest['sma_10'] < latest['sma_20'] < latest['sma_50']:
        trend = "STRONG BEAR"
    elif latest['sma_10'] < latest['sma_20']:
        trend = "BEAR"
    else:
        trend = "SIDEWAYS"

    # Momentum
    chg_1h = (latest['close'] - prev['close']) / prev['close'] * 100
    chg_24h = (latest['close'] - df.iloc[-25]['close']) / df.iloc[-25]['close'] * 100 if len(df) > 25 else 0

    # Volatility
    df['log_ret'] = np.log(df['close'] / df['close'].shift(1))
    vol = df['log_ret'].std() * np.sqrt(24 * 260) * 100

    # Support/Resistance
    recent_high = df['high'].iloc[-20:].max()
    recent_low = df['low'].iloc[-20:].min()

    # EMA crossover
    ema_cross = "BULL" if latest['ema_12'] > latest['ema_26'] else "BEAR"

    # RSI
    rsi = latest['rsi']

    print(f"{sym_name:30s} | Close={latest['close']:>10.2f} | Trend={trend:12s} | EMA={ema_cross:4s} | RSI={rsi:5.1f} | 1h={chg_1h:+.2f}% | 24h={chg_24h:+.2f}% | Vol={vol:.1f}% | H={recent_high:.2f} L={recent_low:.2f}")

mt5.shutdown()
print()
print("Analysis complete.")

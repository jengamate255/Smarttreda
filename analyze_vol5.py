import os
os.environ["MT5_MCP_ACCOUNT"] = "DERIV"
import MetaTrader5 as mt5
import pandas as pd
import numpy as np

r = mt5.initialize(path=r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe", timeout=15000)
print("OK" if r else "FAIL:", mt5.last_error())

syms = mt5.symbols_get()
vol5 = [s.name for s in syms if "Volatility 5" in s.name]
print("Volatility 5 symbols:", vol5)

if vol5:
    rates = mt5.copy_rates_from_pos(vol5[0], mt5.TIMEFRAME_H1, 0, 200)
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df["sma10"] = df["close"].rolling(10).mean()
    df["sma20"] = df["close"].rolling(20).mean()
    df["ema12"] = df["close"].ewm(span=12).mean()
    df["ema26"] = df["close"].ewm(span=26).mean()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    df["tr"] = pd.concat([df["high"] - df["low"], abs(df["high"] - df["close"].shift()), abs(df["low"] - df["close"].shift())], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(14).mean()
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    chg1h = (latest["close"] - prev["close"]) / prev["close"] * 100
    chg24h = (latest["close"] - df.iloc[-25]["close"]) / df.iloc[-25]["close"] * 100 if len(df) > 25 else 0
    
    print("=" * 50)
    print("VOLATILITY 5 INDEX - Technical Analysis")
    print("=" * 50)
    print(f"Close: {latest['close']:.2f}")
    print(f"Trend: {'BULL' if latest['sma10'] > latest['sma20'] else 'BEAR'}")
    print(f"EMA: {'BULL' if latest['ema12'] > latest['ema26'] else 'BEAR'}")
    print(f"RSI: {latest['rsi']:.1f}")
    print(f"1h change: {chg1h:.2f}%")
    print(f"24h change: {chg24h:.2f}%")
    print(f"ATR: {latest['atr']:.2f}")
    
    # Signal
    print()
    print("SIGNAL:")
    if "BULL" in latest["trend"] and latest["ema"] == "BULL" and latest["rsi"] < 50:
        print("  BUY - EMA bullish, RSI oversold")
    elif "BEAR" in latest["trend"] and latest["ema"] == "BEAR" and latest["rsi"] > 50:
        print("  SELL - EMA bearish, RSI overbought")
    else:
        print("  Wait for clearer signal")

mt5.shutdown()
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
import pandas as pd
import numpy as np
from mt5_mcp.client import get_client, reset_client

reset_client()
c = get_client("BTCUSD")
c.ensure_connected()
df = c.rates("BTCUSD", "H4", 1000)

lookback = 24
df['donchian_high'] = df['high'].rolling(lookback).max()
df['donchian_low'] = df['low'].rolling(lookback).min()

breakouts = 0
for i in range(lookback + 1, len(df)):
    prev_high = df['donchian_high'].iloc[i - 1]
    prev_low = df['donchian_low'].iloc[i - 1]
    curr_close = df['close'].iloc[i - 1]
    if curr_close > prev_high or curr_close < prev_low:
        breakouts += 1

print(f"Total breakouts detected: {breakouts}")
print(f"Last donchian_high: {df['donchian_high'].iloc[-1]}")
print(f"Last close: {df['close'].iloc[-1]}")
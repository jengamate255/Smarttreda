import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
import argparse
import pandas as pd
from mt5_mcp.client import get_client, reset_client

args = argparse.Namespace(
    live=True, once=False, interval=300.0, ema_fast=50, ema_slow=200,
    atr_period=14, sl_atr=2.0, trail_atr=2.5, risk_percent=0.5,
    timeframe="H4", min_bars_between=20,
    log_file=r"E:\Project\Mt5 Ea\logs\test_args.log"
)

reset_client()
client = get_client("BTCUSD")
budget = 10000 * 0.5 / 100.0

frame = get_client("BTCUSD").rates("BTCUSD", args.timeframe, 500)
print(f"Frame length: {len(frame)}")

ema_fast = frame["close"].ewm(span=50, adjust=False).mean()
ema_slow = frame["close"].ewm(span=200, adjust=False).mean()
high_low = frame["high"] - frame["low"]
high_close = (frame["high"] - frame["close"].shift()).abs()
low_close = (frame["low"] - frame["close"].shift()).abs()
tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
atr_series = tr.rolling(14).mean()

ema_f = frame["close"].ewm(span=50, adjust=False).mean().iloc[-2]
ema_s = frame["close"].ewm(span=200, adjust=False).mean().iloc[-2]
atr_val = atr_series.iloc[-2]
current_price = frame["close"].iloc[-2]
trend_up = ema_f > ema_s

print(f"ema_f: {ema_f:.2f}")
print(f"ema_s: {ema_s:.2f}")
print(f"atr_val: {atr_val:.2f}")
print(f"current_price: {current_price:.2f}")
print(f"trend_up: {trend_up}")
print(f"in zone (ema_s < price < ema_f): {frame['close'].iloc[-2] > ema_s and frame['close'].iloc[-2] < ema_f}")
print(f"price < ema_f: {frame['close'].iloc[-2] < ema_f}")
print(f"price > ema_s: {frame['close'].iloc[-2] > ema_s}")

# Check last few bars
for i in range(-5, 0):
    c = frame["close"].iloc[i]
    ef = frame["close"].ewm(span=50, adjust=False).mean().iloc[i]
    es = frame["close"].ewm(span=200, adjust=False).mean().iloc[i]
    print(f"  Bar {i}: close={c:.0f}, ema_f={ef:.0f}, ema_s={es:.0f}, in_zone={es < c < ef}, trend_up={ef > es}")
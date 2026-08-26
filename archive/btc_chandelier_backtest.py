import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
import pandas as pd
import numpy as np
from mt5_mcp.client import get_client, reset_client

reset_client()
c = get_client("BTCUSD")
c.ensure_connected()
df = c.rates("BTCUSD", "H4", 2000)
print(f"Loaded {len(df)} H4 bars")

# EMA trend + trailing stop (chandelier)
df["ema_fast"] = df["close"].ewm(span=50, adjust=False).mean()
df["ema_slow"] = df["close"].ewm(span=200, adjust=False).mean()
df["atr"] = (df["high"] - df["low"]).rolling(14).mean()

# Chandelier trailing: trail at N * ATR from extreme
trail_atr = 2.5

trades = []
in_trade = False
direction = 0
entry_price = 0
entry_time = None
sl = 0
trail_level = 0

for i in range(210, len(df)):
    ema_f = df["ema_fast"].iloc[i-1]
    ema_s = df["ema_slow"].iloc[i-1]
    close = df["close"].iloc[i-1]
    atr = df["atr"].iloc[i-1]
    
    trend_up = ema_f > ema_s
    
    if not in_trade:
        if trend_up and close < ema_f and close > ema_s:
            # Long pullback entry
            entry_price = df["open"].iloc[i]
            sl = entry_price - atr * 2.0
            trail_level = entry_price  # initialize trail
            in_trade = True
            direction = 1
            entry_time = df["time"].iloc[i]
        elif not trend_up and close > ema_f and close < ema_s:
            # Short pullback entry
            entry_price = df["open"].iloc[i]
            sl = entry_price + atr * 2.0
            trail_level = entry_price
            in_trade = True
            direction = -1
            entry_time = df["time"].iloc[i]
    else:
        high = df["high"].iloc[i]
        low = df["low"].iloc[i]
        
        if direction == 1:
            # Update trailing stop
            if high > trail_level:
                trail_level = high
                sl = trail_level - atr * trail_atr
            
            if low <= sl:
                pnl = sl - entry_price
                trades.append({"entry": entry_time, "exit": df["time"].iloc[i], "pnl": pnl, "result": "TRAIL"})
                in_trade = False
        else:
            if low < trail_level:
                trail_level = low
                sl = trail_level + atr * trail_atr
            
            if high >= sl:
                pnl = entry_price - sl
                trades.append({"entry": entry_time, "exit": df["time"].iloc[i], "pnl": pnl, "result": "TRAIL"})
                in_trade = False

tdf = pd.DataFrame(trades)
if len(trades) > 0:
    wins = tdf[tdf["pnl"] > 0]
    losses = tdf[tdf["pnl"] < 0]
    n = len(tdf)
    wr = len(wins) / n * 100
    gp = wins["pnl"].sum()
    gl = abs(losses["pnl"].sum())
    pf = gp / gl if gl > 0 else float("inf")
    net = tdf["pnl"].sum()
    maxdd = (tdf["pnl"].cumsum().cummax() - tdf["pnl"].cumsum()).max()
    exp = tdf["pnl"].mean()
    print(f"Chandelier trail 2.5xATR: {n} trades | WR={wr:.1f}% | PF={pf:.2f} | Net={net:.0f} | MaxDD={maxdd:.0f} | Exp={exp:.0f}")
    print(tdf[["entry", "exit", "pnl", "result"]].to_string(index=False))
else:
    print("No trades")
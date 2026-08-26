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

# EMA trend + pullback strategy
results = []

for fast in [20, 50, 100]:
    for slow in [100, 200, 300]:
        if fast >= slow:
            continue
            
        df_test = df.copy()
        df_test["ema_fast"] = df_test["close"].ewm(span=fast, adjust=False).mean()
        df_test["ema_slow"] = df_test["close"].ewm(span=slow, adjust=False).mean()
        df_test["atr"] = (df_test["high"] - df_test["low"]).rolling(14).mean()
        
        trades = []
        in_trade = False
        direction = 0
        entry_price = 0
        sl = 0
        tp = 0
        
        for i in range(max(fast, slow) + 10, len(df_test)):
            ema_f = df_test["ema_fast"].iloc[i-1]
            ema_s = df_test["ema_slow"].iloc[i-1]
            close = df_test["close"].iloc[i-1]
            atr = df_test["atr"].iloc[i-1]
            
            trend_up = ema_f > ema_s
            
            if not in_trade:
                # Look for pullback entry
                if trend_up and close < ema_f and close > ema_s:
                    # Long pullback entry
                    entry_price = df_test["open"].iloc[i]
                    sl = entry_price - atr * 2.0
                    tp = entry_price + atr * 3.0
                    in_trade = True
                    direction = 1
                elif not trend_up and close > ema_f and close < ema_s:
                    # Short pullback entry
                    entry_price = df_test["open"].iloc[i]
                    sl = entry_price + atr * 2.0
                    tp = entry_price - atr * 3.0
                    in_trade = True
                    direction = -1
            else:
                high = df_test["high"].iloc[i]
                low = df_test["low"].iloc[i]
                
                if direction == 1:
                    if low <= sl:
                        trades.append({"pnl": sl - entry_price, "result": "SL"})
                        in_trade = False
                    elif high >= tp:
                        trades.append({"pnl": tp - entry_price, "result": "TP"})
                        in_trade = False
                else:
                    if high >= sl:
                        trades.append({"pnl": entry_price - sl, "result": "SL"})
                        in_trade = False
                    elif low <= tp:
                        trades.append({"pnl": entry_price - tp, "result": "TP"})
                        in_trade = False
        
        if trades:
            tdf = pd.DataFrame(trades)
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
            
            print(f"EMA{fast}/{slow}: {n} trades | WR={wr:.1f}% | PF={pf:.2f} | Net={net:.0f} | MaxDD={tdf['pnl'].cumsum().cummax().max() - tdf['pnl'].cumsum().min():.0f} | Exp={exp:.0f}")
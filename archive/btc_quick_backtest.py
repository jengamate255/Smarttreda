"""Quick BTC H4 Donchian Breakout Backtest - standalone"""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

import pandas as pd
import numpy as np
from mt5_mcp.client import get_client, reset_client

reset_client()
c = get_client("BTCUSD")
c.ensure_connected()

# Fetch H4 data
df = c.rates("BTCUSD", "H4", 2000)
print(f"Loaded {len(df)} H4 bars: {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")

# Calculate Donchian channels and ATR
def donchian(df, period):
    df["donchian_high"] = df["high"].rolling(period).max()
    df["donchian_low"] = df["low"].rolling(period).min()
    df["donchian_mid"] = (df["donchian_high"] + df["donchian_low"]) / 2
    return df

def atr(df, period=14):
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# Test parameters
lookbacks = [12, 24, 48, 72]
atr_stops = [1.5, 2.0, 2.5]
rr_targets = [2.0, 3.0, 4.0]

results = []

for lookback in lookbacks:
    for atr_stop in atr_stops:
        for rr_target in rr_targets:
            df_test = df.copy()
            df_test = donchian(df_test, lookback)
            df_test["atr"] = atr(df_test)
            
            trades = []
            in_trade = False
            entry_price = 0
            entry_time = None
            sl = 0
            tp = 0
            direction = 0  # 1 = long, -1 = short
            
            for i in range(lookback + 1, len(df_test)):
                if not in_trade:
                    # Check for breakout
                    prev_high = df_test["donchian_high"].iloc[i-1]
                    prev_low = df_test["donchian_low"].iloc[i-1]
                    curr_close = df_test["close"].iloc[i-1]
                    curr_open = df_test["open"].iloc[i]
                    
                    if curr_close > prev_high:
                        # Long breakout
                        in_trade = True
                        direction = 1
                        entry_price = max(curr_open, prev_high)  # enter at breakout level
                        atr_val = df_test["atr"].iloc[i-1]
                        sl = entry_price - atr_val * atr_stop
                        tp = entry_price + (entry_price - sl) * rr_target
                        entry_time = df_test["time"].iloc[i]
                    elif curr_close < prev_low:
                        # Short breakout
                        in_trade = True
                        direction = -1
                        entry_price = min(curr_open, prev_low)
                        atr_val = df_test["atr"].iloc[i-1]
                        sl = entry_price + atr_val * atr_stop
                        tp = entry_price - (sl - entry_price) * rr_target
                        entry_time = df_test["time"].iloc[i]
                else:
                    # Check exit
                    high = df_test["high"].iloc[i]
                    low = df_test["low"].iloc[i]
                    
                    if direction == 1:
                        if low <= sl:
                            # Stop loss
                            trades.append({"entry": entry_time, "exit": df_test["time"].iloc[i], 
                                         "pnl": sl - entry_price, "result": "SL"})
                            in_trade = False
                        elif high >= tp:
                            # Take profit
                            trades.append({"entry": entry_time, "exit": df_test["time"].iloc[i], 
                                         "pnl": tp - entry_price, "result": "TP"})
                            in_trade = False
                    else:
                        if high >= sl:
                            trades.append({"entry": entry_time, "exit": df_test["time"].iloc[i], 
                                         "pnl": entry_price - sl, "result": "SL"})
                            in_trade = False
                        elif low <= tp:
                            trades.append({"entry": entry_time, "exit": df_test["time"].iloc[i], 
                                         "pnl": entry_price - tp, "result": "TP"})
                            in_trade = False
            
            if trades:
                trades_df = pd.DataFrame(trades)
                wins = trades_df[trades_df["pnl"] > 0]
                losses = trades_df[trades_df["pnl"] < 0]
                
                n_trades = len(trades_df)
                win_rate = len(wins) / n_trades * 100
                gross_profit = wins["pnl"].sum()
                gross_loss = abs(losses["pnl"].sum())
                pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
                net_pnl = trades_df["pnl"].sum()
                avg_win = wins["pnl"].mean() if len(wins) > 0 else 0
                avg_loss = losses["pnl"].mean() if len(losses) > 0 else 0
                max_dd = (trades_df["pnl"].cumsum().cummax() - trades_df["pnl"].cumsum()).max()
                
                results.append({
                    "params": f"L{lookback}_ATR{atr_stop}_RR{rr_target}",
                    "trades": n_trades,
                    "win_rate": win_rate,
                    "pf": pf,
                    "net": net_pnl,
                    "avg_win": avg_win,
                    "avg_loss": avg_loss,
                    "max_dd": max_dd,
                    "expectancy": trades_df["pnl"].mean()
                })

# Print results
results_df = pd.DataFrame(results).sort_values("pf", ascending=False)
print("\n" + "="*100)
print(f"{'Params':<20} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Net':>10} {'AvgW':>10} {'AvgL':>10} {'MaxDD':>10} {'Exp':>8}")
print("="*100)
for _, r in results_df.iterrows():
    print(f"{r['params']:<20} {r['trades']:>7} {r['win_rate']:>6.1f} {r['pf']:>6.2f} {r['net']:>10.1f} {r['avg_win']:>10.1f} {r['avg_loss']:>10.1f} {r['max_dd']:>10.1f} {r['expectancy']:>8.1f}")

# Top 5
print("\nTop 5 by Profit Factor:")
for _, r in results_df.head(5).iterrows():
    print(f"  {r['params']}: PF={r['pf']:.2f} | Trades={r['trades']} | WR={r['win_rate']:.1f}% | Net={r['net']:.0f} | MaxDD={r['max_dd']:.0f}")
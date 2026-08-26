import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
import pandas as pd
from mt5_mcp.client import get_client, reset_client

reset_client()
c = get_client("BTCUSD")
c.ensure_connected()
df = c.rates("BTCUSD", "H4", 200)

# Check the last 20 bars
for i in range(len(df) - 20, len(df)):
    dh = df["high"].rolling(24).max().iloc[i]
    dl = df["low"].rolling(24).min().iloc[i]
    c = df["close"].iloc[i]
    h = df["high"].iloc[i]
    l = df["low"].iloc[i]
    print(f"{df['time'].iloc[i]}: close={c:.0f} high={h:.0f} low={l:.0f} DH={dh:.0f} DL={dl:.0f} breakup={c>dh} breakdn={c<dl}")
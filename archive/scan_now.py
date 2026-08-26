import sys
import pandas as pd
import numpy as np
import datetime

sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig
from mt5_mcp.indicators import atr as _atr

config = DerivConfig(
    enabled=True,
    ws_url="wss://ws.derivws.com/websockets/v3",
    app_id=1089,
    api_token="REDACTED_DERIV_PAT",
)
client = DerivClient(config)
client.connect()

symbols = ["BOOM900", "BOOM1000", "CRASH600", "CRASH900", "BOOM100", "CRASH100", "BOOM500", "CRASH500"]

print("Scan time:", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
hdr = "Symbol".ljust(11) + "Dir".ljust(6) + "Conf".rjust(6) + "Entry".rjust(10) + "SL".rjust(10) + "TP".rjust(10) + "Now".rjust(10) + "Status"
print(hdr)
print("-" * 80)

for symbol in symbols:
    try:
        result = client.ticks_history(symbol, count=60, style="candles", granularity=60)
        if not result or "candles" not in result or len(result["candles"]) < 30:
            continue
        candles = result["candles"]
        df = pd.DataFrame(candles)
        for c in ["open", "high", "low", "close"]:
            df[c] = df[c].astype(float)
        close = df["close"].iloc[-1]
        atr_val = _atr(df, period=14).iloc[-1]
        if atr_val <= 0:
            continue
        spike_up = symbol.startswith("BOOM")
        highs = df["high"].values
        lows = df["low"].values
        if spike_up:
            idx = int(np.argmax(highs))
            spike_h = highs[idx]
            spike_l = np.min(lows[:idx + 1])
            direction = "SHORT"
            entry = spike_h - (spike_h - spike_l) * 0.382
            sl = spike_h + atr_val * 2
            tp = entry - (sl - entry) * 1.5
        else:
            idx = int(np.argmin(lows))
            spike_l = lows[idx]
            spike_h = np.max(highs[:idx + 1])
            direction = "LONG"
            entry = spike_l + (spike_h - spike_l) * 0.382
            sl = spike_l - atr_val * 2
            tp = entry + (entry - sl) * 1.5
        age = len(df) - 1 - idx
        ratio = (spike_h - spike_l) / atr_val
        if ratio < 2.5:
            status = "no-spike"
            conf = 0
        else:
            conf = min(95, 60 + ratio * 3)
            dist = abs(close - entry)
            if (spike_up and close >= spike_h) or (not spike_up and close <= spike_l):
                status = "BROKEN"
            elif dist > atr_val * 1.5:
                status = "OUTSIDE"
            else:
                status = "ACTIONABLE!"
        line = (
            symbol.ljust(11)
            + direction.ljust(6)
            + str(round(conf)).rjust(5) + "%"
            + str(round(entry, 2)).rjust(10)
            + str(round(sl, 2)).rjust(10)
            + str(round(tp, 2)).rjust(10)
            + str(round(close, 2)).rjust(10)
            + " " + status
        )
        print(line)
    except Exception as e:
        print(symbol, "err", str(e)[:40])
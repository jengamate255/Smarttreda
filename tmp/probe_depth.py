"""Find cache depth on Deriv terminal via copy_rates_range."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import MetaTrader5 as mt5

ACCOUNTS = json.loads(Path("accounts.json").read_text(encoding="utf-8"))["accounts"]
DERIV = next(a for a in ACCOUNTS if a["login"] == 41204968)
mt5.initialize(path=DERIV["terminal"], login=DERIV["login"],
               password=DERIV["password"], server=DERIV["server"], timeout=30000)
info = mt5.account_info()
print("account:", info.login if info else None, info.server if info else None)

s = "Boom 500 Index"
mt5.symbol_select(s, True)
now = datetime.now(timezone.utc)
for days in (60, 180, 365, 500, 730):
    for tf_name, tf in (("M1", mt5.TIMEFRAME_M1), ("M5", mt5.TIMEFRAME_M5),
                        ("M15", mt5.TIMEFRAME_M15), ("H1", mt5.TIMEFRAME_H1)):
        r = mt5.copy_rates_range(s, tf, now - timedelta(days=days), now)
        n = 0 if r is None else len(r)
        span = f"{r[0][0] if n else 0} -> {r[-1][0] if n else 0}"
        print(f"  {tf_name} {days}d: n={n:7d}  {span}")
    print("-" * 40)
mt5.shutdown()
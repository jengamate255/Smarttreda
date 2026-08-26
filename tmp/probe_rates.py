"""Debug: which fetch calls work on the Deriv terminal, and what's cached."""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import MetaTrader5 as mt5

ACCOUNTS = json.loads(Path("accounts.json").read_text(encoding="utf-8"))["accounts"]
DERIV = next(a for a in ACCOUNTS if a["login"] == 41204968)
ok = mt5.initialize(path=DERIV["terminal"], login=DERIV["login"],
                    password=DERIV["password"], server=DERIV["server"], timeout=30000)
print("init:", ok, mt5.last_error())
info = mt5.account_info()
print("account:", info.login if info else None, info.server if info else None)

for s in ["Boom 500 Index", "Crash 500 Index", "EURUSD", "Volatility 75 Index"]:
    print("=" * 30, s)
    mt5.symbol_select(s, True)
    time.sleep(0.5)
    # 1: from_pos small count
    r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M5, 0, 100)
    print("  from_pos(100):", "OK n=%d" % len(r) if r is not None else mt5.last_error())
    # 2: from_pos big count
    r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M5, 0, 200000)
    print("  from_pos(200k):", "OK n=%d" % len(r) if r is not None else mt5.last_error())
    # 3: range
    now = datetime.now(timezone.utc)
    r = mt5.copy_rates_range(s, mt5.TIMEFRAME_M5, now - timedelta(days=60), now)
    print("  range(60d):", "OK n=%d" % len(r) if r is not None else mt5.last_error())
    # 4: tick
    t = mt5.symbol_info_tick(s)
    print("  tick:", None if t is None else (t.time, t.bid))
    # 5: M1 from_pos
    r = mt5.copy_rates_from_pos(s, mt5.TIMEFRAME_M1, 0, 1000)
    print("  M1 from_pos(1000):", "OK n=%d" % len(r) if r is not None else mt5.last_error())

mt5.shutdown()
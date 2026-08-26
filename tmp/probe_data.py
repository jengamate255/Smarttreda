"""Probe data availability: Deriv terminal (explicit creds) + prop terminal (get_client)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import MetaTrader5 as mt5

ACCOUNTS = json.loads(Path("accounts.json").read_text(encoding="utf-8"))["accounts"]
DERIV = next(a for a in ACCOUNTS if a["login"] == 41204968)

MODE = sys.argv[1] if len(sys.argv) > 1 else "deriv"

if MODE == "deriv":
    ok = mt5.initialize(
        path=DERIV["terminal"],
        login=DERIV["login"],
        password=DERIV["password"],
        server=DERIV["server"],
        timeout=30000,
    )
    print("init:", ok, mt5.last_error())
    info = mt5.account_info()
    print("account:", info.login if info else None, info.server if info else None)
    syms = [s.name for s in mt5.symbols_get() or []]
    print("n symbols:", len(syms))
    targets = ["Boom 500 Index", "Boom 100 Index", "Crash 500 Index", "Crash 100 Index"]
    for s in targets:
        if s not in syms:
            print(f"  {s}: NOT FOUND"); continue
        si = mt5.symbol_info(s)
        print(f"  {s}: digits={si.digits} point={si.point} spread={si.spread} "
              f"tick_size={si.trade_tick_size} tick_value={si.trade_tick_value}")
        for tf_name, tf in (("M1", mt5.TIMEFRAME_M1), ("M5", mt5.TIMEFRAME_M5),
                            ("M15", mt5.TIMEFRAME_M15), ("H1", mt5.TIMEFRAME_H1)):
            mt5.symbol_select(s, True)
            rates = mt5.copy_rates_from_pos(s, tf, 0, 200000)
            n = 0 if rates is None else len(rates)
            if n:
                t0 = rates[0][0]; t1 = rates[-1][0]
                print(f"    {tf_name}: {n} bars  {t0} -> {t1}  vol0={rates[-1][5]}")
            else:
                print(f"    {tf_name}: none ({mt5.last_error()})")
elif MODE == "prop":
    from mt5_mcp.client import get_client
    c = get_client()
    c.ensure_connected()
    info = c.account_info()
    print("account:", info.get("login"), info.get("server"))
    for s in ("EURUSD", "GBPUSD", "XAUUSD"):
        si = c.symbol_info(s)
        print(f"  {s}: digits={si.get('digits')} point={si.get('point')} spread={si.get('spread')}")
        for tf_name in ("M5", "M15", "H1"):
            try:
                df = c.rates(s, tf_name, 200000)
                print(f"    {tf_name}: {len(df)} bars  {df['time'].iloc[0]} -> {df['time'].iloc[-1]}  vol0={df['tick_volume'].iloc[-1]}")
            except Exception as e:
                print(f"    {tf_name}: ERR {e}")

mt5.shutdown()
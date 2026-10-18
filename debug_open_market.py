"""Reproduce the watcher's open_market() call to debug retcode=None."""
import os, sys, traceback
os.environ["MT5_MCP_ACCOUNT"] = "DERIV"

from mt5_mcp.client import get_client, reset_client
reset_client()
client = get_client()
client.ensure_connected()

# Check account
info = client.account_info()
print(f"Account: {info.get('login')} | {info.get('server')} | balance={info.get('balance')}")

# Check symbol
sym = "Crash 50 Index"
si = client.symbol_info(sym)
print(f"\n{sym}: trade_mode={si.get('trade_mode')} filling_mode={si.get('filling_mode')}")
print(f"  vol_min={si.get('volume_min')} vol_max={si.get('volume_max')}")

tick = client.tick(sym)
print(f"  tick: bid={tick.get('bid')} ask={tick.get('ask')}")

# Try raw mt5.order_send directly
import MetaTrader5 as mt5
from mt5_mcp.common import ORDER_TYPE_SELL, TRADE_ACTION_DEAL, ORDER_TIME_DAY

request = {
    "action": TRADE_ACTION_DEAL,
    "symbol": sym,
    "volume": 0.1,
    "type": ORDER_TYPE_SELL,
    "price": float(tick.get("bid", 0)),
    "sl": float(tick.get("bid", 0)) + 50.0,
    "tp": 0.0,
    "deviation": 20,
    "magic": 20260601,
    "comment": "debug test",
    "type_time": ORDER_TIME_DAY,
}
print(f"\n--- Raw mt5.order_send ---")
print(f"Request: {request}")
raw = mt5.order_send(request)
print(f"Raw result: {raw}")
print(f"Raw type: {type(raw)}")
if raw is not None:
    print(f"  retcode={raw.retcode} comment={raw.comment}")
else:
    print(f"  mt5.last_error() = {mt5.last_error()}")

# Now try via client.order_send (the code path open_market uses)
print(f"\n--- client.order_send (with filling mode) ---")
try:
    result = client.order_send(request)
    print(f"Result: {result}")
except Exception as e:
    print(f"Exception: {type(e).__name__}: {e}")
    traceback.print_exc()

# Now try via open_market (the exact watcher code path)
print(f"\n--- open_market() (exact watcher path) ---")
from mt5_mcp.tools.trades import open_market
try:
    tr = open_market(sym, "SELL", 0.1, sl=float(tick.get("bid", 0)) + 50.0, tp=0.0, comment="debug open_market")
    print(f"TradeResult: accepted={tr.accepted} ticket={tr.ticket} retcode={tr.retcode} error={tr.error}")
    print(f"  request={tr.request}")
except Exception as e:
    print(f"Exception: {type(e).__name__}: {e}")
    traceback.print_exc()

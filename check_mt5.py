"""Check MT5 connection with detailed diagnostics."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

import os
print("MT5_MCP_ACCOUNT:", os.environ.get("MT5_MCP_ACCOUNT", "not set"))
print("MT5_MCP_PASSWORD:", os.environ.get("MT5_MCP_PASSWORD", "not set"))
print("MT5_MCP_SERVER:", os.environ.get("MT5_MCP_SERVER", "not set"))
print("MT5_MCP_PATH:", os.environ.get("MT5_MCP_PATH", "not set"))
print()

# Check settings
from mt5_mcp.config import reload_settings
s = reload_settings()
print("Settings loaded OK")
print(f"  Prop login: {s.mt5.login if hasattr(s, 'mt5') else 'N/A'}")
print(f"  Prop server: {s.mt5.server if hasattr(s, 'mt5') else 'N/A'}")
print()

# Try direct MT5 init
import MetaTrader5 as mt5
print("Attempting MT5.initialize()...")
result = mt5.initialize(
    path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
    login=11911448,
    timeout=15000
)
if result:
    info = mt5.account_info()
    print(f"Connected! Account: {info.login} Server: {info.server} Balance: {info.balance}")
    tick = mt5.symbol_info_tick("EURUSD")
    if tick:
        print(f"EURUSD: Bid={tick.bid:.5f} Ask={tick.ask:.5f}")
    else:
        print("EURUSD: no tick")
    tick2 = mt5.symbol_info_tick("ETHUSD")
    if tick2:
        print(f"ETHUSD: Bid={tick2.bid:.2f} Ask={tick2.ask:.2f}")
    else:
        print("ETHUSD: no tick")
    mt5.shutdown()
else:
    err = mt5.last_error()
    print(f"MT5 init failed: {err}")

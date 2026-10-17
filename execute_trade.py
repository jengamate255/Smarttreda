"""Quick execute - skip init if already connected."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

import MetaTrader5 as mt5

# Try initialize with short timeout
if not mt5.initialize(path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe", timeout=5000):
    print(f"MT5 init failed: {mt5.last_error()}")
    sys.exit(1)

print("MT5 connected")

# Quick compliance check
from mt5_mcp.compliance import gate
r = gate(symbol="ETHUSD", side="BUY", lots=0.92, sl_price=2361.66, sl_pips=51.4, confidence=85)
print(f"Compliance: allowed={r['allowed']} violations={r['violations']}")

if not r['allowed']:
    print("BLOCKED")
    mt5.shutdown()
    sys.exit(1)

# Execute
tick = mt5.symbol_info_tick("ETHUSD")
if not tick:
    print("No tick")
    mt5.shutdown()
    sys.exit(1)

request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": "ETHUSD",
    "volume": 0.92,
    "type": mt5.ORDER_TYPE_BUY,
    "price": tick.ask,
    "sl": 2361.66,
    "tp": 2567.23,
    "deviation": 20,
    "magic": 202609,
    "comment": "spike_drift_scan",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_IOC,
}

print(f"Sending BUY 0.92 ETHUSD @ {tick.ask} SL={2361.66} TP={2567.23}")
from mt5_mcp import execution_gate
try:
    execution_gate.require(request, source="execute_trade")
except execution_gate.OrderBlocked as exc:
    print(f"BLOCKED: {exc}")
    mt5.shutdown()
    sys.exit(1)
result = mt5.order_send(request)
if result:
    print(f"Retcode: {result.retcode} Comment: {result.comment} Deal: {result.deal} Price: {result.price}")
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        print("TRADE EXECUTED")
    else:
        print("TRADE FAILED")
else:
    print(f"Error: {mt5.last_error()}")

mt5.shutdown()

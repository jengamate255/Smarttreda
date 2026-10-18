import os, MetaTrader5 as mt5
os.environ["MT5_MCP_ACCOUNT"] = "DERIV"

from mt5_mcp.client import get_client, reset_client
reset_client()
client = get_client("deriv")
client.ensure_connected()

for sym_name in ["Crash 99 Index", "Boom 500 Index", "Crash 50 Index"]:
    info = client.symbol_info(sym_name)
    vol_min = info.get("volume_min", 0)
    vol_max = info.get("volume_max", 0)
    vol_step = info.get("volume_step", 0)
    print(f"{sym_name}: vol_min={vol_min} vol_max={vol_max} vol_step={vol_step}")

# Test with correct volume on Crash 99
sym = "Crash 99 Index"
info = client.symbol_info(sym)
vol_min = float(info.get("volume_min", 0.01))
tick = mt5.symbol_info_tick(sym)
price = tick.ask

print(f"\nTest BUY {vol_min} lots {sym} @ {price}")
req = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": sym,
    "volume": vol_min,
    "type": mt5.ORDER_TYPE_BUY,
    "price": price,
    "deviation": 20,
    "magic": 99999,
    "comment": "test_at3",
    "type_time": mt5.ORDER_TIME_GTC,
}
result = mt5.order_send(req)
print(f"retcode={result.retcode} comment='{result.comment}' deal={result.deal}")

if result.retcode == 10009 and result.deal > 0:
    print("FILLED! Closing immediately...")
    pos = mt5.positions_get(symbol=sym)
    if pos:
        close_req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": vol_min,
            "type": mt5.ORDER_TYPE_SELL,
            "position": pos[0].ticket,
            "price": mt5.symbol_info_tick(sym).bid,
            "deviation": 20,
            "magic": 99999,
            "comment": "test_close",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        r2 = mt5.order_send(close_req)
        print(f"Close: retcode={r2.retcode} comment='{r2.comment}'")
    print("\nAutoTrading CONFIRMED WORKING")
else:
    print(f"Failed: retcode={result.retcode} {result.comment}")

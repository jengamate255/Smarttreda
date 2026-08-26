import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
from mt5_mcp.client import get_client, reset_client
from mt5_mcp.tools.boom_crash_strategy import get_market_state

reset_client()
c = get_client("BTCUSD")
c.ensure_connected()

tick = c.tick("BTCUSD")
print(f"BTCUSD: bid={tick.get('bid')} ask={tick.get('ask')}")

state = get_market_state("BTCUSD")
print(f"BTC State: trend={state.get('trend')} atr={state.get('atr')} rsi={state.get('rsi')}")

symbols = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
for s in symbols:
    try:
        t = c.tick(s)
        if t.get("bid", 0) > 0:
            spread = t.get("ask", 0) - t.get("bid", 0)
            print(f"{s}: bid={t.get('bid')} ask={t.get('ask')} spread={spread:.5f}")
    except:
        pass
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
from mt5_mcp.client import get_client, reset_client

reset_client()
c = get_client("CRASH1000")
c.ensure_connected()

symbols = ["CRASH1000", "CRASH500", "BOOM1000", "BOOM500", "VOLATILITY 75"]
for s in symbols:
    try:
        tick = c.tick(s)
        bid = tick.get("bid", 0)
        ask = tick.get("ask", 0)
        spread = ask - bid
        print(f"{s}: bid={bid} ask={ask} spread={spread:.2f}")
    except Exception as e:
        print(f"{s}: error - {e}")
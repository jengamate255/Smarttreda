import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
from mt5_mcp.client import get_client, reset_client

reset_client()
c = get_client("CRASH1000")
c.ensure_connected()

info = c.account_info()
print(f"Account: login={info.get('login')} balance={info.get('balance')} equity={info.get('equity')} currency={info.get('currency')}")

symbols = ["CRASH1000", "CRASH500", "BOOM1000", "BOOM500", "BOOM900", "VOLATILITY 75"]
for s in symbols:
    try:
        tick = c.tick(s)
        bid = tick.get("bid", 0)
        ask = tick.get("ask", 0)
        if bid > 0:
            spread = ask - bid
            print(f"{s}: bid={bid} ask={ask} spread={spread:.2f}")
        else:
            print(f"{s}: NO QUOTE")
    except Exception as e:
        print(f"{s}: error - {e}")
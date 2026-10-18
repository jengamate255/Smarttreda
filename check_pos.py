from mt5_mcp.client import get_client
c = get_client('DERIV')
positions = c.positions()
print(f"Open positions: {len(positions)}")
for p in positions:
    print(f"  {p['symbol']} {p['type']} {p['volume']} lots @ {p['price_open']}")

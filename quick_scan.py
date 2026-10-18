"""Quick market snapshot for Deriv."""
from mt5_mcp.client import get_client

c = get_client('DERIV')
a = c.account_info()
print(f"Deriv: {a['login']} @ {a['server']}")
print(f"Balance: ${a['balance']:.2f} | Equity: ${a['equity']:.2f}")
print()

positions = c.positions()
print(f"Open Positions ({len(positions)}):")
for p in positions:
    print(f"  {p['symbol']} {p['type']} {p['volume']} lots @ {p['price_open']} | PnL: ${p.get('profit', 0):.2f}")
print()

# Quick price check for key symbols
symbols = ['EURUSD', 'USDJPY', 'XAUUSD', 'GBPUSD', 'AUDUSD']
print("Current Prices:")
for sym in symbols:
    try:
        t = c.tick(sym)
        spread = (t['ask'] - t['bid'])
        print(f"  {sym:8s} bid={t['bid']:.5f} ask={t['ask']:.5f} spread={spread:.5f}")
    except Exception as e:
        print(f"  {sym:8s} ERROR: {e}")

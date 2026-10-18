"""Scan Deriv synthetic indices - Boom, Crash, and other synthetics."""
from mt5_mcp.client import get_client
import MetaTrader5 as mt5

c = get_client('DERIV')
a = c.account_info()
print(f"Deriv: {a['login']} @ {a['server']}")
print(f"Balance: ${a['balance']:.2f} | Equity: ${a['equity']:.2f}")
print()

# Get all available symbols
all_symbols = mt5.symbols_get()
if not all_symbols:
    print("ERROR: No symbols available")
    exit(1)

# Filter for synthetic indices (Boom, Crash, Step Index, etc.)
synthetic_keywords = ['BOOM', 'CRASH', 'STEP', 'RANGE', 'VOLATILITY', 'VOL', 'DERIV']
synthetics = []

for sym in all_symbols:
    name = sym.name.upper()
    if any(kw in name for kw in synthetic_keywords):
        synthetics.append(sym.name)

print(f"Found {len(synthetics)} synthetic symbols:")
print("-" * 80)

# Group by type
boom = [s for s in synthetics if 'BOOM' in s]
crash = [s for s in synthetics if 'CRASH' in s]
step = [s for s in synthetics if 'STEP' in s]
other = [s for s in synthetics if not any(kw in s for kw in ['BOOM', 'CRASH', 'STEP'])]

print(f"\nBOOM pairs ({len(boom)}):")
for s in sorted(boom):
    t = c.tick(s)
    if t:
        print(f"  {s:20s} bid={t['bid']:.5f} ask={t['ask']:.5f}")

print(f"\nCRASH pairs ({len(crash)}):")
for s in sorted(crash):
    t = c.tick(s)
    if t:
        print(f"  {s:20s} bid={t['bid']:.5f} ask={t['ask']:.5f}")

print(f"\nSTEP indices ({len(step)}):")
for s in sorted(step):
    t = c.tick(s)
    if t:
        print(f"  {s:20s} bid={t['bid']:.5f} ask={t['ask']:.5f}")

if other:
    print(f"\nOther synthetics ({len(other)}):")
    for s in sorted(other)[:10]:  # Limit to first 10
        t = c.tick(s)
        if t:
            print(f"  {s:20s} bid={t['bid']:.5f} ask={t['ask']:.5f}")

print()
print("=" * 80)
print("Note: Boom/Crash pairs have unique tick-based movement patterns.")
print("Boom: sudden upward spikes | Crash: sudden downward spikes")

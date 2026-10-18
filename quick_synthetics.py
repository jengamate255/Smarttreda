"""Quick scan of Boom/Crash pairs on Deriv."""
from mt5_mcp.client import get_client
from mt5_mcp.common import BOOM_CRASH_SYMBOLS

c = get_client('DERIV')
print(f"Deriv: {c.account_info()['login']} @ {c.account_info()['server']}")
print(f"Balance: ${c.account_info()['balance']:.2f}")
print()

print("BOOM/CRASH Pairs - Current Prices:")
print("-" * 80)

boom_pairs = [s for s in BOOM_CRASH_SYMBOLS if 'BOOM' in s]
crash_pairs = [s for s in BOOM_CRASH_SYMBOLS if 'CRASH' in s]

print(f"\nBOOM pairs ({len(boom_pairs)}):")
for sym in boom_pairs:
    try:
        t = c.tick(sym)
        if t:
            spread = t['ask'] - t['bid']
            print(f"  {sym:15s} bid={t['bid']:10.5f} ask={t['ask']:10.5f} spread={spread:.5f}")
    except Exception as e:
        print(f"  {sym:15s} ERROR: {e}")

print(f"\nCRASH pairs ({len(crash_pairs)}):")
for sym in crash_pairs:
    try:
        t = c.tick(sym)
        if t:
            spread = t['ask'] - t['bid']
            print(f"  {sym:15s} bid={t['bid']:10.5f} ask={t['ask']:10.5f} spread={spread:.5f}")
    except Exception as e:
        print(f"  {sym:15s} ERROR: {e}")

print()
print("=" * 80)
print("Boom pairs: sudden upward spikes (trade BUY after spike detection)")
print("Crash pairs: sudden downward spikes (trade SELL after spike detection)")
print("Strategy: spike-drift with pullback entry, confidence floor 90")

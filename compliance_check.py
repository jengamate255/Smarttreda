"""Run compliance checks for EURUSD and ETHUSD BUY setups."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

from mt5_mcp.config import reload_settings
from mt5_mcp.compliance import gate

s = reload_settings()

setups = [
    {"symbol": "EURUSD", "lots": 0.20, "sl_price": 1.1514, "sl_pips": 22.8, "confidence": 78.5},
    {"symbol": "ETHUSD", "lots": 1.0, "sl_price": 2362.19, "sl_pips": 30.2, "confidence": 85.4},
]

print("=== COMPLIANCE CHECKS (ADJUSTED) ===\n")

for setup in setups:
    symbol = setup["symbol"]
    print(f"--- {symbol} ---")
    r = gate(
        symbol=symbol,
        side="BUY",
        lots=setup["lots"],
        sl_price=setup["sl_price"],
        sl_pips=setup["sl_pips"],
        confidence=setup["confidence"],
    )
    print(f"  Allowed: {r['allowed']}")
    print(f"  Mode: {r.get('mode', 'unknown')}")
    print(f"  Violations: {r.get('violations', [])}")
    print(f"  Warnings: {r.get('warnings', [])}")
    print()

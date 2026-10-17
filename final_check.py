"""Final compliance check for ETHUSD with adjusted lots."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

from mt5_mcp.compliance import gate

# ETHUSD: reduce lots from 0.93 to 0.92 to clear the $0.15 overage
print("=== ETHUSD BUY (0.92 lots) ===")
r = gate(
    symbol="ETHUSD",
    side="BUY",
    lots=0.92,
    sl_price=2361.66,
    sl_pips=51.4,
    confidence=85,
)
print(f"Allowed: {r['allowed']}")
print(f"Violations: {r['violations']}")
print(f"Warnings: {r['warnings']}")

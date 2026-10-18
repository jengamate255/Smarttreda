import os
os.environ["MT5_MCP_ACCOUNT"] = "DERIV"

from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import get_settings
from mt5_mcp.strategy import evaluate_strategy

s = get_settings()
deriv_cfg = s.deriv_mt5
client = MT5Client(deriv_cfg)

if not client.connect():
    print("Failed to connect")
    exit(1)

print("=" * 60)
print("DERIV STRATEGY ANALYSIS - Sep 18, 2026")
print("=" * 60)

symbols = ['Boom 1000 Index', 'Crash 1000 Index']

for sym in symbols:
    print(f"\n--- {sym} ---")
    # Test with different zone score thresholds
    for thresh in [50, 60, 70]:
        plan = evaluate_strategy(sym, min_confidence_percent=70)
        if plan and plan.valid:
            zone_ok = plan.zone_strength and plan.zone_strength >= thresh
            if zone_ok:
                print(f"  threshold={thresh}: ✅ VALID | Conf:{plan.confidence:.1f}% | Zone:{plan.zone_strength:.0f} >= {thresh}")
            else:
                print(f"  threshold={thresh}: ❌ Zone too weak ({plan.zone_strength:.0f} < {thresh})")
        else:
            print(f"  threshold={thresh}: ❌ No valid setup (conf={plan.confidence:.1f}%)")
    
    # Also show the plan without threshold filter
    plan = evaluate_strategy(sym, min_confidence_percent=70)
    if plan and plan.valid:
        print(f"  NO FILTER: ✅ {plan.action} | Conf:{plan.confidence:.1f}% | RR:{plan.risk_reward:.2f} | Zone:{plan.zone_lower:.0f}-{plan.zone_upper:.0f} | {plan.reason}")
    elif plan:
        print(f"  NO FILTER: ❌ conf={plan.confidence:.1f}% | reason:{plan.reason}")
    else:
        print(f"  NO FILTER: ❌ No setup available")

client.shutdown()
print("\nDone!")
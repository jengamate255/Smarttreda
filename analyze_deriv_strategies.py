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

print("=" * 70)
print("DERIV DERIVATIVE STRATEGIES - SPIKE-DRIFT ANALYSIS")
print("=" * 70)

# Focus on the main symbols that had data before
symbols = ['Boom 1000 Index', 'Crash 1000 Index']

# Use different zone score thresholds to find setups
thresholds = [50, 60, 70, 80]

print("=" * 70)
print("STRATEGY: Spike-drift during London Open (07:00-09:00 UTC)")
print("=" * 70)

for sym in symbols:
    print(f"\n--- {sym} ---")
    for thresh in thresholds:
        try:
            plan = evaluate_strategy(sym, min_confidence_percent=70)
            if plan and plan.valid:
                # Check if zone score meets threshold
                if plan.zone_strength and plan.zone_strength >= thresh:
                    print(f"  threshold={thresh}: ✅ VALID | Conf:{plan.confidence:.1f}% | RR:{plan.risk_reward:.2f} | Zone:{plan.zone_lower:.0f}-{plan.zone_upper:.0f}")
                else:
                    print(f"  threshold={thresh}: ❌ Zone strength {plan.zone_strength:.0f} < {thresh}")
            else:
                print(f"  threshold={thresh}: ❌ No valid setup (conf={plan.confidence:.1f}%)")
        except Exception as e:
            print(f"  threshold={thresh}: Error - {e}")
    
    # Also check the latest plan without threshold filter
    try:
        plan = evaluate_strategy(sym, min_confidence_percent=70)
        if plan and plan.valid:
            print(f"  NO FILTER: ✅ {plan.action} | Conf:{plan.confidence:.1f}% | RR:{plan.risk_reward:.2f} | Zone:{plan.zone_lower:.0f}-{plan.zone_upper:.0f} | Reason:{plan.reason}")
        elif plan:
            print(f"  NO FILTER: ❌ conf={plan.confidence:.1f}% | reason:{plan.reason}")
    
except Exception as e:
    print(f"Error: {e}")

client.shutdown()
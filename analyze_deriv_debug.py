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
print("DERIV QUICK ANALYSIS - Sep 18, 2026")
print("=" * 60)

for sym in ['Boom 1000 Index', 'Crash 1000 Index']:
    try:
        print(f"\n--- {sym} ---")
        plan = evaluate_strategy(sym)
        print(f"Plan: {plan}")
        if plan:
            print(f"  valid: {plan.valid}")
            if hasattr(plan, 'invalid_reason'):
                print(f"  invalid_reason: {plan.invalid_reason}")
            print(f"  action: {plan.action}")
            print(f"  confidence: {plan.confidence}")
            print(f"  trade_score: {plan.trade_score}")
            print(f"  entry: {plan.entry}")
            print(f"  sl: {plan.sl}")
            print(f"  tp: {plan.tp}")
            print(f"  risk_reward: {plan.risk_reward}")
            print(f"  zone_lower: {plan.zone_lower}")
            print(f"  zone_upper: {plan.zone_upper}")
            print(f"  htf_trend: {plan.htf_trend}")
            print(f"  confirmation: {plan.confirmation}")
    except Exception as e:
        print(f"❌ {sym}: Error - {e}")
        import traceback
        traceback.print_exc()

client.shutdown()
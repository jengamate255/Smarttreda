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

# Test just 2 symbols for speed
for sym in ['Boom 1000 Index', 'Crash 1000 Index']:
    try:
        print(f"\n--- {sym} ---")
        plan = evaluate_strategy(sym)
        if plan and plan.valid:
            print(f"✅ {sym}: {plan.action}")
            print(f"   Confidence: {plan.confidence}% | Score: {plan.trade_score:.1f}")
            print(f"   Entry: {plan.entry:.5f} | SL: {plan.sl:.5f} | TP: {plan.tp:.5f}")
            print(f"   RR: 1:{plan.risk_reward:.2f}")
            print(f"   Zone: {plan.zone_lower:.2f}-{plan.zone_upper:.2f}")
            print(f"   Trend: {plan.htf_trend} | Confirmation: {plan.confirmation}")
        else:
            reason = plan.invalid_reason if plan else "No data"
            print(f"❌ {sym}: {reason}")
    except Exception as e:
        print(f"❌ {sym}: Error - {e}")

client.shutdown()
print("\nDone!")
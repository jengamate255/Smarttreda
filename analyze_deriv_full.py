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

print("=" * 80)
print("DERIV SYNTHETIC INDICES - FULL TECHNICAL SWEEP")
print("=" * 80)

symbols = client.symbols()
bc_symbols = [s for s in symbols if 'BOOM' in s.upper() or 'CRASH' in s.upper()]

print(f"\nFound {len(bc_symbols)} Boom/Crash symbols\n")

results = []
for sym in bc_symbols:
    try:
        plan = evaluate_strategy(sym)
        
        # Determine status
        if plan.valid:
            status = "✅ VALID"
        elif plan.confidence >= 70:
            status = "⚠️  WEAK"
        else:
            status = "❌ NO TRADE"
        
        results.append({
            'symbol': sym,
            'status': status,
            'action': plan.action,
            'confidence': plan.confidence,
            'trade_score': plan.trade_score,
            'trend': plan.htf_trend,
            'zone_type': plan.zone_type,
            'zone_strength': plan.zone_strength,
            'reason': plan.reason,
            'warnings': plan.warnings
        })
        
        # Print compact line
        action_str = plan.action if plan.action != 'NONE' else '---'
        zone_info = f"{plan.zone_type}({plan.zone_strength:.0f})" if plan.zone_type else "none"
        warn = f" ⚠{plan.warnings[0]}" if plan.warnings else ""
        print(f"{sym:28s} | {action_str:4s} | Conf:{plan.confidence:5.1f}% | Score:{plan.trade_score:5.1f} | {plan.htf_trend:8s} | {zone_info:15s}{warn}")
        
    except Exception as e:
        print(f"{sym:28s} | ERROR: {e}")

client.shutdown()

# Summary
print("\n" + "=" * 80)
valid = [r for r in results if r['status'] == '✅ VALID']
weak = [r for r in results if r['status'] == '⚠️  WEAK']
print(f"\n✅ VALID setups: {len(valid)}")
print(f"⚠️  WEAK setups:  {len(weak)}")
print(f"❌ NO TRADE:     {len([r for r in results if r['status'] == '❌ NO TRADE'])}")

if valid:
    print("\n--- VALID SETUPS ---")
    for r in valid:
        print(f"  {r['action']:4s} {r['symbol']:20s} | Conf:{r['confidence']:5.1f}% | Score:{r['trade_score']:5.1f} | {r['trend']} | {r['zone_type']}({r['zone_strength']:.0f})")

if weak:
    print("\n--- WEAK SETUPS (review manually) ---")
    for r in weak:
        print(f"  {r['action']:4s} {r['symbol']:20s} | Conf:{r['confidence']:5.1f}% | Score:{r['trade_score']:5.1f} | {r['trend']} | {r['reason']}")

print("\n" + "=" * 80)
print("Note: min zone score = 70. London open 07-09 UTC is best session.")
print("Paper trading journal: mt5_mcp/memory/boom_crash_journal.csv")
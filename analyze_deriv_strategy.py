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
print("DERIV BOOM/CRASH STRATEGY ANALYSIS - Sep 18, 2026")
print("=" * 60)

symbols = client.symbols()
bc_symbols = [s for s in symbols if 'BOOM' in s.upper() or 'CRASH' in s.upper()]

priority_symbols = [
    'Boom 1000 Index', 'Crash 1000 Index',
    'Boom 500 Index', 'Crash 500 Index',
    'Boom 300 Index', 'Crash 300 Index',
    'Boom 100 Index', 'Crash 100 Index',
    'Boom 50 Index', 'Crash 50 Index',
]

results = []
for sym in priority_symbols:
    if sym not in bc_symbols:
        print(f"{sym}: NOT AVAILABLE")
        continue
    
    try:
        rates = client.rates(sym, 'H1', 500)
        if rates is None or len(rates) < 100:
            print(f"{sym}: Insufficient data")
            continue
        
        plan = evaluate_strategy(sym)
        
        if plan and plan.valid:
            results.append({
                'symbol': sym,
                'action': plan.action,
                'confidence': plan.confidence,
                'entry': plan.entry,
                'sl': plan.sl,
                'tp': plan.tp,
                'rr': plan.risk_reward,
                'zone': f"{plan.zone_lower:.2f}-{plan.zone_upper:.2f}" if plan.zone_lower else "N/A",
                'trend': plan.htf_trend,
                'confirmation': plan.confirmation
            })
            print(f"\n✅ {sym}: {plan.action}")
            print(f"   Confidence: {plan.confidence}% | Score: {plan.trade_score:.1f}")
            print(f"   Entry: {plan.entry:.5f} | SL: {plan.sl:.5f} | TP: {plan.tp:.5f}")
            print(f"   RR: 1:{plan.risk_reward:.2f} | Zone: {plan.zone_lower:.2f}-{plan.zone_upper:.2f}")
            print(f"   Trend: {plan.htf_trend} | Confirmation: {plan.confirmation}")
        else:
            reason = "No valid setup"
            if plan:
                reason = plan.invalid_reason or "Low confidence"
            print(f"❌ {sym}: {reason}")
            
    except Exception as e:
        print(f"❌ {sym}: Error - {e}")

client.shutdown()

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
if results:
    for r in results:
        print(f"  {r['action']:4s} {r['symbol']:20s} | Conf: {r['confidence']:3d}% | RR: 1:{r['rr']:.2f} | {r['trend']}")
else:
    print("  No valid setups found")

print("\n⚠️  News Check: No major news for synthetic indices")
print("⚠️  Session: London open (07:00-09:00 UTC) is best for spike-drift")
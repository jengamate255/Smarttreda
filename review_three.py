"""Force dual-review on USDCHF, GBPCHF, NZDCAD using market data."""
import sys, json
sys.path.insert(0, "E:/Project/Mt5 Ea")
from dotenv import load_dotenv
load_dotenv("E:/Project/Mt5 Ea/.env")
from mt5_mcp.dual_review import review_trade
from mt5_mcp.strategy import evaluate_strategy
from mt5_mcp.market_intel import market_health
from mt5_mcp.client import get_client

client = get_client()
client.connect()

# USDCHF - already in a BUY position
print("=== USDCHF (open BUY position) ===")
mh = market_health("USDCHF")
health = mh.get("health_score", 0) if isinstance(mh, dict) else 0
regime = mh.get("regime", "?") if isinstance(mh, dict) else "?"
trend = mh.get("trend", "?") if isinstance(mh, dict) else "?"
plan = evaluate_strategy("USDCHF")

# Use position data
positions = client.positions()
pos = next((p for p in positions if p["symbol"] == "USDCHF"), None)
if pos:
    print(f"  Position: {pos['type']} {pos['volume']} @ {pos['price_open']} SL={pos.get('sl',0)} TP={pos.get('tp',0)} PnL={pos['profit']:.2f}")
    print(f"  Health={health:.1f} regime={regime} trend={trend}")
    print(f"  Zone strength={getattr(plan,'zone_strength',0):.0f}")

    side_str = "BUY" if pos["type"] == 0 or str(pos["type"]).upper() == "BUY" else "SELL"
    result = review_trade(
        symbol="USDCHF", side=side_str, lots=pos["volume"],
        sl=pos.get("sl"), tp=pos.get("tp"),
        plan={"action": side_str, "valid": True, "confidence": 92,
               "entry": pos["price_open"], "sl": pos.get("sl"), "tp": pos.get("tp"),
               "zone_strength": getattr(plan, "zone_strength", 0),
               "reason": f"open {side_str} position, evaluating hold/close"},
        health=health,
    )
    print(f"  >>> APPROVED: {result.approved}")
    print(f"  >>> Reason: {result.reason}")
    print(f"  >>> Alpha: {result.alpha.decision} (conf {result.alpha.confidence:.0f})")
    for o in (result.alpha.objections or [])[:4]:
        print(f"      alpha: {o}")
    for c in (result.alpha.conditions or [])[:4]:
        print(f"      alpha-cond: {c}")
    print(f"  >>> Beta: {result.beta.decision} (conf {result.beta.confidence:.0f})")
    for o in (result.beta.objections or [])[:4]:
        print(f"      beta: {o}")
    for c in (result.beta.conditions or [])[:4]:
        print(f"      beta-cond: {c}")
else:
    print("  No open position")
print()

# GBPCHF
print("=== GBPCHF ===")
mh2 = market_health("GBPCHF")
health2 = mh2.get("health_score", 0) if isinstance(mh2, dict) else 0
regime2 = mh2.get("regime", "?") if isinstance(mh2, dict) else "?"
plan2 = evaluate_strategy("GBPCHF")
zone_str2 = getattr(plan2, "zone_strength", 0)
zone_type2 = getattr(plan2, "zone_type", "")
zone_upper2 = getattr(plan2, "zone_upper", None)
zone_lower2 = getattr(plan2, "zone_lower", None)
htf2 = getattr(plan2, "htf_trend", "?")

print(f"  Health={health2:.1f} regime={regime2} htf_trend={htf2}")
print(f"  Zone: {zone_type2} {zone_lower2}-{zone_upper2} strength={zone_str2:.0f}")

# Strategy says bearish trend, demand zone, no confirmation
# Review as potential SELL (bearish trend) or BUY (demand zone)
for side in ["SELL", "BUY"]:
    result = review_trade(
        symbol="GBPCHF", side=side, lots=0.1,
        sl=None, tp=None,
        plan={"action": side, "valid": False, "confidence": 82,
               "zone_strength": zone_str2, "zone_type": zone_type2,
               "zone_upper": zone_upper2, "zone_lower": zone_lower2,
               "htf_trend": htf2, "reason": f"no confirmation candle for {side}"},
        health=health2,
    )
    print(f"  {side} >>> APPROVED: {result.approved} | {result.reason[:120]}")
    if result.approved:
        for o in (result.alpha.objections or [])[:2]:
            print(f"      alpha: {o}")
        for o in (result.beta.objections or [])[:2]:
            print(f"      beta: {o}")
print()

# NZDCAD
print("=== NZDCAD ===")
mh3 = market_health("NZDCAD")
health3 = mh3.get("health_score", 0) if isinstance(mh3, dict) else 0
regime3 = mh3.get("regime", "?") if isinstance(mh3, dict) else "?"
plan3 = evaluate_strategy("NZDCAD")
zone_str3 = getattr(plan3, "zone_strength", 0)
zone_type3 = getattr(plan3, "zone_type", "")
zone_upper3 = getattr(plan3, "zone_upper", None)
zone_lower3 = getattr(plan3, "zone_lower", None)
htf3 = getattr(plan3, "htf_trend", "?")

print(f"  Health={health3:.1f} regime={regime3} htf_trend={htf3}")
print(f"  Zone: {zone_type3} {zone_lower3}-{zone_upper3} strength={zone_str3:.0f}")

for side in ["SELL", "BUY"]:
    result = review_trade(
        symbol="NZDCAD", side=side, lots=0.1,
        sl=None, tp=None,
        plan={"action": side, "valid": False, "confidence": 97,
               "zone_strength": zone_str3, "zone_type": zone_type3,
               "zone_upper": zone_upper3, "zone_lower": zone_lower3,
               "htf_trend": htf3, "reason": f"no confirmation candle for {side}"},
        health=health3,
    )
    print(f"  {side} >>> APPROVED: {result.approved} | {result.reason[:120]}")
    if result.approved:
        for o in (result.alpha.objections or [])[:2]:
            print(f"      alpha: {o}")
        for o in (result.beta.objections or [])[:2]:
            print(f"      beta: {o}")

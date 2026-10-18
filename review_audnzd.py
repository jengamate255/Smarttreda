"""Dual review AUDNZD."""
import sys, json
sys.path.insert(0, "E:/Project/Mt5 Ea")
from dotenv import load_dotenv
load_dotenv("E:/Project/Mt5 Ea/.env")
from mt5_mcp.dual_review import review_trade
from mt5_mcp.strategy import evaluate_strategy
from mt5_mcp.market_intel import market_health

plan = evaluate_strategy("AUDNZD")
mh = market_health("AUDNZD")
health = mh.get("health_score", 0) if isinstance(mh, dict) else 0
lots = getattr(plan, "lots", 0.12) or 0.12
sl = getattr(plan, "sl", None)
tp = getattr(plan, "tp", None)
entry = getattr(plan, "entry", None)
rr = getattr(plan, "risk_reward", None)
score = getattr(plan, "trade_score", 0)

print(f"AUDNZD BUY health={health:.1f} lots={lots} entry={entry} sl={sl} tp={tp} rr={rr} score={score}")
print("Running dual-review (~40s)...")

result = review_trade(
    symbol="AUDNZD", side="BUY", lots=lots, sl=sl, tp=tp,
    plan=plan.__dict__ if hasattr(plan, "__dict__") else {},
    health=health,
)

print(f"APPROVED: {result.approved}")
print(f"Reason: {result.reason}")
print(f"Alpha: {result.alpha.decision} (conf {result.alpha.confidence:.0f})")
for o in (result.alpha.objections or [])[:3]:
    print(f"  alpha: {o}")
for c in (result.alpha.conditions or [])[:3]:
    print(f"  alpha-cond: {c}")
print(f"Beta: {result.beta.decision} (conf {result.beta.confidence:.0f})")
for o in (result.beta.objections or [])[:3]:
    print(f"  beta: {o}")
for c in (result.beta.conditions or [])[:3]:
    print(f"  beta-cond: {c}")

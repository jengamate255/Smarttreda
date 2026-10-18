"""Test dual-review on GBPCHF."""
import sys
sys.path.insert(0, "E:/Project/Mt5 Ea")
from dotenv import load_dotenv
load_dotenv("E:/Project/Mt5 Ea/.env")
from mt5_mcp.strategy import evaluate_strategy
from mt5_mcp.market_intel import market_health
from mt5_mcp.dual_review import review_trade

p = evaluate_strategy("GBPCHF")
mh = market_health("GBPCHF")
health = mh.get("health_score", 0) if isinstance(mh, dict) else 0
lots = p.lots or 0.1

print(f"GBPCHF: {p.action} lots={lots} entry={p.entry} sl={p.sl} tp={p.tp} rr={p.risk_reward}")
print(f"  confidence={p.confidence} score={p.trade_score} zone={p.zone_strength}")
print(f"  valid={p.valid} htf={p.htf_trend}")

result = review_trade(
    symbol="GBPCHF", side="BUY", lots=lots,
    sl=p.sl, tp=p.tp,
    plan=p.model_dump(), health=health,
    account={"balance": 9644, "open_positions": 1},
)

print(f"\nAPPROVED: {result.approved}")
print(f"Reason: {result.reason[:300]}")
print(f"Alpha: {result.alpha.decision} (conf={result.alpha.confidence})")
if result.alpha.objections:
    print(f"  Objections: {result.alpha.objections[0][:200]}")
print(f"Beta: {result.beta.decision} (conf={result.beta.confidence})")
if result.beta.objections:
    print(f"  Objections: {result.beta.objections[0][:200]}")

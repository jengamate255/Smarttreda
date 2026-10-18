"""Debug dual-review parsing."""
import sys, json, re
sys.path.insert(0, "E:/Project/Mt5 Ea")
from dotenv import load_dotenv
load_dotenv("E:/Project/Mt5 Ea/.env")
from mt5_mcp.groq_llm import generate
from mt5_mcp.dual_review import _parse_verdict, ALPHA_SYSTEM, BETA_SYSTEM, _fill

evidence = json.dumps({
    "proposed_order": {"symbol": "AUDCAD", "side": "BUY", "lots": 0.1, "sl": 0.99601, "tp": 1.00783},
    "fresh_bot_plan": {"action": "BUY", "valid": True, "confidence": 88, "trade_score": 99, "risk_reward": 3.03, "zone_strength": 85, "confirmation": "outside_bar (bullish, conf 0.74)"},
    "fresh_health_score": 88,
    "account": {"balance": 9644, "open_positions": 1},
    "floors": {"confidence": 75, "score": 70, "rr": 3.0, "health": 55, "max_bar_age_h": 24},
}, default=str)

system_prompt = _fill(ALPHA_SYSTEM)
user_msg = "PROPOSED TRADE + FRESH EVIDENCE:\n" + evidence

print("Calling Alpha...")
resp = generate(
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ],
    model="qwen/qwen3.8-27b",
    temperature=0.2,
    max_tokens=300,
)
print("Raw output:")
print(repr(resp.content[:500]))
print()

verdict = _parse_verdict("ALPHA", resp.content, 1.0)
print(f"Parsed: decision={verdict.decision} conf={verdict.confidence}")
print(f"Objections: {verdict.objections}")
print(f"Conditions: {verdict.conditions}")
print(f"Rationale: {verdict.rationale[:200]}")

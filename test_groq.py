"""Test dual-review with qwen3-8b."""
import sys
sys.path.insert(0, "E:/Project/Mt5 Ea")
from dotenv import load_dotenv
load_dotenv("E:/Project/Mt5 Ea/.env")
from mt5_mcp.groq_llm import generate

resp = generate(
    messages=[
        {"role": "system", "content": 'You are a trading gate agent. Output STRICT JSON only, no other text: {"decision": "APPROVE" or "REJECT", "confidence": 0-100, "objections": ["..."], "conditions": ["..."], "rationale": "..."}'},
        {"role": "user", "content": "Evaluate this trade: BUY AUDCAD health=88 score=99 zone=85 confirm=outside_bar entry=0.99883 sl=0.99601 tp=1.00783 rr=3.05 regime=trending_bullish. Hard rules: REJECT if health<75, regime not trending, zone<70, rr<3.0, no confirmation."}
    ],
    model="qwen/qwen3.8-27b",
    temperature=0.2,
    max_tokens=300,
)
print("Raw:", repr(resp.content[:400]))

# Test parsing
import json, re
text = resp.content.strip()
text = re.sub(r"^```(?:json)?\s*", "", text)
text = re.sub(r"\s*```$", "", text)
try:
    data = json.loads(text)
    print("Parsed:", data)
except:
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if m:
        candidate = m.group(0)
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            data = json.loads(candidate)
            print("Parsed (extracted):", data)
        except Exception as e:
            print("Parse failed:", e)
            print("Candidate:", candidate[:200])
    else:
        print("No JSON found")

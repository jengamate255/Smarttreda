"""Full market analysis with strategy evaluation."""
import sys, json
sys.path.insert(0, "E:/Project/Mt5 Ea")
from dotenv import load_dotenv
load_dotenv("E:/Project/Mt5 Ea/.env")
from mt5_mcp.client import get_client
from mt5_mcp.market_intel import market_health, scan_markets
from mt5_mcp.strategy import evaluate_strategy

client = get_client()
client.connect()

# Current positions
positions = client.positions()
print("=== OPEN POSITIONS ===")
for p in positions:
    sym = p["symbol"]
    side = p["type"]
    vol = p["volume"]
    entry = p["price_open"]
    pnl = p["profit"]
    sl = p.get("sl", 0)
    tp = p.get("tp", 0)
    print(f"  {sym} {side} {vol} @ {entry} SL={sl} TP={tp} PnL={pnl:.2f}")
if not positions:
    print("  (none)")
print()

# Scan forex pairs
forex_pairs = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
    "EURGBP", "EURJPY", "GBPJPY", "CHFJPY", "AUDJPY", "EURAUD", "EURNZD",
    "GBPAUD", "GBPNZD", "AUDCAD", "AUDCHF", "AUDNZD", "CADJPY", "CADCHF",
    "NZDJPY", "NZDCAD", "NZDCHF", "EURCHF", "GBPCHF", "GBPCAD", "GBPCAD",
    "EURCAD", "USDSGD", "USDHKD", "USDMXN",
]

print("=== FOREX SCAN ===")
results = []
for sym in forex_pairs:
    try:
        mh = market_health(sym)
        if isinstance(mh, dict):
            hs = mh.get("health_score", 0)
            verdict = mh.get("verdict", "?")
            regime = mh.get("regime", "?")
            trend = mh.get("trend", "?")
            tc = mh.get("trend_confidence", 0)
            spread_note = ""
            for c in mh.get("components", []):
                if c["name"] == "spread":
                    spread_note = c.get("note", "")
            results.append({
                "symbol": sym, "health": hs, "verdict": verdict,
                "regime": regime, "trend": trend, "tc": tc, "spread": spread_note
            })
    except Exception as e:
        print(f"  {sym}: ERROR - {str(e)[:80]}")

# Sort by health descending
results.sort(key=lambda x: x["health"], reverse=True)
for r in results:
    print(f"  {r['symbol']:10s} health={r['health']:5.1f}  verdict={r['verdict']:10s}  regime={r['regime']:18s}  trend={r['trend']:10s}  tc={r['tc']:.0f}%  {r['spread']}")
print()

# Pick best candidates
top = [r for r in results if r["health"] >= 60 and r["verdict"] in ("strong", "moderate", "cautious")]
print(f"=== TOP CANDIDATES (health>=60, not blocked) ===")
if not top:
    top = results[:3]
    print("  (none met strict criteria, showing top 3 by health)")

for r in top[:3]:
    sym = r["symbol"]
    print(f"\n--- {sym} ---")
    plan = evaluate_strategy(sym)
    if hasattr(plan, "__dict__"):
        d = plan.__dict__
    elif isinstance(plan, dict):
        d = plan
    else:
        d = {"raw": str(plan)}
    for k, v in d.items():
        if k in ("raw",):
            print(f"  {k}: {v}")
        elif isinstance(v, (list, dict)):
            print(f"  {k}: {json.dumps(v, indent=2, default=str)[:300]}")
        else:
            print(f"  {k}: {v}")

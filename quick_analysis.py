import os
os.environ["MT5_MCP_ACCOUNT"] = "PROP"

from mt5_mcp.client import get_client
from mt5_mcp.strategy import evaluate_strategy
import json

c = get_client()
if not c:
    print("Failed to connect to MT5")
    exit(1)

print("Connected to MT5 PROP account")
print()

# Symbols to analyze
symbols = ["EURUSD", "USDJPY", "XAUUSD", "SPX500", "JP225", "ETHUSD", "XAGUSD", "AUDUSD", "EURJPY"]

for sym in symbols:
    try:
        plan = evaluate_strategy(sym)
        if plan:
            action = plan.get("action", "?")
            entry = plan.get("entry", 0)
            sl = plan.get("sl", 0)
            tp = plan.get("tp", 0)
            rr = plan.get("risk_reward", 0)
            conf = plan.get("confidence", 0)
            score = plan.get("trade_score", 0)
            valid = plan.get("valid", False)
            htf = plan.get("htf_trend", "?")
            zone_type = plan.get("zone_type", "?")
            zone_score = plan.get("zone_score", 0)
            
            status = "VALID" if valid else "NO SETUP"
            print(f"{sym:10s} | {action:4s} | Entry={entry:.5f} | SL={sl:.5f} | TP={tp:.5f} | RR={rr:.1f} | Conf={conf:.0f}% | Score={score:.0f} | HTF={htf} | Zone={zone_type}({zone_score:.0f}) | {status}")
        else:
            print(f"{sym:10s} | No plan returned")
    except Exception as e:
        print(f"{sym:10s} | Error: {e}")

print()
print("Analysis complete.")

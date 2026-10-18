"""Scan tradable pairs for current setups."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

from mt5_mcp.config import reload_settings
from mt5_mcp.compliance import evaluate as compliance_status
from mt5_mcp.client import get_client
from mt5_mcp.strategy import evaluate_strategy

s = reload_settings()
client = get_client()

# Health check
acct = client.account_info()
print("=== ACCOUNT ===")
print(f"Balance: ${acct.get('balance', 0):.2f}")
print(f"Equity: ${acct.get('equity', 0):.2f}")
print(f"Margin: ${acct.get('margin', 0):.2f}")
print(f"Free margin: ${acct.get('margin_free', 0):.2f}")

# Compliance status
status = compliance_status()
print("\n=== COMPLIANCE ===")
print(f"Mode: {status.get('mode', 'unknown')}")
print(f"Status: {status.get('status', 'unknown')}")
print(f"Trades today: {status.get('trades_today', 0)}/{status.get('max_trades_per_day', 5)}")
print(f"Open risk: ${status.get('open_risk', 0):.2f}")
print(f"Consecutive losses: {status.get('consecutive_losses', 0)}")

# Tradable symbols
tradable = ["EURUSD", "SPX500", "JP225", "ETHUSD", "USOIL"]
print("\n=== SCANNING TRADABLE PAIRS ===")

for symbol in tradable:
    print(f"\n--- {symbol} ---")
    try:
        plan = evaluate_strategy(symbol)
        if plan is None:
            print("  No plan returned")
            continue

        # Handle both dict and object
        if isinstance(plan, dict):
            trend = plan.get("trend", "unknown")
            zone_score = plan.get("zone_score", 0)
            trade_score = plan.get("trade_score", 0)
            confidence = plan.get("confidence", 0)
            action = plan.get("action", "none")
            entry = plan.get("entry_price", 0)
            sl = plan.get("sl_price", 0)
            tp = plan.get("tp_price", 0)
            rr = plan.get("rr_ratio", 0)
            valid = plan.get("valid", False)
            confirmation = plan.get("confirmation", "none")
            reasons = plan.get("reasons", [])
        else:
            trend = getattr(plan, "trend", "unknown")
            zone_score = getattr(plan, "zone_score", 0)
            trade_score = getattr(plan, "trade_score", 0)
            confidence = getattr(plan, "confidence", 0)
            action = getattr(plan, "action", "none")
            entry = getattr(plan, "entry_price", 0)
            sl = getattr(plan, "sl_price", 0)
            tp = getattr(plan, "tp_price", 0)
            rr = getattr(plan, "rr_ratio", 0)
            valid = getattr(plan, "valid", False)
            confirmation = getattr(plan, "confirmation", "none")
            reasons = getattr(plan, "reasons", [])

        print(f"  Trend: {trend}")
        print(f"  Zone score: {zone_score}")
        print(f"  Trade score: {trade_score}")
        print(f"  Confidence: {confidence:.0f}%")
        print(f"  Action: {action}")
        print(f"  Entry: {entry:.5f}")
        print(f"  SL: {sl:.5f}")
        print(f"  TP: {tp:.5f}")
        print(f"  RR: {rr:.2f}")
        print(f"  Valid: {valid}")
        print(f"  Confirmation: {confirmation}")
        if reasons:
            print(f"  Reasons: {reasons}")

    except Exception as e:
        print(f"  Error: {e}")

"""Detailed scan of EURUSD and ETHUSD setups."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

from mt5_mcp.client import get_client
from mt5_mcp.strategy import evaluate_strategy
from mt5_mcp.strategy import trend_analysis

client = get_client()

for symbol in ["EURUSD", "ETHUSD"]:
    print(f"\n{'='*50}")
    print(f"=== {symbol} DETAILED ANALYSIS ===")
    print(f"{'='*50}")

    # Trend analysis
    try:
        trend = trend_analysis(symbol)
        print(f"\nTrend: {trend.get('trend', 'unknown')}")
        print(f"H1 EMA fast: {trend.get('h1_ema_fast', 0):.5f}")
        print(f"H1 EMA slow: {trend.get('h1_ema_slow', 0):.5f}")
        print(f"H4 EMA fast: {trend.get('h4_ema_fast', 0):.5f}")
        print(f"H4 EMA slow: {trend.get('h4_ema_slow', 0):.5f}")
        print(f"H1 RSI: {trend.get('h1_rsi', 0):.1f}")
        print(f"H4 RSI: {trend.get('h4_rsi', 0):.1f}")
    except Exception as e:
        print(f"Trend error: {e}")

    # Strategy evaluation
    try:
        plan = evaluate_strategy(symbol)
        if plan:
            if isinstance(plan, dict):
                for k, v in plan.items():
                    print(f"  {k}: {v}")
            else:
                print(f"  Plan object: {plan}")
    except Exception as e:
        print(f"Strategy error: {e}")

    # Current price
    try:
        tick = client.tick(symbol)
        bid = tick.get("bid", 0)
        ask = tick.get("ask", 0)
        print(f"\nCurrent price: Bid={bid:.5f} Ask={ask:.5f}")
    except Exception as e:
        print(f"Tick error: {e}")

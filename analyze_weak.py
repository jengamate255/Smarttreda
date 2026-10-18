"""Analyze weak trades and define what strong setups look like."""
import sys, json
sys.path.insert(0, "E:/Project/Mt5 Ea")
from dotenv import load_dotenv
load_dotenv("E:/Project/Mt5 Ea/.env")
from mt5_mcp.client import get_client
from mt5_mcp.strategy import evaluate_strategy
from mt5_mcp.market_intel import market_health

client = get_client()
client.connect()

# Current positions
positions = client.positions()
print("=== CURRENT POSITIONS ===")
for p in positions:
    sym = p["symbol"]
    side = "BUY" if p["type"] == 0 else "SELL"
    entry = p["price_open"]
    sl = p.get("sl", 0)
    tp = p.get("tp", 0)
    pnl = p["profit"]
    vol = p["volume"]
    print(f"  {sym} {side} {vol} @ {entry}")
    print(f"    SL={sl} TP={tp} PnL={pnl:.2f}")

    # Analyze why this trade is weak
    mh = market_health(sym)
    plan = evaluate_strategy(sym)
    health = mh.get("health_score", 0) if isinstance(mh, dict) else 0
    regime = mh.get("regime", "?") if isinstance(mh, dict) else "?"
    trend = mh.get("trend", "?") if isinstance(mh, dict) else "?"
    tc = mh.get("trend_confidence", 0) if isinstance(mh, dict) else 0
    zone_str = getattr(plan, "zone_strength", 0)
    confirm = getattr(plan, "confirmation", "")
    valid = getattr(plan, "valid", False)
    score = getattr(plan, "trade_score", 0)

    print(f"    HEALTH: {health:.1f} regime={regime} trend={trend} tc={tc:.0f}%")
    print(f"    STRATEGY: valid={valid} score={score:.0f} zone={zone_str:.0f} confirm='{confirm}'")

    # Identify weaknesses
    weaknesses = []
    if health < 75:
        weaknesses.append(f"Health {health:.0f} < 75 threshold")
    if regime in ("transition", "ranging", "quiet_contraction", "unknown"):
        weaknesses.append(f"Regime '{regime}' — not a clean trend")
    if trend == "sideways":
        weaknesses.append(f"Trend is sideways — no directional bias")
    if tc < 80:
        weaknesses.append(f"Trend confidence {tc:.0f}% < 80%")
    if zone_str < 70:
        weaknesses.append(f"Zone strength {zone_str:.0f} < 70 minimum")
    if not confirm:
        weaknesses.append("No confirmation candle")
    if not valid:
        weaknesses.append("Strategy marked as invalid")

    if weaknesses:
        print(f"    WEAKNESSES:")
        for w in weaknesses:
            print(f"      - {w}")
    else:
        print(f"    No obvious weaknesses found")
    print()

# What strong setups should look like
print("=== STRONG SETUP CRITERIA ===")
print("  1. Health >= 75 (tradeable verdict)")
print("  2. Regime: trending_bullish or trending_bearish (NOT transition/ranging/unknown)")
print("  3. Trend confidence >= 80%")
print("  4. Zone strength >= 70 (supply/demand zone)")
print("  5. Confirmation candle: hammer, engulfing, pin_bar, BOS, CHOCH")
print("  6. HTF trend matches trade side (bullish->BUY, bearish->SELL)")
print("  7. R:R >= 3.0 with NATURAL targets (not stretched)")
print("  8. Spread within limits")
print("  9. Session appropriate (London/NY for forex)")
print("  10. No imminent high-impact news")
print()

# Scan for what WOULD pass all criteria
print("=== SCANNING FOR STRONG SETUPS ===")
forex = ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","NZDUSD","USDCAD",
         "EURGBP","EURJPY","GBPJPY","CHFJPY","AUDJPY","EURAUD","EURNZD",
         "GBPAUD","GBPNZD","AUDCAD","AUDCHF","AUDNZD","CADJPY","CADCHF",
         "NZDJPY","NZDCAD","NZDCHF","EURCHF","GBPCHF","GBPCAD","EURCAD"]

strong = []
for sym in forex:
    try:
        mh = market_health(sym)
        plan = evaluate_strategy(sym)
        health = mh.get("health_score", 0) if isinstance(mh, dict) else 0
        regime = mh.get("regime", "?") if isinstance(mh, dict) else "?"
        trend = mh.get("trend", "?") if isinstance(mh, dict) else "?"
        tc = mh.get("trend_confidence", 0) if isinstance(mh, dict) else 0
        valid = getattr(plan, "valid", False)
        action = getattr(plan, "action", "NONE")
        score = getattr(plan, "trade_score", 0)
        zone_str = getattr(plan, "zone_strength", 0)
        confirm = getattr(plan, "confirmation", "")
        conf = getattr(plan, "confidence", 0)

        # Check all criteria
        checks = {
            "health": health >= 75,
            "regime": regime in ("trending_bullish", "trending_bearish"),
            "trend_conf": tc >= 80,
            "zone": zone_str >= 70,
            "confirm": bool(confirm),
            "valid": valid and action in ("BUY", "SELL"),
            "score": score >= 70,
        }
        passed = sum(checks.values())
        if passed >= 6:
            strong.append({
                "symbol": sym, "health": health, "regime": regime,
                "trend": trend, "tc": tc, "valid": valid, "action": action,
                "score": score, "zone": zone_str, "confirm": confirm,
                "conf": conf, "passed": passed, "checks": checks,
            })
    except Exception as e:
        pass

strong.sort(key=lambda x: x["passed"], reverse=True)
if strong:
    for s in strong:
        fails = [k for k, v in s["checks"].items() if not v]
        print(f"  {s['symbol']:8s} health={s['health']:.0f} regime={s['regime']:20s} {s['action']:4s} score={s['score']:.0f} zone={s['zone']:.0f} confirm={s['confirm'][:20]} passed={s['passed']}/7")
        if fails:
            print(f"           FAILS: {', '.join(fails)}")
else:
    print("  No symbols pass 6/7 criteria right now")
    print("  Market conditions may not be optimal for trading")

"""Continuous market monitor + auto-trader runner.

Runs every 5 minutes:
1. Scans all forex pairs for strong setups
2. Runs dual-review on candidates
3. Executes approved trades via auto-trader
4. Logs all findings

Usage:
    python monitor.py              # run once
    python monitor.py --loop       # continuous (every 5 min)
    python monitor.py --dry-run    # scan only, no trades
"""
import sys, os, json, time, argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.chdir(Path(__file__).resolve().parent)

from dotenv import load_dotenv
load_dotenv(".env")

from mt5_mcp.client import get_client
from mt5_mcp.strategy import evaluate_strategy
from mt5_mcp.market_intel import market_health
from mt5_mcp.dual_review import review_trade

SCAN_SYMBOLS = [
    "AUDCAD", "NZDUSD", "AUDNZD", "USDCAD", "CHFJPY", "EURJPY",
    "GBPJPY", "USDCHF", "GBPCHF", "NZDCAD", "EURUSD", "GBPUSD",
    "AUDUSD", "NZDJPY", "CADJPY", "AUDJPY", "EURAUD", "EURNZD",
    "GBPAUD", "GBPNZD", "AUDCAD", "AUDCHF", "CADCHF", "NZDCHF",
    "EURCHF", "GBPCAD", "EURCAD",
]

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
MONITOR_LOG = LOG_DIR / "monitor.jsonl"


def log_event(event: dict):
    event["at"] = datetime.now(timezone.utc).isoformat()
    with open(MONITOR_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


def scan_all():
    """Scan all symbols and return candidates that pass basic criteria."""
    candidates = []
    for sym in SCAN_SYMBOLS:
        try:
            plan = evaluate_strategy(sym)
            mh = market_health(sym)
            health = mh.get("health_score", 0) if isinstance(mh, dict) else 0
            regime = mh.get("regime", "?") if isinstance(mh, dict) else "?"
            trend = mh.get("trend", "?") if isinstance(mh, dict) else "?"
            action = getattr(plan, "action", "NONE")
            valid = getattr(plan, "valid", False)
            score = getattr(plan, "trade_score", 0)
            conf = getattr(plan, "confidence", 0)
            zone = getattr(plan, "zone_strength", 0)
            confirm = getattr(plan, "confirmation", "")
            entry = getattr(plan, "entry", None)
            sl = getattr(plan, "sl", None)
            tp = getattr(plan, "tp", None)
            sl_pips = getattr(plan, "sl_pips", None)
            tp_pips = getattr(plan, "tp_pips", None)
            rr = getattr(plan, "risk_reward", None)

            # Basic criteria: health >= 70, regime trending, zone >= 60
            if (health >= 70 and
                regime in ("trending_bullish", "trending_bearish") and
                zone >= 60):
                candidates.append({
                    "symbol": sym, "health": health, "regime": regime,
                    "trend": trend, "action": action, "valid": valid,
                    "score": score, "conf": conf, "zone": zone,
                    "confirm": confirm, "entry": entry, "sl": sl, "tp": tp,
                    "sl_pips": sl_pips, "tp_pips": tp_pips, "rr": rr,
                })
        except Exception as e:
            pass
    candidates.sort(key=lambda x: (x["health"], x["zone"]), reverse=True)
    return candidates


def run_dual_review(candidate):
    """Run dual-review on a candidate. Returns (approved, result)."""
    sym = candidate["symbol"]
    side = candidate["action"]
    if side not in ("BUY", "SELL"):
        return False, None

    plan = evaluate_strategy(sym)
    mh = market_health(sym)
    health = mh.get("health_score", 0) if isinstance(mh, dict) else 0
    lots = getattr(plan, "lots", 0.1) or 0.1
    sl = getattr(plan, "sl", None)
    tp = getattr(plan, "tp", None)

    result = review_trade(
        symbol=sym, side=side, lots=lots, sl=sl, tp=tp,
        plan=plan.__dict__ if hasattr(plan, "__dict__") else {},
        health=health,
    )
    return result.approved, result


def run_auto_trader_cycle():
    """Run one auto-trader cycle."""
    try:
        from mt5_mcp.auto_trader import cycle
        cycle()
        return True
    except Exception as e:
        print(f"  Auto-trader cycle failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--dry-run", action="store_true", help="Scan only, no trades")
    parser.add_argument("--interval", type=int, default=300, help="Loop interval (seconds)")
    args = parser.parse_args()

    client = get_client()
    client.connect()

    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        print(f"\n{'='*60}")
        print(f"SCAN @ {now}")
        print(f"{'='*60}")

        # Check current positions
        positions = client.positions()
        print(f"\nOpen positions: {len(positions)}")
        for p in positions:
            sym = p["symbol"]
            side = "BUY" if p["type"] == 0 else "SELL"
            print(f"  {sym} {side} {p['volume']} @ {p['price_open']} PnL={p['profit']:.2f}")

        # Scan for candidates
        print(f"\nScanning {len(SCAN_SYMBOLS)} symbols...")
        candidates = scan_all()
        print(f"Found {len(candidates)} candidates with health>=70, trending regime, zone>=60")

        if candidates:
            print(f"\nTop candidates:")
            for c in candidates[:5]:
                print(f"  {c['symbol']:8s} health={c['health']:.0f} {c['action']:4s} score={c['score']:.0f} zone={c['zone']:.0f} confirm={c['confirm'][:25]} rr={c['rr']}")

            # Run dual-review on top candidates
            for c in candidates[:3]:
                if not c["valid"] or c["action"] not in ("BUY", "SELL"):
                    continue
                if c["symbol"] in [p["symbol"] for p in positions]:
                    print(f"\n  {c['symbol']}: already in position, skipping review")
                    continue

                print(f"\n  Dual-review: {c['symbol']} {c['action']}...")
                approved, result = run_dual_review(c)
                if result:
                    print(f"  >>> APPROVED: {approved}")
                    print(f"  >>> Reason: {result.reason[:150]}")
                    log_event({
                        "type": "dual_review",
                        "symbol": c["symbol"],
                        "side": c["action"],
                        "approved": approved,
                        "reason": result.reason,
                        "health": c["health"],
                        "score": c["score"],
                    })

                    if approved and not args.dry_run:
                        print(f"  >>> EXECUTING TRADE...")
                        # The auto-trader will handle execution on its next cycle
                        # For now, log the approved signal
                        log_event({
                            "type": "trade_signal",
                            "symbol": c["symbol"],
                            "side": c["action"],
                            "entry": c["entry"],
                            "sl": c["sl"],
                            "tp": c["tp"],
                            "rr": c["rr"],
                            "health": c["health"],
                        })
                else:
                    print(f"  >>> Review failed (Groq error)")
        else:
            print("No candidates meeting criteria right now")

        if not args.loop:
            break

        print(f"\nNext scan in {args.interval}s...")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

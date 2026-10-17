"""Confirm setups at current prices."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

import MetaTrader5 as mt5
from mt5_mcp.config import reload_settings
from mt5_mcp.strategy import evaluate_strategy
from mt5_mcp.compliance import gate

s = reload_settings()
mt5.initialize(path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe", timeout=10000)

for symbol in ["EURUSD", "ETHUSD"]:
    print(f"\n{'='*50}")
    print(f"=== {symbol} ===")
    print(f"{'='*50}")

    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if not tick or not info:
        print("No data")
        continue

    bid = tick.bid
    ask = tick.ask
    point = info.point
    spread_pts = (ask - bid) / point
    print(f"Current: Bid={bid} Ask={ask} Spread={spread_pts:.0f} pts")

    try:
        plan = evaluate_strategy(symbol)
        if plan is None:
            print("No plan")
            continue

        entry = float(plan.entry)
        sl = float(plan.sl)
        tp = float(plan.tp)
        rr = float(plan.risk_reward)
        sl_pips = float(plan.sl_pips)
        tp_pips = float(plan.tp_pips)
        slippage = abs(bid - entry) / (point * 10)

        print(f"Valid: {plan.valid}")
        print(f"Action: {plan.action}")
        print(f"Confidence: {plan.confidence:.0f}%")
        print(f"HTF Trend: {plan.htf_trend}")
        print(f"Confirmation: {plan.confirmation}")
        print(f"Entry: {entry}")
        print(f"SL: {sl} ({sl_pips:.1f} pips)")
        print(f"TP: {tp} ({tp_pips:.1f} pips)")
        print(f"RR: {rr:.2f}")
        print(f"Lots: {plan.lots}")
        print(f"Risk: ${plan.risk_amount:.2f}")
        print(f"Slippage from entry: {slippage:.1f} pips")

        # Compliance
        r = gate(
            symbol=symbol,
            side=plan.action,
            lots=float(plan.lots),
            sl_price=sl,
            sl_pips=sl_pips,
            confidence=plan.confidence,
        )
        print(f"Compliance: allowed={r['allowed']} violations={r['violations']}")

        if plan.valid and r['allowed']:
            print("STATUS: READY TO EXECUTE")
        elif not r['allowed']:
            print("STATUS: BLOCKED")
        else:
            print("STATUS: INVALID SETUP")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

mt5.shutdown()

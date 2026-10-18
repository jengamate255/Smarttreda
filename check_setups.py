#!/usr/bin/env python3
"""Check prop firm account and scan for potential setups."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
sys.path.insert(0, r"E:\Project\Mt5 Ea\mt5_mcp")

from mt5_mcp.client import get_client
from mt5_mcp.multi_strategy import STRATEGY_ROUTING
from mt5_mcp.auto_trader import SYMBOL_CONFIG, _plan_for
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

client = get_client()
account = client.account_info()
print(f"\n{'='*60}")
print(f"Account: {account.get('login')} @ {account.get('server')}")
print(f"Balance: ${float(account.get('balance', 0)):.2f}")
print(f"Equity: ${float(account.get('equity', 0)):.2f}")
positions = client.positions()
print(f"Open positions: {len(positions)}")
for p in positions:
    sym = p.get('symbol', 'unknown')
    ticket = p.get('ticket', '?')
    ptype = p.get('type', '?')
    vol = p.get('volume', 0)
    print(f"  {sym} ticket={ticket} {ptype} {vol} lots")
print(f"{'='*60}\n")

print("Scanning 8 symbols for setups...\n")
for symbol in SYMBOL_CONFIG:
    strategy = STRATEGY_ROUTING.get(symbol, "trend_zone")
    print(f"{symbol} ({strategy}):")
    try:
        plan = _plan_for(symbol, risk_percent=0.25)
        if plan and hasattr(plan, 'avail') and plan.avail:
            print(f"  [OK] SETUP: {plan.side} @ {plan.entry_price}")
            print(f"     SL={plan.sl_price} TP={plan.tp_price}")
            print(f"     Confidence={plan.confidence} Score={plan.trade_score}")
        else:
            reason = getattr(plan, 'reason', 'no setup') if plan else "no plan"
            print(f"  [NO] {reason}")
    except Exception as e:
        print(f"  [ERR] {type(e).__name__}: {str(e)[:80]}")
    print()

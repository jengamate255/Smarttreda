#!/usr/bin/env python3
"""Detailed scan for ETHUSD and USOIL setups."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
sys.path.insert(0, r"E:\Project\Mt5 Ea\mt5_mcp")

from mt5_mcp.client import get_client
from mt5_mcp.auto_trader import _plan_for
import logging

logging.basicConfig(level=logging.WARNING)

client = get_client()

for symbol in ["ETHUSD", "USOIL"]:
    print(f"\n{'='*60}")
    print(f"{symbol} - Detailed Analysis")
    print(f"{'='*60}")
    plan = _plan_for(symbol, risk_percent=0.25)
    
    print(f"Plan object: {type(plan).__name__}")
    
    # Print all attributes first
    print(f"\nAll attributes:")
    for attr in dir(plan):
        if not attr.startswith('_'):
            try:
                val = getattr(plan, attr)
                if not callable(val):
                    print(f"  {attr}: {val}")
            except Exception as e:
                print(f"  {attr}: ERROR - {e}")

"""
Paper Trading / Diagnostic Mode
Simulates the complete trading pipeline without sending real orders.
Records every decision for analysis.
"""
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, "E:/Project/Mt5 Ea")

import MetaTrader5 as mt5
import numpy as np

# Initialize MT5
if not mt5.initialize():
    print(f"MT5 initialization failed: {mt5.last_error()}")
    sys.exit(1)

print("=" * 80)
print("PAPER TRADING DIAGNOSTIC MODE")
print("=" * 80)
print(f"Timestamp: {datetime.now()}")
print(f"Mode: SIMULATION (no real orders)")
print()

acct = mt5.account_info()
if acct:
    print(f"Account: {acct.login}")
    print(f"Server: {acct.server}")
    print(f"Balance: ${acct.balance:.2f}")
    print(f"Equity: ${acct.equity:.2f}")
print()

# Load configuration
from mt5_mcp.config import get_settings
from mt5_mcp.compliance import gate as compliance_gate
from mt5_mcp.consensus import check as consensus_check
from mt5_mcp.strategy import evaluate_strategy

settings = get_settings()

# Decision log
decisions = []
stats = defaultdict(int)

def log_decision(symbol, stage, passed, reason=None, details=None):
    """Log a pipeline decision."""
    decision = {
        "timestamp": datetime.now().isoformat(),
        "symbol": symbol,
        "stage": stage,
        "passed": passed,
        "reason": reason,
        "details": details or {},
    }
    decisions.append(decision)
    
    if passed:
        stats[f"{stage}_pass"] += 1
    else:
        stats[f"{stage}_fail"] += 1
        stats[f"{stage}_fail_{reason}"] += 1

# Test symbols
from trade_desk import UNIVERSE as DESK_UNIVERSE
test_symbols = DESK_UNIVERSE[:10]  # Test first 10

print(f"Testing {len(test_symbols)} symbols: {', '.join(test_symbols[:5])}...")
print()

# Pipeline simulation
print("PIPELINE SIMULATION")
print("-" * 80)

for symbol in test_symbols:
    print(f"\n{symbol}:")
    
    # Stage 1: Market data
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 100)
    if rates is None or len(rates) == 0:
        log_decision(symbol, "market_data", False, "no_data")
        print(f"  ✗ Market data: NO DATA")
        continue
    log_decision(symbol, "market_data", True)
    print(f"  ✓ Market data: {len(rates)} bars")
    
    # Stage 2: Strategy evaluation
    try:
        result = evaluate_strategy(symbol)
        if not result.valid:
            log_decision(symbol, "strategy", False, result.reason or "invalid", {
                "confidence": result.confidence,
                "trade_score": result.trade_score,
                "zone_strength": result.zone_strength,
            })
            print(f"  ✗ Strategy: {result.reason}")
            continue
        log_decision(symbol, "strategy", True, details={
            "direction": result.direction,
            "confidence": result.confidence,
            "trade_score": result.trade_score,
        })
        print(f"  ✓ Strategy: {result.direction} conf={result.confidence}% score={result.trade_score}")
    except Exception as e:
        log_decision(symbol, "strategy", False, "error", {"error": str(e)})
        print(f"  ✗ Strategy: ERROR - {e}")
        continue
    
    # Stage 3: Compliance gate
    sl_pips = 25.0  # Typical SL
    compliance_result = compliance_gate(symbol, result.direction, 0.1, sl_pips=sl_pips)
    if not compliance_result.get("allowed"):
        violations = compliance_result.get("violations", [])
        reason = violations[0] if violations else "unknown"
        log_decision(symbol, "compliance", False, reason, {"violations": violations})
        print(f"  ✗ Compliance: {reason}")
        continue
    log_decision(symbol, "compliance", True)
    print(f"  ✓ Compliance: PASS")
    
    # Stage 4: Consensus check
    consensus_result = consensus_check(symbol, result.direction, score=result.trade_score, source="paper_trading")
    if not consensus_result.get("allowed"):
        reason = consensus_result.get("reason", "unknown")
        log_decision(symbol, "consensus", False, str(reason))
        print(f"  ✗ Consensus: {reason}")
        continue
    log_decision(symbol, "consensus", True)
    print(f"  ✓ Consensus: ALLOW")
    
    # Stage 5: Risk management (simulated)
    risk_amount = acct.equity * 0.005  # 0.5% risk
    log_decision(symbol, "risk", True, details={"risk_amount": risk_amount})
    print(f"  ✓ Risk: ${risk_amount:.2f}")
    
    # Stage 6: Order execution (SIMULATED)
    log_decision(symbol, "execution", True, details={"simulated": True})
    print(f"  ✓ Execution: SIMULATED (no real order)")
    
    # Final decision
    print(f"  → ACCEPTED (paper trade)")

# Summary
print()
print("=" * 80)
print("PIPELINE STATISTICS")
print("=" * 80)

stages = ["market_data", "strategy", "compliance", "consensus", "risk", "execution"]
for stage in stages:
    passed = stats.get(f"{stage}_pass", 0)
    failed = stats.get(f"{stage}_fail", 0)
    total = passed + failed
    pct = (passed / total * 100) if total > 0 else 0
    print(f"{stage:15s}: {passed:3d}/{total:3d} passed ({pct:5.1f}%)")
    
    # Show failure reasons
    for key, count in stats.items():
        if key.startswith(f"{stage}_fail_"):
            reason = key.replace(f"{stage}_fail_", "")
            print(f"  └─ {reason}: {count}")

print()
print(f"Total decisions logged: {len(decisions)}")
print(f"Symbols that passed all stages: {stats.get('execution_pass', 0)}")
print()

# Save decisions to file
output_path = Path("E:/Project/Mt5 Ea/logs/paper_trading_decisions.jsonl")
output_path.parent.mkdir(parents=True, exist_ok=True)
with output_path.open("w", encoding="utf-8") as f:
    for decision in decisions:
        f.write(json.dumps(decision) + "\n")

print(f"Decisions saved to: {output_path}")
print()

mt5.shutdown()
print("Paper trading diagnostic complete.")
print()
print("NOTE: This simulation does NOT account for:")
print("  - Real-time market conditions")
print("  - Existing positions")
print("  - Daily trade limits")
print("  - Correlation exposure")
print("  - Margin requirements")
print()
print("For accurate results, run this diagnostic when the market is open.")

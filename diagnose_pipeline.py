"""
Pipeline Diagnostic Tool - Simplified
Traces the complete trading pipeline to identify where signals become zero.
"""
import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, "E:/Project/Mt5 Ea")

import MetaTrader5 as mt5

# Initialize MT5
if not mt5.initialize():
    print(f"MT5 initialization failed: {mt5.last_error()}")
    sys.exit(1)

print("=" * 80)
print("TRADING PIPELINE DIAGNOSTIC")
print("=" * 80)
print(f"Timestamp: {datetime.now()}")
print(f"MT5 Connected: Yes")

acct = mt5.account_info()
if acct:
    print(f"Account: {acct.login}")
    print(f"Server: {acct.server}")
    print(f"Balance: ${acct.balance:.2f}")
    print(f"Equity: ${acct.equity:.2f}")
print()

# Load settings.json directly
settings_path = Path("E:/Project/Mt5 Ea/mt5_mcp/settings.json")
settings = json.loads(settings_path.read_text())

print("CONFIGURATION (from settings.json)")
print("-" * 80)

risk_cfg = settings.get("risk", {})
equity = acct.equity if acct else 10000
max_risk_pct = risk_cfg.get("max_risk", 0.5)
max_risk_usd = equity * max_risk_pct / 100

print(f"Max risk per trade: {max_risk_pct}% = ${max_risk_usd:.2f}")
print(f"Max positions: {risk_cfg.get('max_positions', 3)}")
print(f"Max trades/day: {risk_cfg.get('max_trades_per_day', 3)}")
print(f"Min RR: {risk_cfg.get('min_risk_reward', 3.0)}")
print(f"SL range: {risk_cfg.get('sl_min_pips', 20)}-{risk_cfg.get('sl_max_pips', 30)} pips")
print(f"TP range: {risk_cfg.get('tp_min_pips', 80)}-{risk_cfg.get('tp_max_pips', 100)} pips")
print()

strat_cfg = settings.get("strategy", {})
print(f"Confidence floor: {strat_cfg.get('confidence_floor', 75)}%")
print(f"Min trade score: {strat_cfg.get('min_trade_score', 70)}")
print(f"Min zone strength: {strat_cfg.get('min_zone', 70)}")
print(f"Require MTF alignment: {strat_cfg.get('require_mtf_alignment', True)}")
print()

blocked = risk_cfg.get("blocked_symbols", [])
print(f"Blocked symbols ({len(blocked)}): {', '.join(blocked)}")
print()

consensus_cfg = settings.get("consensus", {})
allowed = consensus_cfg.get("allowed_symbols", [])
print(f"Consensus allowlist ({len(allowed)}): {', '.join(allowed)}")
print()

# Engine symbol universes
print("ENGINE SYMBOL UNIVERSES")
print("-" * 80)

from trade_desk import UNIVERSE as DESK_UNIVERSE
from confirm_execute_daemon import SYMBOLS as CONFIRM_SYMBOLS
from forex_breakout_watch import FOREX_SYMBOLS as BREAKOUT_SYMBOLS

print(f"Trade Desk UNIVERSE ({len(DESK_UNIVERSE)}):")
print(f"  {', '.join(DESK_UNIVERSE)}")
print()
print(f"Confirm DEFAULT_SYMBOLS ({len(CONFIRM_SYMBOLS)}):")
print(f"  {', '.join(CONFIRM_SYMBOLS)}")
print()
print(f"Breakout FOREX_SYMBOLS ({len(BREAKOUT_SYMBOLS)}):")
print(f"  {', '.join(BREAKOUT_SYMBOLS)}")
print()

# Conflict analysis
print("CRITICAL: SYMBOL CONFLICT ANALYSIS")
print("-" * 80)

all_engine_symbols = set(DESK_UNIVERSE) | set(CONFIRM_SYMBOLS) | set(BREAKOUT_SYMBOLS)
blocked_set = set(blocked)
allowed_set = set(allowed)

conflicts = all_engine_symbols & blocked_set
print(f"⚠ Symbols in engines BUT blocked by compliance ({len(conflicts)}):")
for sym in sorted(conflicts):
    engines = []
    if sym in DESK_UNIVERSE: engines.append("desk")
    if sym in CONFIRM_SYMBOLS: engines.append("confirm")
    if sym in BREAKOUT_SYMBOLS: engines.append("breakout")
    print(f"  - {sym} (used by: {', '.join(engines)})")
print()

missing_from_allowlist = all_engine_symbols - allowed_set
print(f"⚠ Symbols in engines BUT NOT in consensus allowlist ({len(missing_from_allowlist)}):")
for sym in sorted(missing_from_allowlist):
    engines = []
    if sym in DESK_UNIVERSE: engines.append("desk")
    if sym in CONFIRM_SYMBOLS: engines.append("confirm")
    if sym in BREAKOUT_SYMBOLS: engines.append("breakout")
    print(f"  - {sym} (used by: {', '.join(engines)})")
print()

tradable = all_engine_symbols - blocked_set - missing_from_allowlist
print(f"✓ Symbols that can potentially trade: {len(tradable)}")
if tradable:
    print(f"  {', '.join(sorted(tradable))}")
print()

# Test data availability
print("MARKET DATA AVAILABILITY")
print("-" * 80)

test_symbols = sorted(all_engine_symbols)[:10]
data_available = 0
for symbol in test_symbols:
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print(f"{symbol}: NOT FOUND in MT5")
        continue

    if not symbol_info.visible:
        print(f"{symbol}: EXISTS but NOT VISIBLE")
        continue

    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 100)
    if rates is None or len(rates) == 0:
        print(f"{symbol}: NO DATA")
        continue

    data_available += 1
    print(f"{symbol}: ✓ {len(rates)} bars, spread={symbol_info.spread:.1f}pts")

print()
print(f"Data available: {data_available}/{len(test_symbols)} symbols")
print()

# Test compliance gate
print("COMPLIANCE GATE TEST")
print("-" * 80)

from mt5_mcp.compliance import gate as compliance_gate

test_cases = [
    ("EURUSD", "BUY", 0.1, 25.0, "Should PASS"),
    ("GBPUSD", "SELL", 0.1, 25.0, "Should BLOCK (blocked symbol)"),
    ("AUDNZD", "BUY", 0.1, 50.0, "May BLOCK (risk cap)"),
    ("USDJPY", "BUY", 0.1, 30.0, "Should PASS"),
]

for symbol, side, lots, sl_pips, expected in test_cases:
    result = compliance_gate(symbol, side, lots, sl_pips=sl_pips)
    # result is a dict with 'allowed' key
    allowed = result.get("allowed", False)
    status = "PASS" if allowed else "BLOCK"
    print(f"{symbol:8s} {side:4s} SL={sl_pips:2.0f}pips: {status:5s} - {expected}")
    violations = result.get("violations", [])
    if violations:
        print(f"         Violations: {', '.join(violations)}")

print()

# Test consensus check
print("CONSENSUS CHECK TEST")
print("-" * 80)

from mt5_mcp.consensus import check as consensus_check

test_consensus = [
    ("EURUSD", "In allowlist"),
    ("AUDNZD", "NOT in allowlist"),
    ("CHFJPY", "NOT in allowlist"),
]

for symbol, note in test_consensus:
    result = consensus_check(symbol, "BUY", score=70.0, source="diagnostic")
    allowed = result.get("allowed", False)
    status = "ALLOW" if allowed else "BLOCK"
    print(f"{symbol:8s} score=70: {status:5s} - {note}")
    if not allowed:
        reason = result.get("reason", result.get("violations", ["unknown"]))
        print(f"         Reason: {reason}")

print()

# Summary
print("=" * 80)
print("DIAGNOSTIC SUMMARY")
print("=" * 80)
print(f"Total unique symbols across engines: {len(all_engine_symbols)}")
print(f"Symbols blocked by compliance: {len(conflicts)}")
print(f"Symbols missing from consensus allowlist: {len(missing_from_allowlist)}")
print(f"Symbols that can potentially trade: {len(tradable)}")
print()

if len(conflicts) > 0:
    print("⚠ BLOCKER #1: Compliance blocked_symbols overlap with engine symbols")
    print(f"  Fix: Remove {', '.join(sorted(conflicts))} from blocked_symbols")
    print()

if len(missing_from_allowlist) > 0:
    print("⚠ BLOCKER #2: Engine symbols missing from consensus allowlist")
    print(f"  Fix: Add {', '.join(sorted(missing_from_allowlist))} to consensus.allowed_symbols")
    print()

if len(tradable) == 0:
    print("⚠ CRITICAL: No symbols can trade! All are blocked or missing from allowlist.")
    print()

mt5.shutdown()
print("Diagnostic complete.")

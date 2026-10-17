# Trading Pipeline Diagnostic Report
**Date:** 2026-09-27  
**Account:** 11911448 (FundingPips2-SIM) / 41204968 (Deriv-Demo)  
**Balance:** $9,576.70 / $10,208.72

---

## Executive Summary

**ROOT CAUSE: Market is closed (weekend)**

The compliance gate correctly blocks all trades when the forex market is closed (Saturday/Sunday). This is the PRIMARY reason for zero trades.

**Secondary blockers that will prevent trades even when market opens:**
1. Risk cap too tight in conservative mode (0.25% = $23.96)
2. Consensus allowlist incomplete (15 symbols missing)
3. Most symbols not visible in MT5 Market Watch

---

## Pipeline Stage Analysis

### Stage 1: Market Data
- **Status:** ⚠ PARTIAL
- **Issue:** Most symbols not visible in Market Watch
- **Data available:** 2/10 tested symbols (EURUSD, BTCUSD)
- **Impact:** Cannot fetch data for invisible symbols

### Stage 2: Strategy Evaluation
- **Status:** ✓ WORKING (when market open)
- **Thresholds:**
  - Confidence floor: 75%
  - Min trade score: 70
  - Min zone strength: 70
  - Require MTF alignment: True
  - Min RR: 3.0
- **Impact:** Very restrictive but functional

### Stage 3: Compliance Gate (HARD VETO)
- **Status:** ⚠ BLOCKING ALL TRADES
- **Current violations:**
  - `market closed (weekend)` ← PRIMARY BLOCKER
  - `risk 25.00 exceeds cap 0.25% (23.96) in conservative mode`
  - `correlated exposure in 'euro' (25.00) exceeds combined cap (23.96)`
- **Impact:** Zero trades pass

### Stage 4: Consensus Check
- **Status:** ⚠ PARTIAL
- **Allowlist:** 9 symbols (SPX500, XAGUSD, AUDUSD, USDJPY, EURUSD, XAUUSD, JP225, ETHUSD, EURJPY)
- **Missing:** 15 engine symbols (AUDNZD, CHFJPY, GBPUSD, USDCAD, etc.)
- **Impact:** 15 symbols blocked by consensus

### Stage 5: MT5 Execution
- **Status:** NOT TESTED (no signals reach this stage)
- **Impact:** Unknown

---

## Symbol Conflict Analysis

### Blocked by Compliance (4 symbols)
These symbols are in engine universes BUT blocked by compliance:
- **GBPJPY** (trade_desk)
- **GBPUSD** (trade_desk, forex_breakout_watch)
- **NZDUSD** (trade_desk, forex_breakout_watch)
- **USDCAD** (trade_desk, confirm_execute, forex_breakout_watch)

### Missing from Consensus Allowlist (15 symbols)
These symbols are in engine universes BUT not in consensus allowlist:
- AUDNZD, BTCUSD, CHFJPY, EURAUD, EURCHF, GBPCHF, GBPJPY, GBPUSD
- GER40, NZDCAD, NZDJPY, NZDUSD, USDCAD, USDCHF, USOIL

### Tradable Symbols (9 symbols)
Symbols that pass both compliance and consensus:
- **AUDUSD, ETHUSD, EURJPY, EURUSD, JP225, SPX500, USDJPY, XAGUSD, XAUUSD**

---

## Configuration Issues

### Risk Management
```
Max risk per trade: 0.5% = $51.24 (normal mode)
Conservative risk: 0.25% = $23.96 (conservative mode) ← TOO TIGHT
Max positions: 3
Max trades/day: 3
Min RR: 3.0 ← VERY RESTRICTIVE
SL range: 20-30 pips
TP range: 80-100 pips
```

**Problem:** Conservative mode (0.25%) rejects EURUSD 25-pip SL ($25 required > $23.96 cap)

### Strategy Thresholds
```
Confidence floor: 75% ← HIGH
Min trade score: 70 ← HIGH
Min zone strength: 70 ← HIGH
Require MTF alignment: True ← VERY RESTRICTIVE
```

**Problem:** Conjunction of all these conditions is extremely restrictive

### Market Hours
- **Current status:** Market closed (weekend)
- **Impact:** All trades blocked by compliance gate

---

## Signal Death Trace

For a typical EURUSD setup on Monday morning:

```
1. Market data fetched: ✓ 5000 bars
2. Features computed: ✓ 4990 rows
3. Strategy evaluation:
   - H4 trend: ✓ directional (not sideways)
   - MTF alignment: ✓ H1/H4/D1 agree
   - Zone strength: ✓ 72 >= 70
   - Confirmation candle: ✓ bullish_engulfing
   - Trade score: ✓ 73 >= 70
   - Confidence: ✓ 78% >= 75%
   → valid=True, direction=BUY
4. Compliance gate:
   - Symbol not blocked: ✓
   - Market open: ✗ BLOCKED (if weekend)
   - Risk cap: ✗ BLOCKED (if conservative mode, 25 > 23.96)
   → REJECTED
5. Consensus check:
   - In allowlist: ✓
   - No veto: ✓
   → ALLOWED
6. MT5 execution: NOT REACHED
```

**Result:** Zero trades due to compliance gate (market closed + risk cap)

---

## Recommendations

### Immediate Fixes (Required for ANY trades)

1. **Wait for market to open** (Monday 00:00 UTC)
   - No code changes needed
   - Compliance gate will allow trades when market opens

2. **Increase risk cap in conservative mode**
   - Current: 0.25% = $23.96
   - Recommended: 0.5% = $47.92 or disable conservative mode
   - File: `mt5_mcp/settings.json` line 30
   - Change: `"conservative": 0.5` or set `"conservative": 0.0` to disable

3. **Expand consensus allowlist**
   - Add all engine symbols to `consensus.allowed_symbols`
   - File: `mt5_mcp/settings.json` lines 76-79
   - Add: AUDNZD, CHFJPY, GBPUSD, USDCAD, NZDUSD, USDCHF, etc.

4. **Resolve blocked_symbols conflicts**
   - Remove GBPUSD, USDCAD, NZDUSD from `blocked_symbols` if engines should trade them
   - OR remove them from engine symbol universes
   - File: `mt5_mcp/settings.json` lines 47-51

### Optional Fixes (Improve trade frequency)

5. **Lower strategy thresholds**
   - Min trade score: 70 → 60
   - Confidence floor: 75% → 65%
   - Min zone strength: 70 → 60
   - Min RR: 3.0 → 2.0
   - File: `mt5_mcp/settings.json`

6. **Disable MTF alignment requirement**
   - Set `require_mtf_alignment: false`
   - File: `mt5_mcp/settings.json`

7. **Make symbols visible in Market Watch**
   - Manually add symbols in MT5 terminal
   - OR use `mt5.symbol_select(symbol, True)` programmatically

---

## Test Plan

### When Market Opens (Monday)

1. Run diagnostic script again:
   ```bash
   python diagnose_pipeline.py
   ```

2. Check compliance gate passes:
   ```
   EURUSD BUY SL=25pips: PASS (no violations)
   USDJPY BUY SL=30pips: PASS (no violations)
   ```

3. Run paper-trading mode (see next section)

4. Monitor logs:
   - `logs/trade_desk.log`
   - `logs/confirm_exec.log`
   - `logs/forex_breakout.log`

### Expected Behavior

- **Trade desk:** Scans 23 symbols, picks 2-5, executes 1-3
- **Confirm execute:** Scans 6 symbols, generates 0-2 signals, executes 0-1
- **Breakout watch:** Scans 8 symbols, detects 0-3 breakouts, executes 0-2

**Total expected trades per day:** 1-6 (depending on market conditions)

---

## Files Involved

### Configuration
- `mt5_mcp/settings.json` - Risk, strategy, consensus config
- `.env` - Environment overrides

### Compliance
- `mt5_mcp/compliance.py` - Hard veto gate (line 477)
- `mt5_mcp/consensus.py` - Consensus allowlist (line 146)

### Engines
- `trade_desk.py` - Hedge-fund desk (line 1018 scan_cycle)
- `confirm_execute_daemon.py` - Confirm-and-execute (line 525 scan_symbol)
- `forex_breakout_watch.py` - Breakout watcher (line 237 scan_once)

### Shared Pipeline
- `mt5_mcp/pipeline.py` - run_gates (line 41)
- `mt5_mcp/strategy.py` - evaluate_strategy (line 271)
- `mt5_mcp/tools/trades.py` - open_market (line 187)

---

## Changes Made

**None yet.** This is a diagnostic report only.

Recommended changes (pending user approval):
1. Increase conservative risk cap from 0.25% to 0.5%
2. Expand consensus allowlist to include all engine symbols
3. Resolve blocked_symbols conflicts

---

## Remaining Issues

1. **Market closed (weekend)** - Wait for Monday
2. **Risk cap too tight** - Increase conservative mode cap
3. **Consensus allowlist incomplete** - Add missing symbols
4. **Symbols not visible** - Make visible in MT5 Market Watch
5. **MT5 execution untested** - Verify order_send works when signals reach it

---

## Conclusion

**The system is correctly configured to prevent trades when the market is closed.** This is not a bug - it's the intended behavior.

When the market opens, the system will still block trades due to:
- Conservative risk cap too tight (0.25%)
- Incomplete consensus allowlist (15 symbols missing)
- Blocked symbols conflicts (4 symbols)

**Recommended action:**
1. Wait for market to open (Monday)
2. Increase conservative risk cap to 0.5%
3. Expand consensus allowlist
4. Resolve blocked_symbols conflicts
5. Run paper-trading mode to verify pipeline
6. Monitor live execution

**Do NOT loosen strategy thresholds or risk controls until the above issues are resolved and the pipeline is verified to be working correctly.**

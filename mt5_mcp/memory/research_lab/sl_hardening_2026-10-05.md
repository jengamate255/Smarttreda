# SL Hardening: BTCUSD & USDJPY Stop-Out Investigation

**Date:** 2026-10-05
**Trigger:** BTCUSD 4 SELL trades all SL hits (-$74.41), USDJPY 3 trades all SL hits (-$86.75)

## Root Cause

The desk (`trade_desk.py`) uses **M5 ATR(14) × 4.5** for SL distance on ALL symbols.
This produces absurdly tight stops on volatile symbols:

| Symbol | M5 ATR range | Desk SL (4.5×) | Actual volatility | Verdict |
|--------|-------------|-----------------|-------------------|---------|
| BTCUSD | $50-$200 | $225-$900 | $3,300+ (3.3% pullbacks) | **4-15x too tight** |
| ETHUSD | $10-$30 | $45-$135 | $100+ (4% pullbacks) | **2-8x too tight** |
| USDJPY | 3-8 pips | 13.5-36 pips | 15-25 pips session opens | **clipped by noise** |
| GBPJPY | 4-10 pips | 18-45 pips | 20-35 pips session opens | **marginal** |

The dedicated strategies in `multi_strategy.py` already had appropriate parameters:
- `btc_trend_following`: H4 ATR × 2.5 = ~$1,155 SL
- `mean_reversion` (USDJPY): fixed 25-pip SL

But the desk ignores strategy routing entirely — it uses its own confluence scoring + uniform M5 ATR.

## Compounding Issues

1. **No spread buffer on SL price** — spread was added to sizing math only (reducing lot size),
   but the actual SL level sent to the broker had no spread compensation. On BTCUSD with $3-$15
   spreads, the effective stop was tighter than intended.

2. **TP calculated before stop adjustments** — `tp_dist = DESK_TP_R_MULT * stop` was computed
   before the floor/spread/structural adjustments to `stop`, breaking the R:R ratio.

3. **48-hour time stop** — BTC trends take days to develop; desk flattens before the trade works.

## Fixes Applied

### 1. Symbol-specific minimum SL floors (`trade_desk.py` + `mt5_mcp/pipeline.py`)

```python
MIN_SL_FLOOR = {
    "BTCUSD": 1000.0,   # $1000 min (was $225-$900 from M5 ATR)
    "ETHUSD": 30.0,     # $30 min
    "USDJPY": 0.20,     # 20 pips min (was 9-36 pips)
    "EURJPY": 0.20,     # 20 pips
    "GBPJPY": 0.25,     # 25 pips
    "CHFJPY": 0.20,     # 20 pips
}
```

Applied in `_plan_levels()` after ATR calculation, before structural invalidation check.
Shared `enforce_min_sl_floor()` utility added to `pipeline.py` for other execution paths.

### 2. Half-spread buffer on SL price (`trade_desk.py` `_plan_levels()`)

```python
stop += spread_points * point * 0.5  # half-spread buffer on SL price
```

Full spread remains in sizing math (reduces lot size). Half-spread added to SL distance
protects the actual exit level from being immediately hit by the bid-ask bounce.

### 3. TP recalculated after all stop adjustments

Moved `tp_dist = DESK_TP_R_MULT * stop` to after floor + spread + structural invalidation,
so the TP always maintains the correct R:R ratio relative to the final stop distance.

## Expected Impact

| Symbol | Before (SL distance) | After (SL distance) | Effect |
|--------|---------------------|---------------------|--------|
| BTCUSD | $225-$900 | $1,000+ | Survives normal BTC noise |
| USDJPY | 9-36 pips | 20+ pips | Survives session-open chop |
| EURUSD | ~30 pips (unchanged) | ~30 pips | No change (floor not triggered) |

Lot sizes will decrease proportionally (same $90 risk / wider SL = fewer lots),
but win rate should improve because stops aren't clipped by noise.

## Files Modified

- `trade_desk.py` — `MIN_SL_FLOOR` dict, `_plan_levels()` floor + spread buffer + TP recalc
- `mt5_mcp/pipeline.py` — shared `MIN_SL_FLOOR` + `enforce_min_sl_floor()` utility

## Follow-Up

- Monitor BTCUSD/USDJPY win rate over next 10 trades
- Consider raising `MAX_HOLD_HOURS` for BTCUSD (48h → 96h) to let trends develop
- If JPY pairs still get clipped, raise floor to 25-30 pips
- The desk still ignores strategy routing — long-term fix is to integrate
  `multi_strategy.py` parameters into the desk's entry/SL logic

# ICT Concepts [LuxAlgo] — testable signal spec (extracted from Pine v5 source)

Date: 2026-08-17. Source: TradingView "ICT Concepts [LuxAlgo]" indicator.
Purpose: spec for event-study validation. Each component = one testable edge concept.

## Components (exact definitions from source)

### 1. Displacement candles (momentum candle)
```
body    = |close - open|
meanBody = SMA(body, 5)
L_bodyUP = body > meanBody AND (high - max(close,open)) < body*0.36
           AND (min(close,open) - low) < body*0.36 AND close > open
L_bodyDN = same with close < open
```
- body larger than 5-bar average AND both wicks < 36% of body.
- Test: does a displacement candle continue (drift in its direction over 3/5/10/20 bars)?

### 2. Fair Value Gap (FVG) — 3-candle imbalance
```
imbalanceUP = L_bodyUP[1] AND low > high[2]     (current bar low above the high 2 bars back)
FVG zone    = [high[2], low]  (bullish; price gap that was not traded)
imbalanceDN = L_bodyDN[1] AND high < low[2]
```
- Requires a displacement candle immediately before the gap.
- Test A (continuation): after FVG forms, forward drift in FVG direction.
- Test B (retracement bounce / "rebalance"): when price later re-enters the FVG
  zone (high >= zone top for bullish), does it bounce back in the FVG direction?

### 3. Market Structure Shift (MSS)
- Zigzag: ph = pivothigh(3, 1), pl = pivotlow(3, 1) — alternating swing points.
- Bullish MSS: last structure move was DOWN (last swing point is a high, d==1,
  MSS.dir was -1) and close > that swing high → structure shifted up.
- Simplified, robust equivalent for event study:
  - Find most recent confirmed swing high PH and the most recent swing low PL
    that formed AFTER PH (lower-low context).
  - Event: close > PH  → bullish MSS. Entry next bar open, direction = long.
- Test: forward drift vs same-direction random-entry baseline.

### 4. Break of Structure (BOS)
- Only valid while MSS.dir == 1 (already bullish): close > latest swing high.
- Event: close above the most recent swing high, given the previous MSS was
  bullish (i.e., continuation inside an uptrend).
- Test: continuation drift.

### 5. Order Blocks
- swings(length=10): upper = highest(10), lower = lowest(10).
- When close crosses a swing point, scan candles from swing to break; the OB is
  the min/max of the candles between.
- Testable simplification: after a bullish OB forms (down-candle cluster before
  an impulsive up-break), does price respect it — bounce probability when price
  returns to the OB zone vs random?

### 6. Volume Imbalance (engulfing continuation)
```
vImb_Bl = open > close[1] AND high[1] > low AND close > close[1]
          AND open > open[1] AND high[1] < min(close, open)  (current bar's low > prev high)
vImb_Br = mirror
```
- Up bar opens above prev close, closes higher, opens higher, and prev high is
  below current low (gap up + follow-through).
- Test: continuation drift after vImb_Bl / vImb_Br.

### 7. Killzones (session timing filters)
```
NY         = 07:00-09:00 America/New_York
LondonOpen = 07:00-10:00 Europe/London
LondonCls  = 15:00-17:00 Europe/London
Asian      = 10:00-14:00 Asia/Tokyo
```
- Not a standalone signal — a FILTER. Test whether any of the above components
  improve when restricted to NY/London sessions (FX only; synthetics are 24/7).

### 8. Liquidity pools (buyside/sellside clusters)
- 3+ swing highs within ±ATR/2.5 of the current high → liquidity box; box broken
  when close > top+bottom progression.
- NOTE: this is the "liquidity sweep" family already tested NO EDGE
  (ledger 2026-08-17-liquidity-sweep-v1, ~46k events). Only re-test if the
  definition differs materially (close-based box break, not wick-based sweep).

## Validation rules (mandatory, from edge-lab standards)
- Entry on NEXT bar open; drift in ATR units at 3/5/10/20 bars; 1-ATR touch
  win/loss within 20 bars.
- Baseline: same-direction RANDOM-bar entries, same symbols/timeframes.
  Hit-rate vs 50% alone is NOT acceptable.
- N >= 200 events per cell; bootstrap p < 0.05 vs baseline; half-sample
  stability; cost survival (FX ~1.5 pips, XAU ~12 pts, synthetics ~1-2 pts).

## Data
- FX (prop :8051): EURUSD, GBPUSD, XAUUSD — M15, H1 (max history available).
- Synthetics (deriv :8050): Boom 500, Boom 100, Crash 500, Crash 100 — M5 (and
  M1 if cheap). Sessions/killzones do NOT apply to synthetics.

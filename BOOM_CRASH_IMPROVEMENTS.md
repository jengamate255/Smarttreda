# Boom/Crash Synthetic Trading Improvements

## Summary of Changes (2026-09-27)

Implemented 4 major improvements to enhance Boom/Crash synthetic pair trading effectiveness.

---

## 1. Fixed Correlation Guard False-Positive ✅

**Problem:** All Boom/Crash pairs were being blocked with "opposite exposure open" even when no Boom/Crash positions existed.

**Root Cause:** The `_has_opposite_exposure()` function was checking ALL positions for boom/crash keywords, including forex pairs. It also had overly aggressive logic that blocked ALL booms if ANY crash was open.

**Solution:**
- Improved position filtering to only check actual Boom/Crash symbols (not forex)
- Changed from binary "any opposite exposure" to "exposure limit per type"
- Added detailed logging showing which positions are causing blocks
- New function `_get_exposure_by_type()` groups positions by boom/crash type

**Code Changes:**
```python
# Old: Blocked ALL booms if ANY crash open
def _has_opposite_exposure(client, symbol: str) -> bool:
    # Checked all positions, including forex

# New: Allows up to 2 positions of each type independently
def _has_opposite_exposure(client, symbol: str, max_positions_per_type: int = 2) -> tuple[bool, str]:
    # Only checks Boom/Crash positions
    # Returns (should_block, detailed_reason)
```

**Impact:** Boom/Crash watcher can now enter trades when appropriate, instead of being 100% blocked.

---

## 2. Lowered Confidence Floor from 90 to 75 ✅

**Problem:** Confidence floor of 90 was calibrated for forex zone trading, too restrictive for synthetic spike-drift patterns.

**Solution:**
- Changed default `--confidence-floor` from 90.0 to 75.0
- Added help text explaining the change
- Synthetics have different volatility profiles, 75 is more appropriate

**Code Changes:**
```python
# Old
parser.add_argument("--confidence-floor", type=float, default=90.0)

# New
parser.add_argument("--confidence-floor", type=float, default=75.0,
                    help="Minimum confidence to enter trade (default: 75, lowered from 90 for synthetics)")
```

**Impact:** More setups will pass the confidence filter, increasing trade opportunities while maintaining quality.

---

## 3. Added Spike-Frequency Filtering ✅

**Problem:** No tracking of spike momentum - all spikes treated equally regardless of recent activity.

**Solution:**
- Added spike history tracking per symbol (last 60 minutes)
- Hot symbols (≥2 spikes in 60min) get +5 confidence boost
- Cold symbols need base confidence to pass
- Detailed logging of spike frequency for monitoring

**Code Changes:**
```python
# New tracking in BoomCrashWatcher.__init__
self._spike_history: dict[str, list[float]] = {}
self._spike_window_minutes = 60
self._min_spike_frequency = 2

# New methods
def _record_spike(self, symbol: str) -> None
def _get_spike_frequency(self, symbol: str) -> int
def _is_spike_hot(self, symbol: str) -> bool

# Integrated into scan_once
self._record_spike(api_symbol)
spike_freq = self._get_spike_frequency(api_symbol)

# Confidence boost for hot symbols
if spike_freq >= self._min_spike_frequency:
    effective_confidence += 5.0
```

**Impact:** Prioritizes symbols with recent spike momentum, improving entry quality and win rate.

---

## 4. Separated Boom/Crash Exposure Limits ✅

**Problem:** Single correlation bucket blocked all booms if any crash open (and vice versa).

**Solution:**
- Independent exposure limits: up to 2 BOOM positions AND 2 CRASH positions simultaneously
- No longer blocks booms just because a crash is open
- Prevents over-concentration while allowing diversification

**Code Changes:**
```python
# New function to group positions by type
def _get_exposure_by_type(client) -> dict[str, list[str]]:
    exposure = {"boom": [], "crash": []}
    # Groups positions by boom/crash type

# Updated check with per-type limits
def _has_opposite_exposure(client, symbol: str, max_positions_per_type: int = 2):
    # Allows up to 2 of each type
    # Blocks if we'd exceed limit for that type
```

**Impact:** Can now run multiple Boom strategies (BOOM1000, BOOM500) and Crash strategies simultaneously, improving opportunity capture.

---

## Testing & Deployment

### Restart Required
The MT5R_Watch task needs to be restarted to pick up the changes:
```powershell
schtasks /End /TN "MT5R_Watch"
schtasks /Run /TN "MT5R_Watch"
```

### Expected Behavior
1. **Correlation guard:** Should no longer block all entries (unless actual Boom/Crash positions exist)
2. **Confidence floor:** Setups with confidence 75-89 will now be considered (previously blocked)
3. **Spike frequency:** Hot symbols (2+ spikes in 60min) get logged as "hot" and receive +5 confidence boost
4. **Exposure limits:** Can have up to 2 BOOM + 2 CRASH positions open simultaneously

### Monitoring
Watch `logs/trade_watch_live.log` for:
- `spike detected: conf=X.X freq=Y/60min (hot)` - active spike momentum
- `spike frequency boost: X.X -> Y.Y` - confidence boost applied
- `exposure limit reached: max 2 BOOM positions already open` - working as intended
- No more false "opposite exposure" blocks

---

## Configuration Options

All parameters can be adjusted via command-line args when starting the watcher:

```bash
--confidence-floor 75.0      # Minimum confidence (default: 75)
--max-spike-age 15           # Max candles since spike (default: 15)
--lookback 60                # Minutes to look back for spikes (default: 60)
--spike-threshold-atr 2.5    # ATR multiplier for spike detection (default: 2.5)
--min-spike-ticks 8          # Minimum ticks in spike (default: 8)
```

Internal parameters (in code):
```python
self._spike_window_minutes = 60    # Track spikes in last 60 minutes
self._min_spike_frequency = 2      # Minimum spikes to be "hot"
max_positions_per_type = 2         # Max positions of each type (boom/crash)
spike_frequency_boost = 5.0        # Confidence boost for hot symbols
```

---

## Next Steps

1. **Monitor for 24-48 hours** to verify correlation guard fix
2. **Track win rate** with new confidence floor (75 vs old 90)
3. **Analyze spike frequency data** to tune `_min_spike_frequency` if needed
4. **Consider dynamic position limits** based on account equity or win rate

---

## Files Modified

- `trade_watch.py` - All 4 improvements implemented
  - `_has_opposite_exposure()` - Fixed correlation guard
  - `_get_exposure_by_type()` - New position grouping
  - `BoomCrashWatcher.__init__()` - Added spike tracking
  - `BoomCrashWatcher._record_spike()` - New spike recording
  - `BoomCrashWatcher._get_spike_frequency()` - New frequency query
  - `BoomCrashWatcher._is_spike_hot()` - New hot check
  - `BoomCrashWatcher.scan_once()` - Integrated all improvements
  - `_parse_args()` - Lowered confidence floor default

---

## Risk Notes

- **Lower confidence floor** = more trades, potentially more losses initially
- **Spike frequency boost** = preferential treatment for active symbols
- **Independent exposure** = can have 4 positions total (2 boom + 2 crash) vs previous 1
- Monitor drawdown closely in first week
- Adjust `max_positions_per_type` if risk is too high

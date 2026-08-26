#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'r', errors='ignore') as f:
    lines = f.readlines()

# Replace the get_signals function (lines 506-626) with a simplified version
# That only uses self-tune config, no bot analysis needed

new_func = '''@app.get("/api/signals")
def get_signals(
    symbol: str = Query("BOOM150", pattern="^(BOOM|CRASH)\\d+$"),
    timeframe: str = Query("H1"),
    count: int = Query(300, ge=50, le=1000),
) -> dict:
    """Get current trading signals for a synthetic symbol.
    
    Returns signal entries based on the self-tuning AI layer parameters.
    Uses per-symbol confidence floors, spike thresholds and size multipliers.
    No live MT5 connection required - uses cached tuning configuration.
    """
    from mt5_mcp.self_tune import current_symbol
    import time
    
    sym_tune = current_symbol(symbol)
    
    # If symbol is disabled, return empty
    if sym_tune.get("enabled") is False:
        return {
            "symbol": symbol,
            "signals": [],
            "enabled": False,
            "confidence_floor": sym_tune.get("confidence_floor", 90.0),
        }
    
    # Generate signals based on per-symbol tuned parameters only
    # These represent the strategy's current signal thresholds
    signals = []
    
    # Trend signal: based on confidence floor and spike age
    confidence_floor = sym_tune.get("confidence_floor", 90.0)
    max_spike_age = sym_tune.get("max_spike_age", 15)
    spike_threshold_atr = sym_tune.get("spike_threshold_atr", 2.5)
    size_multiplier = sym_tune.get("size_multiplier", 1.0)
    
    # Signal strength indicator based on parameter tightness
    # Lower confidence floor = more signals, higher = fewer but higher quality
    strength_base = (97.0 - confidence_floor) / 97.0  # 0.0 to 1.0
    
    # Add a confidence-level signal
    signals.append({
        "id": f"{symbol}_confidence_{int(time.time())}",
        "symbol": symbol,
        "side": "NEUTRAL",
        "type": "parameter",
        "price": None,
        "strength": round(strength_base, 2),
        "expires": time.time() + 86400,
        "size_multiplier": size_multiplier,
        "confidence_floor": confidence_floor,
        "max_spike_age": max_spike_age,
        "spike_threshold_atr": spike_threshold_atr,
    })
    
    # Sort by strength (already one signal, but maintain sort order)
    signals.sort(key=lambda s: s.get("strength", 0), reverse=True)
    
    return {
        "symbol": symbol,
        "signals": signals,
        "enabled": sym_tune.get("enabled", True),
        "confidence_floor": confidence_floor,
        "max_spike_age": max_spike_age,
        "spike_threshold_atr": spike_threshold_atr,
        "size_multiplier": size_multiplier,
    }'''

# Find the exact range to replace (lines 506-626, which is 0-indexed 505-625)
# Let's find the start and end indices
start_idx = None
end_idx = None

for i, line in enumerate(lines):
    if i >= 505 and '@app.get("/api/signals")' in line:
        start_idx = i
        break

if start_idx is None:
    print(f"Could not find start signal at line {start_idx}")
    # Try finding /api/signals
    for i, line in enumerate(lines):
        if '/api/signals' in line:
            start_idx = i
            break

# Find the end - after the return statement of get_signals
if start_idx is not None:
    # Find the next function definition after start_idx
    end_idx = None
    for i in range(start_idx + 1, len(lines)):
        # Check if this line starts a new function at the same indent level
        stripped = lines[i].strip()
        if stripped.startswith('def ') and i > start_idx + 5:
            end_idx = i
            break
    
    if end_idx is None:
        end_idx = len(lines)  # Go to end of file
    
    print(f"Replacing lines {start_idx+1} to {end_idx} (0-indexed: {start_idx} to {end_idx-1})")
    
    # Replace the function
    new_lines = lines[:start_idx] + [new_func + '\n'] + lines[end_idx:]
    
    with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'w', errors='ignore') as f:
        f.writelines(new_lines)
    
    print(f"Function replaced. Original: {len(lines)} lines, New: {len(new_lines)} lines")
else:
    print("Could not find endpoint location")
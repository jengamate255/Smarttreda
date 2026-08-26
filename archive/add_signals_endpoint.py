#!/usr/bin/env python
import sys
sys.path.insert(0, r'E:\Project\Mt5 Ea')

with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'r', errors='ignore') as f:
    lines = f.readlines()

# The bot_analysis function ends, next function starts at line 507 (1-indexed)
# We want to insert after line 504 (the return statement of bot_analysis)
# Let's find the exact insertion point - after the bot_analysis return

# Find line 504 (1-indexed) which should be the return of bot_analysis
# Actually, let's just append after the bot_analysis function's blank line

# The insert position: after line that contains 'return result' in bot_analysis
# Let's search for it
insert_idx = None
for i, line in enumerate(lines):
    if 'return result' in line and i > 480 and i < 510:
        insert_idx = i + 1  # Insert after this line
        print(f'Found insert point at line {i+1}: {line[:60]}')
        break

if insert_idx is None:
    # Fallback: insert at line 506 (after bot_analysis)
    insert_idx = 506
    print(f'Using fallback insert point at line {insert_idx}')

# The signals endpoint code to insert
signals_code = '''
@app.get("/api/signals")
def get_signals(
    symbol: str = Query("BOOM150", regex="^(BOOM|CRASH)\\d+$"),
    timeframe: str = Query("H1"),
    count: int = Query(300, ge=50, le=1000),
) -> dict:
    """Get current trading signals for a synthetic symbol.
    
    Returns signal zones, entry levels, and recommended parameters
    based on the self-tuning AI layer.
    """
    from mt5_mcp.self_tune import current_symbol
    from mt5_mcp.technical_analysis import compute_rsi, compute_macd, compute_ema
    
    sym_tune = current_symbol(symbol)
    
    # If symbol is disabled, return empty
    if sym_tune.get("enabled") is False:
        return {
            "symbol": symbol,
            "signals": [],
            "enabled": False,
            "confidence_floor": sym_tune.get("confidence_floor", 90.0),
        }
    
    # Fetch rate data
    key = _bot_cache_key(symbol, timeframe, count)
    with _lock:
        cached = _bot_cache.get(key)
        if cached and time.monotonic() - cached[0] < BOT_CACHE_TTL:
            bot = cached[1]
        else:
            bot = _compute_bot_analysis(symbol, timeframe, count)
            with _lock:
                _bot_cache[key] = (time.monotonic(), bot)
    
    # Generate signals based on bot analysis and current params
    signals = []
    
    # Check for spike signals based on recent price action
    # (Simplified: look for strong swing points with EMA filter)
    trend = bot.get("trend", {})
    direction = trend.get("direction", "neutral")
    confidence = trend.get("confidence", 0.0)
    
    # Add trend-based signal
    if direction != "neutral" and confidence > 70.0:
        side = "BUY" if direction == "bullish" else "SELL"
        price = None  # Will be filled by caller
        signals.append({
            "id": f"{symbol}_{side}_{int(time.time())}",
            "symbol": symbol,
            "side": side,
            "type": "trend",
            "price": price,
            "strength": min(confidence / 100.0, 1.0),
            "expires": time.time() + 3600,  # 1 hour
            "size_multiplier": sym_tune.get("size_multiplier", 1.0),
            "confidence_floor": sym_tune.get("confidence_floor", 90.0),
        })
    
    # Check for zone-based signals
    zones = bot.get("zones", [])
    for z in zones[:3]:  # Top 3 zones
        if z.get("broken", False):
            continue  # Skip broken zones
        zone_type = z.get("type", "neutral")
        if zone_type in ("supply", "resistance"):
            side = "SELL"
        elif zone_type in ("demand", "support"):
            side = "BUY"
        else:
            continue
        
        strength = z.get("strength", 0.0) / 10.0  # Normalize
        if strength > 0.3:
            signals.append({
                "id": f"{symbol}_{side}_{z.get('lower', 0):.1f}",
                "symbol": symbol,
                "side": side,
                "type": "zone",
                "price": z.get("lower") or z.get("upper"),
                "strength": round(strength, 2),
                "expires": time.time() + 3600,
                "size_multiplier": sym_tune.get("size_multiplier", 1.0),
                "confidence_floor": sym_tune.get("confidence_floor", 90.0),
            })
    
    # Check for pattern signals
    patterns = bot.get("patterns", [])
    for p in patterns[:2]:  # Top 2 patterns
        direction = p.get("direction", "neutral")
        confidence_p = p.get("confidence", 0.0)
        if direction == "neutral" or confidence_p < 50.0:
            continue
        
        side = "BUY" if direction == "bullish" else "SELL"
        signals.append({
            "id": f"{symbol}_{side}_{p.get('pattern', 'unknown')}",
            "symbol": symbol,
            "side": side,
            "type": "pattern",
            "price": p.get("price"),
            "strength": round(min(confidence_p / 100.0, 1.0), 2),
            "expires": time.time() + 3600,
            "size_multiplier": sym_tune.get("size_multiplier", 1.0),
            "confidence_floor": sym_tune.get("confidence_floor", 90.0),
        })
    
    # Sort by strength (strongest first)
    signals.sort(key=lambda s: s.get("strength", 0), reverse=True)
    
    return {
        "symbol": symbol,
        "signals": signals,
        "enabled": sym_tune.get("enabled", True),
        "confidence_floor": sym_tune.get("confidence_floor", 90.0),
        "max_spike_age": sym_tune.get("max_spike_age", 15),
        "spike_threshold_atr": sym_tune.get("spike_threshold_atr", 2.5),
        "size_multiplier": sym_tune.get("size_multiplier", 1.0),
    }


'''
# Insert the code
new_lines = lines[:insert_idx] + [signals_code] + lines[insert_idx:]

with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'w', errors='ignore') as f:
    f.writelines(new_lines)

print(f'Signals endpoint added at line {insert_idx}')
print(f'Original lines: {len(lines)}, New lines: {len(new_lines)}')
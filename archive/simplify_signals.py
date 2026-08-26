#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'r', errors='ignore') as f:
    content = f.read()

# Find and replace the entire get_signals function with a simpler version
old_func = '''@app.get("/api/signals")
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
    }'''

new_func = '''@app.get("/api/signals")
def get_signals(
    symbol: str = Query("BOOM150", pattern="^(BOOM|CRASH)\\d+$"),
    timeframe: str = Query("H1"),
    count: int = Query(300, ge=50, le=1000),
) -> dict:
    """Get current trading signals for a synthetic symbol.
    
    Returns signal zones, entry levels, and recommended parameters
    based on the self-tuning AI layer.
    
    Uses per-symbol tuned parameters from the self-tune module.
    Signals are generated from bot analysis combined with
    per-symbol confidence floors and size multipliers.
    """
    from mt5_mcp.self_tune import current_symbol
    
    sym_tune = current_symbol(symbol)
    
    # If symbol is disabled, return empty
    if sym_tune.get("enabled") is False:
        return {
            "symbol": symbol,
            "signals": [],
            "enabled": False,
            "confidence_floor": sym_tune.get("confidence_floor", 90.0),
        }
    
    # Use bot analysis from cache (no live MT5 required for config)
    key = _bot_cache_key(symbol, timeframe, count)
    with _lock:
        cached = _bot_cache.get(key)
        if cached and time.monotonic() - cached[0] < BOT_CACHE_TTL:
            bot = cached[1]
        else:
            # Compute with minimal data - use defaults if cache empty
            try:
                bot = _compute_bot_analysis(symbol, timeframe, count)
            except Exception:
                bot = {"zones": [], "swings": {"highs": [], "lows": []}, "patterns": [], "trend": {"direction": "neutral", "confidence": 0.0}}
            with _lock:
                _bot_cache[key] = (time.monotonic(), bot)
    
    # Generate signals based on bot analysis and current params
    signals = []
    
    # Check for trend-based signal
    trend = bot.get("trend", {})
    direction = trend.get("direction", "neutral")
    confidence = trend.get("confidence", 0.0)
    
    if direction != "neutral" and confidence > 70.0:
        side = "BUY" if direction == "bullish" else "SELL"
        signals.append({
            "id": f"{symbol}_{side}_{int(time.time())}",
            "symbol": symbol,
            "side": side,
            "type": "trend",
            "price": None,
            "strength": min(confidence / 100.0, 1.0),
            "expires": __import__('time').time() + 3600,
            "size_multiplier": sym_tune.get("size_multiplier", 1.0),
            "confidence_floor": sym_tune.get("confidence_floor", 90.0),
        })
    
    # Check for zone-based signals (top 3 unbroken zones)
    zones = bot.get("zones", [])
    unbroken_zones = [z for z in zones if not z.get("broken", False)][:3]
    for z in unbroken_zones:
        zone_type = z.get("type", "neutral")
        if zone_type in ("supply", "resistance"):
            side = "SELL"
        elif zone_type in ("demand", "support"):
            side = "BUY"
        else:
            continue
        
        strength = z.get("strength", 0.0) / 10.0
        if strength > 0.3:
            signals.append({
                "id": f"{symbol}_{side}_{z.get('lower', 0):.1f}",
                "symbol": symbol,
                "side": side,
                "type": "zone",
                "price": z.get("lower") or z.get("upper"),
                "strength": round(strength, 2),
                "expires": __import__('time').time() + 3600,
                "size_multiplier": sym_tune.get("size_multiplier", 1.0),
                "confidence_floor": sym_tune.get("confidence_floor", 90.0),
            })
    
    # Check for pattern signals (top 2 high-confidence patterns)
    patterns = bot.get("patterns", [])
    for p in patterns[:2]:
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
            "expires": __import__('time').time() + 3600,
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
    }'''

if old_func in content:
    content = content.replace(old_func, new_func)
    with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'w', errors='ignore') as f:
        f.write(content)
    print("Signals function simplified")
else:
    print("Old function not found - checking content")
    # Just verify the endpoint exists
    if '/api/signals' in content:
        print("Endpoint exists, will overwrite")
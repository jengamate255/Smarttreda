import sys
sys.path.insert(0, '.')

from mt5_mcp.tools.boom_crash_strategy import scan_boom_crash_opportunities, BOOM_CRASH_SYMBOLS, get_market_state

# Test all working Deriv symbols for current spikes
all_symbols = [
    "BOOM1000", "BOOM900", "BOOM600", "BOOM500", "BOOM200", "BOOM100", "BOOM99", "BOOM50",
    "CRASH1000", "CRASH900", "CRASH600", "CRASH500", "CRASH200", "CRASH100", "CRASH99", "CRASH50",
]

print('Scanning all 16 BOOM/CRASH symbols for current spike signals...\n')

signals = scan_boom_crash_opportunities(all_symbols, min_confidence=0)

print('=== ACTIONABLE SIGNALS (confidence > 0) ===')
if signals:
    for s in signals:
        print(f'{s.symbol}: {s.direction.upper()} | Conf: {s.confidence:.1f}% | Entry: {s.entry_price:.2f} | SL: {s.sl_price:.2f} | TP: {s.tp_price:.2f} | Age: {s.spike_age_candles} bars | {s.reason}')
else:
    print('No actionable signals found')

print()
print('=== MARKET STATE FOR ALL SYMBOLS ===')
for sym in all_symbols:
    state = get_market_state(sym)
    spike = state.get('spike', {})
    price = state.get("current_price", "N/A")
    atr = state.get("atr", "N/A")
    rsi = state.get("rsi", "N/A")
    detected = spike.get("detected", False)
    conf = spike.get("confidence", 0)
    
    if isinstance(price, (int, float)):
        price_str = f"{price:.2f}"
    else:
        price_str = str(price)
    if isinstance(atr, (int, float)):
        atr_str = f"{atr:.2f}"
    else:
        atr_str = str(atr)
    if isinstance(rsi, (int, float)):
        rsi_str = f"{rsi:.1f}"
    else:
        rsi_str = str(rsi)
    
    print(f'{sym}: Price={price_str}, ATR={atr_str}, RSI={rsi_str}, Spike={detected}, Conf={conf}')
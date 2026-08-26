import sys
sys.path.insert(0, '.')

from mt5_mcp.tools.boom_crash_strategy import scan_boom_crash_opportunities, BOOM_CRASH_SYMBOLS, get_market_state

signals = scan_boom_crash_opportunities(list(BOOM_CRASH_SYMBOLS), min_confidence=0)

if signals:
    print("=== ACTIONABLE SIGNALS ===")
    for s in signals:
        print(f'{s.symbol}: {s.direction.upper()} | Conf: {s.confidence:.1f}% | Entry: {s.entry_price:.2f} | SL: {s.sl_price:.2f} | TP: {s.tp_price:.2f} | Age: {s.spike_age_candles} bars | {s.reason}')
else:
    print("=== NO ACTIONABLE SIGNALS (confidence > 0) ===")
    print("\n=== MARKET STATE FOR ALL SYMBOLS ===")
    for sym in BOOM_CRASH_SYMBOLS:
        state = get_market_state(sym)
        spike = state.get("spike", {})
        print(f'{sym}: Price={state.get("current_price", "N/A")}, ATR={state.get("atr", "N/A")}, RSI={state.get("rsi", "N/A")}, SpikeDetected={spike.get("detected", False)}, SpikeConf={spike.get("confidence", 0)}')
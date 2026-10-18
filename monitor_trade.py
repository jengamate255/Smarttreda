"""Monitor ETHUSD BUY trade with alerts."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

import MetaTrader5 as mt5
from datetime import datetime

# Trade parameters
ENTRY = 2409.89
SL = 2361.66
TP = 2567.23
LOTS = 0.92
RISK_USD = 47.15
RR = 3.0

# Alert levels (percent of distance to SL/TP)
ALERT_LEVELS = {
    "breakeven": 0.3,    # Move SL to breakeven at 30% of SL distance
    "partial_tp": 0.5,   # Consider partial TP at 50% of TP distance
    "trail_start": 0.6,  # Start trailing at 60% of TP distance
}

def check_trade():
    """Check current trade status and price levels."""
    if not mt5.initialize(path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe", timeout=5000):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] MT5 connection failed")
        return

    # Get current positions
    positions = mt5.positions_get(symbol="ETHUSD")
    if not positions or len(positions) == 0:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] No open ETHUSD positions")
        mt5.shutdown()
        return

    # Find our position
    pos = None
    for p in positions:
        if p.magic == 202609:
            pos = p
            break
    
    if not pos:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] No position with magic 202609")
        mt5.shutdown()
        return

    # Get current price
    tick = mt5.symbol_info_tick("ETHUSD")
    if not tick:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] No tick data")
        mt5.shutdown()
        return

    bid = tick.bid
    current_pnl = pos.profit
    pnl_pips = (bid - ENTRY) / 0.01  # ETHUSD pip = 0.01

    # Calculate key levels
    sl_distance = ENTRY - SL
    tp_distance = TP - ENTRY
    sl_pct = (ENTRY - bid) / sl_distance if sl_distance > 0 else 0
    tp_pct = (bid - ENTRY) / tp_distance if tp_distance > 0 else 0

    # Print status
    print(f"\n{'='*50}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ETHUSD MONITOR")
    print(f"{'='*50}")
    print(f"Entry: {ENTRY:.2f} | Current: {bid:.2f}")
    print(f"PnL: ${current_pnl:.2f} ({pnl_pips:.1f} pips)")
    print(f"SL: {SL:.2f} | TP: {TP:.2f}")
    print(f"Distance to SL: {sl_pct:.1%} | Distance to TP: {tp_pct:.1%}")

    # Alert checks
    alerts = []
    
    # Breakeven alert
    if tp_pct >= ALERT_LEVELS["breakeven"] and pos.sl < ENTRY:
        alerts.append(f"⚡ BREAKEVEN ALERT: Price at {tp_pct:.0%} of TP. Consider moving SL to breakeven ({ENTRY:.2f})")
    
    # Partial TP alert
    if tp_pct >= ALERT_LEVELS["partial_tp"]:
        alerts.append(f"💰 PARTIAL TP ALERT: Price at {tp_pct:.0%} of TP. Consider closing 50% at {bid:.2f}")
    
    # Trail start alert
    if tp_pct >= ALERT_LEVELS["trail_start"]:
        alerts.append(f"📈 TRAIL ALERT: Price at {tp_pct:.0%} of TP. Consider trailing SL to {bid - sl_distance * 0.3:.2f}")
    
    # SL warning
    if sl_pct >= 0.8:
        alerts.append(f"⚠️ SL WARNING: Price {sl_pct:.0%} of the way to SL!")
    
    # TP warning
    if tp_pct >= 0.9:
        alerts.append(f"🎯 TP WARNING: Price {tp_pct:.0%} of the way to TP!")

    # Print alerts
    if alerts:
        print("\n🚨 ALERTS:")
        for a in alerts:
            print(f"  {a}")
    else:
        print("\n✅ No alerts - trade running normally")

    mt5.shutdown()

if __name__ == "__main__":
    check_trade()

"""Continuous ETHUSD monitor with periodic checks."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

import MetaTrader5 as mt5
from datetime import datetime
import time

ENTRY = 2409.89
SL = 2361.66
TP = 2567.23
MAGIC = 202609

def monitor():
    """Run one monitoring cycle."""
    if not mt5.initialize(path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe", timeout=5000):
        return "MT5 disconnected"

    positions = mt5.positions_get(symbol="ETHUSD")
    if not positions:
        mt5.shutdown()
        return "NO POSITION"

    pos = None
    for p in positions:
        if p.magic == MAGIC:
            pos = p
            break

    if not pos:
        mt5.shutdown()
        return "NO POSITION"

    tick = mt5.symbol_info_tick("ETHUSD")
    if not tick:
        mt5.shutdown()
        return "NO TICK"

    bid = tick.bid
    pnl = pos.profit
    pnl_pips = (bid - ENTRY) / 0.01
    tp_pct = (bid - ENTRY) / (TP - ENTRY) * 100 if TP > ENTRY else 0
    sl_pct = (ENTRY - bid) / (ENTRY - SL) * 100 if ENTRY > SL else 0

    now = datetime.now().strftime("%H:%M:%S")

    # Status line
    status = f"[{now}] ETHUSD ${bid:.2f} | PnL ${pnl:.2f} ({pnl_pips:.1f}p) | TP {tp_pct:.0f}% | SL {sl_pct:.0f}%"

    # Alerts
    alerts = []
    if tp_pct >= 30 and pos.sl < ENTRY:
        alerts.append(f"BREAKEVEN: Move SL to {ENTRY:.2f}")
    if tp_pct >= 50:
        alerts.append(f"PARTIAL TP: Close 50% at ${bid:.2f}")
    if tp_pct >= 60:
        trails = bid - (ENTRY - SL) * 0.3
        alerts.append(f"TRAIL: Move SL to {trails:.2f}")
    if sl_pct >= 80:
        alerts.append(f"SL WARNING: {sl_pct:.0f}% to stop loss!")

    mt5.shutdown()
    return status, alerts

if __name__ == "__main__":
    print("Starting ETHUSD monitor (Ctrl+C to stop)...")
    print(f"Entry: {ENTRY} | SL: {SL} | TP: {TP}")
    print()

    try:
        while True:
            result = monitor()
            if result == "NO POSITION":
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Position closed!")
                break
            elif result == "MT5 disconnected":
                print(f"[{datetime.now().strftime('%H:%M:%S')}] MT5 disconnected, retrying...")
            elif result == "NO TICK":
                print(f"[{datetime.now().strftime('%H:%M:%S')}] No tick data")
            else:
                status, alerts = result
                print(status)
                for a in alerts:
                    print(f"  🚨 {a}")

            time.sleep(30)  # Check every 30 seconds
    except KeyboardInterrupt:
        print("\nMonitor stopped")

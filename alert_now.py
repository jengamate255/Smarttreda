"""Quick one-shot alert check - run this anytime."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
import MetaTrader5 as mt5
from datetime import datetime

ENTRY = 2409.89
SL = 2361.66
TP = 2567.23

if mt5.initialize(path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe", timeout=10000):
    positions = mt5.positions_get(symbol="ETHUSD")
    tick = mt5.symbol_info_tick("ETHUSD")
    mt5.shutdown()

    if positions and tick:
        for p in positions:
            if p.magic == 202609:
                bid = tick.bid
                pnl = p.profit
                pnl_pips = (bid - ENTRY) / 0.01
                tp_pct = max(0, (bid - ENTRY) / (TP - ENTRY) * 100)
                sl_dist = max(0, (ENTRY - bid) / (ENTRY - SL) * 100)

                print(f"[{datetime.now().strftime('%H:%M:%S')}] ETHUSD ${bid:.2f}")
                print(f"PnL: ${pnl:.2f} ({pnl_pips:.1f}p)")
                print(f"TP: {tp_pct:.0f}% | SL: {sl_dist:.0f}%")

                # Alerts
                if tp_pct >= 30:
                    print(">>> BREAKEVEN: Move SL to 2409.89")
                if tp_pct >= 50:
                    print(">>> PARTIAL TP: Close 50%")
                if tp_pct >= 60:
                    print(f">>> TRAIL: Move SL to {bid - (ENTRY-SL)*0.3:.2f}")
                if sl_dist >= 80:
                    print(">>> SL WARNING!")
                if tp_pct < 30 and sl_dist < 80:
                    print("No alerts")
                break
    else:
        print("No position")
else:
    print("MT5 disconnected")

"""Check ETHUSD position after restart."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

import MetaTrader5 as mt5
from datetime import datetime

mt5.initialize(path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe", timeout=15000)

positions = mt5.positions_get(symbol="ETHUSD")
tick = mt5.symbol_info_tick("ETHUSD")

if positions and tick:
    for p in positions:
        if p.magic == 202609:
            pnl = p.profit
            pnl_pips = (tick.bid - p.price_open) / 0.01
            tp_pct = (tick.bid - 2409.89) / (2567.23 - 2409.89) * 100
            sl_dist = (p.price_open - tick.bid) / (p.price_open - 2361.66) * 100

            print(f"[{datetime.now().strftime('%H:%M:%S')}] ETHUSD MONITOR")
            print(f"Price: ${tick.bid:.2f}")
            print(f"Entry: ${p.price_open:.2f}")
            print(f"PnL: ${pnl:.2f} ({pnl_pips:.1f} pips)")
            print(f"TP progress: {tp_pct:.0f}%")
            print(f"SL distance: {sl_dist:.0f}%")
            print()

            # Alerts
            alerts = []
            if tp_pct >= 30 and p.sl < 2409.89:
                alerts.append("BREAKEVEN: Move SL to 2409.89")
            if tp_pct >= 50:
                alerts.append("PARTIAL TP: Close 50% at current price")
            if tp_pct >= 60:
                trail = tick.bid - (2409.89 - 2361.66) * 0.3
                alerts.append(f"TRAIL: Move SL to {trail:.2f}")
            if sl_dist >= 80:
                alerts.append("SL WARNING: Close to stop loss!")

            if alerts:
                print("ALERTS:")
                for a in alerts:
                    print(f"  {a}")
            else:
                print("No alerts - trade running normally")
            break
    else:
        print("No position with magic 202609")
else:
    print("No positions or tick data")

mt5.shutdown()

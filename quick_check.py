"""Quick position check."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
import MetaTrader5 as mt5
from datetime import datetime

if mt5.initialize(path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe", timeout=5000):
    tick = mt5.symbol_info_tick("ETHUSD")
    positions = mt5.positions_get(symbol="ETHUSD")
    
    if tick and positions:
        for p in positions:
            if p.magic == 202609:
                pnl = p.profit
                pnl_pips = (tick.bid - p.price_open) / 0.01
                tp_pct = (tick.bid - 2409.89) / (2567.23 - 2409.89) * 100
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ETHUSD ${tick.bid:.2f} | PnL ${pnl:.2f} ({pnl_pips:.1f}p) | TP progress: {tp_pct:.0f}%")
                break
        else:
            print("No position with magic 202609")
    else:
        print("No tick or positions")
    
    mt5.shutdown()
else:
    print("MT5 disconnected")

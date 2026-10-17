"""Check trade via client module."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

from mt5_mcp.client import get_client
from datetime import datetime

try:
    client = get_client()
    
    # Get positions
    positions = client.positions()
    eth_pos = [p for p in positions if p.get('symbol') == 'ETHUSD' and p.get('magic') == 202609]
    
    if eth_pos:
        pos = eth_pos[0]
        tick = client.tick('ETHUSD')
        bid = tick.get('bid', 0)
        entry = pos.get('price_open', 0)
        pnl = pos.get('profit', 0)
        pnl_pips = (bid - entry) / 0.01
        tp_pct = (bid - 2409.89) / (2567.23 - 2409.89) * 100
        sl_dist = (entry - bid) / (entry - 2361.66) * 100
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ETHUSD ${bid:.2f}")
        print(f"Entry: {entry:.2f} | PnL: ${pnl:.2f} ({pnl_pips:.1f}p)")
        print(f"TP progress: {tp_pct:.0f}% | SL distance: {sl_dist:.0f}%")
        
        # Alerts
        if tp_pct >= 30:
            print("BREAKEVEN: Consider moving SL to entry")
        if tp_pct >= 50:
            print("PARTIAL TP: Consider closing 50%")
        if sl_dist >= 80:
            print("SL WARNING: Close to stop loss!")
    else:
        print("No ETHUSD position found")
        
except Exception as e:
    print(f"Error: {e}")

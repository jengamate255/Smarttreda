"""Telegram alert monitor for ETHUSD trade."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

import MetaTrader5 as mt5
from mt5_mcp.telegram_alerts import send_telegram
from datetime import datetime
import json
import time

ENTRY = 2409.89
SL = 2361.66
TP = 2567.23
MAGIC = 202609
STATE_FILE = r"E:\Project\Mt5 Ea\alert_state.json"

def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"fired": []}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def check_and_alert():
    if not mt5.initialize(path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe", timeout=10000):
        return

    positions = mt5.positions_get(symbol="ETHUSD")
    tick = mt5.symbol_info_tick("ETHUSD")
    mt5.shutdown()

    if not positions or not tick:
        return

    for p in positions:
        if p.magic == MAGIC:
            bid = tick.bid
            pnl = p.profit
            pnl_pips = (bid - ENTRY) / 0.01
            tp_pct = max(0, (bid - ENTRY) / (TP - ENTRY) * 100)
            sl_dist = max(0, (ENTRY - bid) / (ENTRY - SL) * 100)

            state = load_state()
            alerts = []

            if tp_pct >= 30 and "be" not in state["fired"]:
                alerts.append(("be", f"BREAKEVEN ALERT\nPrice: ${bid:.2f}\nTP Progress: {tp_pct:.0f}%\n\nMove SL to {ENTRY:.2f}"))
            if tp_pct >= 50 and "pt" not in state["fired"]:
                alerts.append(("pt", f"PARTIAL TP ALERT\nPrice: ${bid:.2f}\nTP Progress: {tp_pct:.0f}%\n\nClose 50% at ${bid:.2f}"))
            if tp_pct >= 60 and "trail" not in state["fired"]:
                trail_sl = bid - (ENTRY - SL) * 0.3
                alerts.append(("trail", f"TRAIL ALERT\nPrice: ${bid:.2f}\nTP Progress: {tp_pct:.0f}%\n\nMove SL to {trail_sl:.2f}"))
            if sl_dist >= 80 and "slw" not in state["fired"]:
                alerts.append(("slw", f"SL WARNING\nPrice: ${bid:.2f}\nSL Distance: {sl_dist:.0f}%\n\nApproaching stop loss!"))

            for alert_id, msg in alerts:
                send_telegram(f"<b>ETHUSD Trade Alert</b>\n\n{msg}")
                state["fired"].append(alert_id)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Alert sent: {alert_id}")

            save_state(state)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ${bid:.2f} PnL: ${pnl:.2f} TP:{tp_pct:.0f}% SL:{sl_dist:.0f}%")
            break

if __name__ == "__main__":
    print("ETHUSD Telegram alert monitor started")
    while True:
        try:
            check_and_alert()
            time.sleep(60)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(30)

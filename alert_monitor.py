"""Hard alert monitor for ETHUSD trade - checks every 60s, only alerts on key levels."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

import MetaTrader5 as mt5
from datetime import datetime
import time
import json

# Trade config
ENTRY = 2409.89
SL = 2361.66
TP = 2567.23
MAGIC = 202609

# Alert thresholds (percent of TP distance)
ALERTS = {
    "breakeven": 30,   # Alert at 30% TP progress
    "partial_tp": 50,  # Alert at 50% TP progress
    "trail_start": 60, # Alert at 60% TP progress
    "tp_close": 90,    # Alert at 90% TP progress
    "sl_warning": 80,  # Alert at 80% SL distance
}

# State file to track which alerts have fired
STATE_FILE = r"E:\Project\Mt5 Ea\alert_state.json"

def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"fired_alerts": []}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def check_trade():
    if not mt5.initialize(path=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe", timeout=10000):
        return None

    positions = mt5.positions_get(symbol="ETHUSD")
    tick = mt5.symbol_info_tick("ETHUSD")
    mt5.shutdown()

    if not positions or not tick:
        return None

    for p in positions:
        if p.magic == MAGIC:
            bid = tick.bid
            pnl = p.profit
            pnl_pips = (bid - ENTRY) / 0.01
            tp_pct = max(0, (bid - ENTRY) / (TP - ENTRY) * 100)
            sl_dist = max(0, (ENTRY - bid) / (ENTRY - SL) * 100)

            return {
                "price": bid,
                "pnl": pnl,
                "pnl_pips": pnl_pips,
                "tp_pct": tp_pct,
                "sl_dist": sl_dist,
                "time": datetime.now().strftime("%H:%M:%S"),
                "open": True
            }

    return {"open": False}

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ETHUSD alert monitor started")
    print(f"Entry: {ENTRY} | SL: {SL} | TP: {TP}")
    print(f"Alerts: BE@30% | Partial@50% | Trail@60% | TP@90% | SL Warn@80%")
    print("-" * 60)

    state = load_state()

    while True:
        try:
            trade = check_trade()

            if trade is None:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] MT5 disconnected, retrying...")
                time.sleep(30)
                continue

            if not trade.get("open", True):
                print(f"[{datetime.now().strftime('%H:%M:%S')}] POSITION CLOSED!")
                break

            now = trade["time"]
            price = trade["price"]
            pnl = trade["pnl"]
            tp_pct = trade["tp_pct"]
            sl_dist = trade["sl_dist"]

            # Check alerts
            new_alerts = []

            # Breakeven alert
            if tp_pct >= ALERTS["breakeven"] and "breakeven" not in state["fired_alerts"]:
                new_alerts.append(f"BREAKEVEN: Price at {tp_pct:.0f}% of TP. Move SL to {ENTRY:.2f}")
                state["fired_alerts"].append("breakeven")

            # Partial TP alert
            if tp_pct >= ALERTS["partial_tp"] and "partial_tp" not in state["fired_alerts"]:
                new_alerts.append(f"PARTIAL TP: Price at {tp_pct:.0f}% of TP. Close 50% at ${price:.2f}")
                state["fired_alerts"].append("partial_tp")

            # Trail start alert
            if tp_pct >= ALERTS["trail_start"] and "trail_start" not in state["fired_alerts"]:
                trail_sl = price - (ENTRY - SL) * 0.3
                new_alerts.append(f"TRAIL: Price at {tp_pct:.0f}% of TP. Move SL to {trail_sl:.2f}")
                state["fired_alerts"].append("trail_start")

            # TP close alert
            if tp_pct >= ALERTS["tp_close"] and "tp_close" not in state["fired_alerts"]:
                new_alerts.append(f"TP ALERT: Price at {tp_pct:.0f}% of TP! Consider closing.")
                state["fired_alerts"].append("tp_close")

            # SL warning
            if sl_dist >= ALERTS["sl_warning"] and "sl_warning" not in state["fired_alerts"]:
                new_alerts.append(f"SL WARNING: {sl_dist:.0f}% of SL distance used!")
                state["fired_alerts"].append("sl_warning")

            # Only print if there are new alerts or every 5 minutes
            if new_alerts:
                print(f"\n[{now}] *** ALERT *** ${price:.2f} PnL: ${pnl:.2f}")
                for a in new_alerts:
                    print(f"  >>> {a}")
                save_state(state)
            else:
                # Quiet status every 5 min
                if int(datetime.now().timestamp()) % 300 < 60:
                    print(f"[{now}] ${price:.2f} PnL: ${pnl:.2f} TP:{tp_pct:.0f}% SL:{sl_dist:.0f}%")

            time.sleep(60)

        except KeyboardInterrupt:
            print("\nMonitor stopped")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()

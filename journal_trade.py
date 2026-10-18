"""Journal the executed ETHUSD trade."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

from mt5_mcp.memory.boom_crash_journal import record_trade, update_stats

# Record the trade
record_trade({
    "symbol": "ETHUSD",
    "action": "BUY",
    "lots": 0.92,
    "entry_price": 2409.89,
    "sl_price": 2361.66,
    "tp_price": 2567.23,
    "sl_pips": 51.4,
    "tp_pips": 154.2,
    "rr_ratio": 3.0,
    "risk_amount": 47.15,
    "session": "UTC evening",
    "h1_trend": "bullish",
    "confirmation": "bullish_engulfing",
    "notes": "Demand zone 2370-2427, H4 bullish trend, compliance passed"
})

print("Trade journaled successfully")

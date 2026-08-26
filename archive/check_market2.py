import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
from mt5_mcp.client import get_client, reset_client

reset_client()
c = get_client("BTCUSD")
c.ensure_connected()

# BTC tick
tick = c.tick("BTCUSD")
print(f"BTCUSD: bid={tick.get('bid')} ask={tick.get('ask')} spread={tick.get('ask',0)-tick.get('bid',0):.2f}")

# Check other major pairs
symbols = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF"]
print("\nForex/Metals:")
for s in symbols:
    try:
        t = c.tick(s)
        if t.get("bid", 0) > 0:
            spread = t.get("ask", 0) - t.get("bid", 0)
            print(f"  {s}: bid={t.get('bid')} ask={t.get('ask')} spread={spread:.5f}")
        else:
            print(f"  {s}: no quote")
    except Exception as e:
        print(f"  {s}: error - {e}")

# Check BTC H4 data for trend
from mt5_mcp.client import get_client
c2 = get_client("BTCUSD")
df = c2.rates("BTCUSD", "H4", 200)
ema50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
ema200 = df["close"].ewm(span=200, adjust=False).mean().iloc[-1]
price = df["close"].iloc[-1]
print(f"\nBTC H4 Trend:")
print(f"  Price: {price:.2f}")
print(f"  EMA50: {ema50:.2f}")
print(f"  EMA200: {ema200:.2f}")
print(f"  Trend: {'UP' if ema50 > ema200 else 'DOWN'}")
print(f"  Pullback zone: {ema200:.2f} - {ema50:.2f}")
print(f"  In zone: {ema200 < price < ema50 if ema50 > ema200 else ema50 < price < ema200}")
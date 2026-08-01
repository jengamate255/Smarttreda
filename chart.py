import asyncio
from server import mt5_connection, MT5Config, get_rates, get_symbol_info
import numpy as np


async def get_chart_data(symbol: str = "EURUSD", timeframe: int = 16385, count: int = 100):
    config = MT5Config()
    mt5_connection.connect(config)
    
    class MockContext:
        pass
    
    ctx = MockContext()
    rates = await get_rates(ctx, symbol, timeframe, count)
    tick = await get_symbol_info(ctx, symbol)
    
    return rates, tick


def simple_chart(rates, tick, symbol="EURUSD", timeframe="H1", width=100, height=25):
    if not rates:
        print("No data")
        return
    
    closes = [r["close"] for r in rates]
    highs = [r["high"] for r in rates]
    lows = [r["low"] for r in rates]
    
    # Calculate indicators
    sma20 = np.convolve(closes, np.ones(20)/20, mode='valid')
    sma20 = np.pad(sma20, (len(closes)-len(sma20), 0), mode='edge')
    
    sma50 = np.convolve(closes, np.ones(50)/50, mode='valid')
    sma50 = np.pad(sma50, (len(closes)-len(sma50), 0), mode='edge')
    
    # RSI
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.convolve(gains, np.ones(14)/14, mode='valid')
    avg_loss = np.convolve(losses, np.ones(14)/14, mode='valid')
    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    rsi = np.pad(rsi, (14, 0), mode='edge')
    
    # Price range
    min_price = min(lows)
    max_price = max(highs)
    price_range = max_price - min_price
    
    # Build chart
    chart = []
    for row in range(height):
        price_level = max_price - (row / (height - 1)) * price_range
        line = f"{price_level:.5f} |"
        
        for i in range(min(width, len(closes))):
            idx = len(closes) - width + i
            if idx < 0:
                line += " "
                continue
                
            high = highs[idx]
            low = lows[idx]
            close = closes[idx]
            open_ = rates[idx]["open"]
            
            # Candle body
            body_top = max(open_, close)
            body_bottom = min(open_, close)
            
            if high >= price_level >= low:
                if body_top >= price_level >= body_bottom:
                    # Inside body
                    line += "#" if close >= open_ else "O"
                else:
                    # Wick
                    line += "|"
            elif sma20[idx] >= price_level >= sma20[idx] - price_range/200:
                line += "="
            elif sma50[idx] >= price_level >= sma50[idx] - price_range/200:
                line += "-"
            else:
                line += " "
        chart.append(line)
    
    # Print chart (reversed so highest price at top)
    print(f"\n{symbol} {timeframe} - Bid: {tick.bid:.5f} Ask: {tick.ask:.5f} Spread: {tick.spread}")
    print(f"SMA20: {sma20[-1]:.5f} | SMA50: {sma50[-1]:.5f} | RSI: {rsi[-1]:.1f}")
    print("-" * (width + 12))
    for line in reversed(chart):
        print(line)
    print("-" * (width + 12))
    
    # Volume/RSI mini chart
    print("\nRSI (14):")
    rsi_line = ""
    for i in range(min(width, len(rsi))):
        idx = len(rsi) - width + i
        if idx < 0:
            rsi_line += " "
            continue
        val = rsi[idx]
        if val >= 70:
            rsi_line += "^"
        elif val <= 30:
            rsi_line += "v"
        elif val >= 50:
            rsi_line += "="
        else:
            rsi_line += "-"
    print(f"  70|{rsi_line}|30")
    print(f"    {rsi[-1]:.1f} current")


def multi_tf_chart():
    import asyncio
    
    async def fetch_all():
        config = MT5Config()
        mt5_connection.connect(config)
        
        class MockContext:
            pass
        ctx = MockContext()
        
        tf_map = {"M15": 16385, "H1": 16388, "H4": 16408}
        results = {}
        
        for name, tf in tf_map.items():
            rates = await get_rates(ctx, "EURUSD", tf, 100)
            results[name] = rates
        
        tick = await get_symbol_info(ctx, "EURUSD")
        return results, tick
    
    results, tick = asyncio.run(fetch_all())
    
    for tf_name, rates in results.items():
        if rates:
            simple_chart(rates, tick, "EURUSD", tf_name, width=80, height=15)
            print()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "multi":
        multi_tf_chart()
    else:
        async def main():
            rates, tick = await get_chart_data()
            simple_chart(rates, tick)
        
        asyncio.run(main())
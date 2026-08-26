import asyncio
import json
from server import mt5_connection, MT5Config, get_rates, get_symbol_info
from technical_analysis import (
    to_ohlcv, sma, ema, rsi, macd, bollinger_bands, atr, 
    stochastic, adx, pivot_points, analyze_trend, support_resistance
)
import numpy as np


async def analyze_eurusd():
    config = MT5Config()
    mt5_connection.connect(config)
    
    class MockContext:
        pass
    
    ctx = MockContext()
    
    # Get data for multiple timeframes
    timeframes = {
        "H1": 16385,
        "H4": 16388,
        "D1": 16408,
    }
    
    results = {}
    
    for tf_name, tf_value in timeframes.items():
        rates = await get_rates(ctx, "EURUSD", tf_value, 500)
        ohlcv = to_ohlcv(rates)
        
        close = np.array([c.close for c in ohlcv])
        high = np.array([c.high for c in ohlcv])
        low = np.array([c.low for c in ohlcv])
        open_ = np.array([c.open for c in ohlcv])
        
        # Indicators
        rsi_vals = rsi(close)
        macd_line, signal_line, histogram = macd(close)
        bb_upper, bb_middle, bb_lower = bollinger_bands(close)
        atr_vals = atr(high, low, close)
        stoch_k, stoch_d = stochastic(high, low, close)
        adx_vals = adx(high, low, close)
        
        # Trend
        trend = analyze_trend(close)
        
        # Support/Resistance
        supports, resistances = support_resistance(close, high, low)
        
        # Pivot points (using last completed candle)
        pivots = pivot_points(high[-2], low[-2], close[-2])
        
        # Current values
        current_price = close[-1]
        current_rsi = rsi_vals[-1]
        current_macd = macd_line[-1]
        current_signal = signal_line[-1]
        current_hist = histogram[-1]
        current_atr = atr_vals[-1]
        current_adx = adx_vals[-1]
        stoch_k_val = stoch_k[-1]
        stoch_d_val = stoch_d[-1]
        
        results[tf_name] = {
            "current_price": round(current_price, 5),
            "trend": trend,
            "rsi": round(current_rsi, 2),
            "macd": round(current_macd, 5),
            "macd_signal": round(current_signal, 5),
            "macd_histogram": round(current_hist, 5),
            "bb_upper": round(bb_upper[-1], 5),
            "bb_middle": round(bb_middle[-1], 5),
            "bb_lower": round(bb_lower[-1], 5),
            "atr": round(current_atr, 5),
            "adx": round(current_adx, 2),
            "stoch_k": round(stoch_k_val, 2),
            "stoch_d": round(stoch_d_val, 2),
            "supports": supports,
            "resistances": resistances,
            "pivots": {k: round(v, 5) for k, v in pivots.items()},
        }
    
    # Current tick
    tick = await get_symbol_info(ctx, "EURUSD")
    results["tick"] = {
        "bid": tick.bid,
        "ask": tick.ask,
        "spread": tick.spread
    }
    
    return results


if __name__ == "__main__":
    results = asyncio.run(analyze_eurusd())
    print(json.dumps(results, indent=2))
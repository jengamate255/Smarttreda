import asyncio
from server import mt5_connection, MT5Config, get_rates, get_symbol_info

async def get_market_data(symbol: str = "EURUSD", timeframe: int = 16385, count: int = 200):
    config = MT5Config()
    mt5_connection.connect(config)
    
    class MockContext:
        pass
    
    ctx = MockContext()
    
    # Get rates
    rates = await get_rates(ctx, symbol, timeframe, count)
    
    # Get current tick
    tick = await get_symbol_info(ctx, symbol)
    
    return {
        "symbol": symbol,
        "rates": rates,
        "current_bid": tick.bid,
        "current_ask": tick.ask,
        "spread": tick.spread
    }

if __name__ == "__main__":
    data = asyncio.run(get_market_data())
    print(f"Got {len(data['rates'])} candles for {data['symbol']}")
    print(f"Current: bid={data['current_bid']}, ask={data['current_ask']}")
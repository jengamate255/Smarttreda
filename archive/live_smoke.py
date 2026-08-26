"""Live smoke test for MT5 connection.

Requires a running MT5 terminal and valid credentials in .env.
Run directly: python live_smoke.py
Not a pytest test (needs live terminal).
"""

import asyncio
from server import mt5_connection, MT5Config, get_account_info, get_symbol_info, get_positions, get_rates


async def test():
    config = MT5Config()
    mt5_connection.connect(config)

    class MockContext:
        pass

    ctx = MockContext()

    acc = await get_account_info(ctx)
    print(f'Account: {acc.login}, Balance: {acc.balance}, Equity: {acc.equity}')

    sym = await get_symbol_info(ctx, 'EURUSD')
    print(f'EURUSD: bid={sym.bid}, ask={sym.ask}, spread={sym.spread}')

    pos = await get_positions(ctx)
    print(f'Positions: {len(pos)}')

    rates = await get_rates(ctx, 'EURUSD', 16385, 3)
    print(f'Rates: {len(rates)}')
    for r in rates:
        print(f'  time={r["time"]} O={r["open"]} H={r["high"]} L={r["low"]} C={r["close"]}')


if __name__ == "__main__":
    asyncio.run(test())
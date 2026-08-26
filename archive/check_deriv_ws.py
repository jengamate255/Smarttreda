import asyncio
import websockets
import json

async def test():
    ws = await websockets.connect('wss://ws.derivws.com/websockets/v3?app_id=1089')
    await ws.send(json.dumps({'active_symbols': 'brief', 'product_type': 'basic'}))
    resp = await ws.recv()
    data = json.loads(resp)
    for s in data.get('active_symbols', []):
        sym = s.get('symbol', '')
        if 'CRASH' in sym or 'BOOM' in sym or 'VOLATILITY' in sym:
            print(f"{sym}: {s.get('display_name', '')} tradeable={s.get('tradeable', False)} open={s.get('exchange_is_open', False)}")
    await ws.close()

asyncio.run(test())
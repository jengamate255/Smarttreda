import asyncio
import websockets
import json

async def test():
    ws = await websockets.connect('wss://ws.derivws.com/websockets/v3?app_id=1089')
    
    # Try different product types
    for pt in ['basic', 'financial', 'gaming', 'all']:
        await ws.send(json.dumps({'active_symbols': 'brief', 'product_type': pt}))
        resp = await ws.recv()
        data = json.loads(resp)
        print(f"product_type={pt}: {len(data.get('active_symbols', []))} symbols")
        if data.get('active_symbols'):
            for s in data['active_symbols'][:5]:
                print(f"  {s.get('symbol')}: {s.get('display_name')}")
    await ws.close()

asyncio.run(test())
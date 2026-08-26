import asyncio
import websockets
import json

async def test():
    # Try with token in URL
    token = "REDACTED_DERIV_PAT"
    url = f"wss://ws.derivws.com/websockets/v3?app_id=1089&token={token}"
    ws = await websockets.connect(url)
    
    # Check if already authorized
    await ws.send(json.dumps({'active_symbols': 'brief', 'product_type': 'basic'}))
    resp = await ws.recv()
    data = json.loads(resp)
    print(f"Symbols without authorize: {len(data.get('active_symbols', []))}")
    
    if data.get('active_symbols'):
        for s in data.get('active_symbols', [])[:10]:
            sym = s.get('symbol', '')
            if 'CRASH' in sym or 'BOOM' in sym or 'VOLATILITY' in sym or 'R_' in sym:
                print(f"  {sym}: {s.get('display_name', '')} tradeable={s.get('tradeable', False)} open={s.get('exchange_is_open', False)}")
    
    await ws.close()

asyncio.run(test())
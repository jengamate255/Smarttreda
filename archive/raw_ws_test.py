import asyncio
import json
import websockets

TOKEN = "REDACTED_DERIV_PAT"

async def main():
    url = "wss://ws.derivws.com/websockets/v3?app_id=1089"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"authorize": TOKEN, "req_id": 1}))
        resp = json.loads(await ws.recv())
        print("AUTHORIZE:", json.dumps(resp)[:400])
        if "error" not in resp:
            acct = resp.get("authorize", {})
            print("loginid:", acct.get("loginid"), "| currency:", acct.get("currency"), "| virtual:", acct.get("is_virtual"))

asyncio.run(main())
import os
os.environ["MT5_MCP_ACCOUNT"] = "DERIV"

from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import get_settings

s = get_settings()
deriv_cfg = s.deriv_mt5
client = MT5Client(deriv_cfg)

if client.connect():
    print("Connected to DERIV")
    symbols = client.symbols()
    bc_symbols = [s for s in symbols if 'BOOM' in s.upper() or 'CRASH' in s.upper()]
    print("Boom/Crash symbols:", bc_symbols)
    for sym in bc_symbols[:4]:
        try:
            rates = client.rates(sym, 'H1', 10)
            if rates is not None and len(rates) > 0:
                last = rates.iloc[-1]
                print(f'{sym}: close={last["close"]} time={last["time"]}')
        except Exception as e:
            print(f'{sym}: error={e}')
    client.shutdown()
else:
    print("Failed to connect")
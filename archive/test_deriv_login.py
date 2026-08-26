import os
os.environ['MT5_MCP_MT5_TERMINAL_PATH'] = r'C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe'
os.environ['MT5_MCP_MT5_LOGIN'] = '41204968'
os.environ['MT5_MCP_MT5_PASSWORD'] = 'REDACTED_MT5_PASSWORD$$$'
os.environ['MT5_MCP_MT5_SERVER'] = 'Deriv-Demo'
import sys
sys.path.insert(0, r'E:\Project\Mt5 Ea')
from mt5_mcp.client import get_client, reset_client
reset_client()
c = get_client()
c.ensure_connected()
info = c.account_info()
print(f"Connected: login={info.get('login')} server={info.get('server')} balance={info.get('balance')}")
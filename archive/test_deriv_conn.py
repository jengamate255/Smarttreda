import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
from mt5_mcp.client import get_client, reset_client
reset_client()
c = get_client()
c.ensure_connected()
info = c.account_info()
print(f"Connected: login={info.get('login')} server={info.get('server')} balance={info.get('balance')}")
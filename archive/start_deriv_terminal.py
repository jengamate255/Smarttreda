import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
from mt5_mcp.client import get_client, reset_client
import time

reset_client()
c = get_client("CRASH1000")
c.ensure_connected()
info = c.account_info()
print(f"Account: login={info.get('login')} balance={info.get('balance')} equity={info.get('equity')}")
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")
from mt5_mcp.client import get_client, reset_client
import MetaTrader5 as mt5

# Try to login with the specific account
print("Attempting to login with account 21727841...")
mt5.initialize(path=r"C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe")
result = mt5.login(21727841, password="REDACTED_MT5_PASSWORD$$$", server="Deriv-Demo")
if result:
    info = mt5.account_info()
    print(f"Login successful: login={info.login} balance={info.balance} equity={info.equity} server={info.server}")
else:
    print(f"Login failed: {mt5.last_error()}")

mt5.shutdown()
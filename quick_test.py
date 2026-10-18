import MetaTrader5 as mt5
import sys

# Try with the PROP terminal path
path = r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"
print(f"Trying path: {path}")

result = mt5.initialize(path=path, timeout=10000)
if result:
    info = mt5.account_info()
    print(f"OK! Login={info.login} Server={info.server} Balance={info.balance}")
    mt5.shutdown()
else:
    err = mt5.last_error()
    print(f"Failed: {err}")
    sys.exit(1)

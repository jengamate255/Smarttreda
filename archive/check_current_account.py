import MetaTrader5 as mt5

# Try to connect to already running terminal without initialize
print("Checking current terminal connection...")
mt5.initialize()
info = mt5.account_info()
if info:
    print(f"Current account: login={info.login} balance={info.balance} equity={info.equity} server={info.server}")
else:
    print(f"No account connected: {mt5.last_error()}")
mt5.shutdown()
import MetaTrader5 as mt5
result = mt5.initialize()
print(f'Init: {result}')
if result:
    info = mt5.account_info()
    if info:
        print(f'Account: {info.login} @ {info.server} balance={info.balance}')
    else:
        print(f'No account info: {mt5.last_error()}')
else:
    print(f'Error: {mt5.last_error()}')
mt5.shutdown()
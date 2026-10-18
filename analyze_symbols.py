import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import MetaTrader5 as mt5

if not mt5.initialize():
    print('MT5 init failed')
    exit()

account = mt5.account_info()
print('Account:', account.login, '@', account.server)
print('Balance:', account.balance)
print('Equity:', account.equity)
print()

symbols = mt5.symbols_get()
if symbols:
    print('Total symbols:', len(symbols))
    print()
    
    major_pairs = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDUSD', 'NZDUSD', 'USDCAD', 'USDCHF',
                   'EURGBP', 'EURJPY', 'GBPJPY', 'AUDJPY', 'EURAUD', 'EURCAD', 'GBPCAD',
                   'AUDCAD', 'AUDNZD', 'NZDJPY', 'CADJPY', 'CHFJPY', 'EURCHF']
    
    print('Analyzing major pairs:')
    print('=' * 70)
    header = '{:<12} {:>10} {:>10} {:>8}'.format('Symbol', 'Bid', 'Ask', 'Spread')
    print(header)
    print('-' * 70)
    
    for sym_name in major_pairs:
        info = mt5.symbol_info(sym_name)
        if info and info.visible:
            tick = mt5.symbol_info_tick(sym_name)
            if tick:
                spread = (tick.ask - tick.bid) / info.point if info.point > 0 else 0
                row = '{:<12} {:>10.5f} {:>10.5f} {:>8.1f}'.format(sym_name, tick.bid, tick.ask, spread)
                print(row)
    
    print()
    print('All available symbols with prices:')
    print('-' * 70)
    
    all_symbols = []
    for s in symbols:
        if s.visible:
            tick = mt5.symbol_info_tick(s.name)
            if tick and tick.bid > 0:
                spread = (tick.ask - tick.bid) / s.point if s.point > 0 else 0
                all_symbols.append((s.name, tick.bid, tick.ask, spread))
    
    all_symbols.sort(key=lambda x: x[0])
    for name, bid, ask, spread in all_symbols[:80]:
        row = '{:<12} Bid: {:.5f}  Ask: {:.5f}  Spread: {:.1f}'.format(name, bid, ask, spread)
        print(row)

mt5.shutdown()

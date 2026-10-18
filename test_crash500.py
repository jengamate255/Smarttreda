"""Check available synthetic indices and run backtest."""
import sys
sys.path.insert(0, r"E:\Project\Mt5 Ea")

import MetaTrader5 as mt5

if mt5.initialize():
    print("MT5 initialized successfully")
    
    # Get account info
    account_info = mt5.account_info()
    if account_info:
        print(f"Account: {account_info.login}")
        print(f"Server: {account_info.server}")
        print(f"Balance: {account_info.balance}")
    
    # Get all symbols
    symbols = mt5.symbols_get()
    if symbols:
        print(f"\nTotal symbols: {len(symbols)}")
        
        # Find synthetic indices (Volatility, Boom, Crash, etc.)
        print("\nSynthetic indices available:")
        synthetic = []
        for s in symbols:
            name = s.name
            # Check for common synthetic patterns
            if any(x in name.upper() for x in ["VOLATILITY", "BOOM", "CRASH", "SP500", "SPX", "JP225", "NASDAQ", "US30", "GER40", "FTSE", "STX50"]):
                synthetic.append(name)
                print(f"  {name}")
        
        if not synthetic:
            print("  No synthetic indices found")
            
        # Also check for forex pairs we trade
        print("\nForex pairs we trade:")
        forex_pairs = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD"]
        for pair in forex_pairs:
            try:
                info = mt5.symbol_info(pair)
                if info:
                    print(f"  {pair}: available")
            except:
                print(f"  {pair}: not found")
    
    mt5.shutdown()
else:
    print("Failed to initialize MT5")

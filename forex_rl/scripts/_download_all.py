"""Download real MT5 data for key forex pairs."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forex_rl.src.data.loader import DataLoader
from forex_rl.src.data.cleaner import DataCleaner

loader = DataLoader()
cleaner = DataCleaner()

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
TIMEFRAMES = ["M15", "H1", "H4"]
START = "2020-01-01"
END = "2025-08-31"

for symbol in SYMBOLS:
    for tf in TIMEFRAMES:
        print(f"\n{'='*50}")
        print(f"Downloading {symbol} {tf} [{START} to {END}]")
        try:
            df = loader.load_from_mt5(symbol, tf, START, END)
            df = cleaner.clean(df)
            filepath = f"forex_rl/data/raw/{symbol}_{tf}.parquet"
            loader.save_to_parquet(df, filepath)
            print(f"  Saved {len(df)} bars to {filepath}")
            print(f"  Date range: {df.index[0]} to {df.index[-1]}")
            print(f"  Close range: {df['close'].min():.5f} to {df['close'].max():.5f}")
        except Exception as e:
            print(f"  ERROR: {e}")

print("\n" + "="*50)
print("DOWNLOAD COMPLETE")

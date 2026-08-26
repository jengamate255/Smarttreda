import sys
sys.path.insert(0, '.')

from mt5_mcp.mt5_client import MT5Client
from mt5_mcp.config import MT5Config

# Connect to Prop terminal
config = MT5Config(
    terminal_path=r'C:\Program Files\MetaTrader 5 Terminal\terminal64.exe',
    login=None,
    password='',
    server=''
)
client = MT5Client(config)
if client.connect():
    print('Connected to Prop terminal')
    # Check for BOOM/CRASH symbols
    all_symbols = client.symbols()
    print(f'Total symbols: {len(all_symbols)}')
    
    boom_symbols = [s for s in all_symbols if 'BOOM' in s.upper() or 'BOOM' in s]
    crash_symbols = [s for s in all_symbols if 'CRASH' in s.upper() or 'CRASH' in s]
    synthetic_symbols = [s for s in all_symbols if any(x in s.upper() for x in ['BOOM', 'CRASH', 'JUMP', 'STEP', 'VOLATILITY', 'VIX'])]
    
    print(f'BOOM symbols: {boom_symbols}')
    print(f'CRASH symbols: {crash_symbols}')
    print(f'All synthetic-like: {synthetic_symbols}')
    
    # Also check for any symbols containing "index" or "synthetic"
    other = [s for s in all_symbols if 'INDEX' in s.upper() or 'SYNTHETIC' in s.upper()]
    print(f'Other index/synthetic: {other[:20]}')
else:
    print(f'Failed to connect: {client.last_error()}')
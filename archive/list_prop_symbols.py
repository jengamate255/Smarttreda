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
    all_symbols = client.symbols()
    print(f'Total symbols: {len(all_symbols)}')
    for s in sorted(all_symbols):
        print(f'  {s}')
else:
    print(f'Failed to connect: {client.last_error()}')
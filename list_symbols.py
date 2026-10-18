"""List available tradeable symbols."""
import sys, json
sys.path.insert(0, "E:/Project/Mt5 Ea")
from dotenv import load_dotenv
load_dotenv("E:/Project/Mt5 Ea/.env")
from mt5_mcp.client import get_client

client = get_client()
client.connect()
symbols = client.symbols()
print(f"Type: {type(symbols)}, count: {len(symbols)}")
if symbols:
    print(f"First element type: {type(symbols[0])}")
    print(f"First 5: {symbols[:5]}")

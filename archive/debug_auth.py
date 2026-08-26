import sys
import json

sys.path.insert(0, '.')

from mt5_mcp.deriv_client import DerivClient
from mt5_mcp.config import DerivConfig

TOKEN = "REDACTED_DERIV_PAT"

config = DerivConfig(
    enabled=True,
    ws_url="wss://ws.derivws.com/websockets/v3",
    app_id=1089,
    api_token=TOKEN,
)
client = DerivClient(config)
if not client.connect():
    print("CONNECT FAILED:", client.last_error())
    sys.exit(1)

print("=== AUTHORIZE RAW ===")
try:
    auth = client.authorize()
    print(json.dumps(auth, indent=1)[:1000])
except Exception as e:
    print("AUTHORIZE ERROR:", e)

print("\n=== CONTRACTS FOR BOOM900 RAW ===")
try:
    cf = client.contracts_for("BOOM900")
    print(json.dumps(cf, indent=1)[:2000])
except Exception as e:
    print("CONTRACTS ERROR:", e)
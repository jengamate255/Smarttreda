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

print("=== AUTHORIZE ===")
try:
    auth = client.authorize()
    if "error" in auth:
        print("AUTH ERROR:", json.dumps(auth["error"]))
        sys.exit(1)
    acct = auth.get("authorize", {})
    print("loginid:", acct.get("loginid"))
    print("currency:", acct.get("currency"))
    print("is_virtual:", acct.get("is_virtual"))
    print("email:", acct.get("email"))
    print("balance:", acct.get("balance"))
    print("landing_company:", acct.get("landing_company_name"))
except Exception as e:
    print("AUTHORIZE ERROR:", e)
    sys.exit(1)

print("\n=== BALANCE ===")
try:
    bal = client.balance()
    print(json.dumps(bal, indent=1)[:400])
except Exception as e:
    print("BALANCE ERROR:", e)

print("\n=== CONTRACTS BOOM900 ===")
try:
    cf = client.contracts_for("BOOM900")
    if "error" in cf:
        print("ERR:", json.dumps(cf["error"]))
    else:
        c = cf.get("contracts_for", {})
        print("symbol:", c.get("symbol"))
        for hit in c.get("hit", []):
            print(" ", hit.get("contract_type"), "|", hit.get("display_name"))
except Exception as e:
    print("CONTRACTS ERROR:", e)

print("\n=== CONTRACTS CRASH600 ===")
try:
    cf = client.contracts_for("CRASH600")
    if "error" in cf:
        print("ERR:", json.dumps(cf["error"]))
    else:
        c = cf.get("contracts_for", {})
        print("symbol:", c.get("symbol"))
        for hit in c.get("hit", []):
            print(" ", hit.get("contract_type"), "|", hit.get("display_name"))
except Exception as e:
    print("CONTRACTS ERROR:", e)
"""Read-only, cost-adjusted baseline for the current consensus symbol universe."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)
from mt5_mcp.config import get_settings, MT5Config
from mt5_mcp.mt5_client import MT5Client

settings = get_settings()
connection = MT5Config(**{key: os.getenv("MT5_"+env) or os.getenv("MT5_MCP_MT5_"+env) or getattr(settings.mt5,key)
    for key,env in (("login","LOGIN"),("password","PASSWORD"),("server","SERVER"),("terminal_path","TERMINAL_PATH"))})
if not connection.is_configured:
    raise RuntimeError("Read-only backtest account credentials are not configured")
native = MT5Client(connection)
if not native.connect():
    raise RuntimeError("Cannot connect the read-only backtest client")
account = native.account_info()
if connection.login and account.get("login") != connection.login:
    raise RuntimeError("Backtest account does not match the configured account")
from mt5_mcp.client import set_client
set_client(native)

spec = importlib.util.spec_from_file_location("quality_baseline_atlas", ROOT / "mt5_mcp/research/atlas.py")
atlas = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = atlas
spec.loader.exec_module(atlas)
atlas.OUT_DIR = ROOT / "reports/quality_baseline_20261013"
atlas.OUT_DIR.mkdir(parents=True, exist_ok=True)
fetch = atlas._deep_frame


def guarded_frame(symbol, timeframe):
    current = atlas.mt5.account_info()
    if current is None or current.login != account["login"]:
        raise RuntimeError("Account changed during read-only history fetch")
    return fetch(symbol, timeframe)


atlas._deep_frame = guarded_frame
disk = json.loads((ROOT / "mt5_mcp/settings.json").read_text(encoding="utf-8"))
symbols = disk["consensus"]["allowed_symbols"]
print("Baseline only: routed/candidate strategy families, not the complete quality-mode executor.", flush=True)
raise SystemExit(atlas.main(["--symbols", ",".join(symbols), "--no-notify"]))

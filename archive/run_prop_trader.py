"""Run one guarded prop-firm SIM evaluation using the project .env profile."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

for source, target in (
    ("MT5_LOGIN", "MT5_MCP_MT5_LOGIN"),
    ("MT5_PASSWORD", "MT5_MCP_MT5_PASSWORD"),
    ("MT5_SERVER", "MT5_MCP_MT5_SERVER"),
    ("MT5_PATH", "MT5_MCP_MT5_TERMINAL_PATH"),
):
    if os.getenv(source):
        os.environ[target] = os.environ[source]
os.environ.setdefault(
    "MT5_MCP_MT5_TERMINAL_PATH",
    r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
)
os.environ.setdefault("MT5_AUTOTRADER_ENABLED", "1")
os.environ.setdefault("MT5_AUTOTRADER_MODE", "SIM")
os.environ.setdefault("MT5_AUTOTRADER_EXPECTED_LOGIN", "11911448")
os.environ.setdefault("MT5_MCP_MAGIC", "235010")

from mt5_mcp.auto_trader import run_once  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="allow accepted plans to send orders")
    args = parser.parse_args()
    os.environ["MT5_AUTOTRADER_EXECUTE"] = "1" if args.live else "0"
    return run_once(dry_run=not args.live)


if __name__ == "__main__":
    raise SystemExit(main())

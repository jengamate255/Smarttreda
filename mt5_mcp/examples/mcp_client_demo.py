"""Demo MCP client: connects to the MT5 server over stdio and runs a strategy check.

Usage:
    python mcp_client_demo.py EURUSD
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

SERVER = str(Path(__file__).resolve().parent.parent / "server.py")


def payload(result) -> dict:
    raw = result.data if hasattr(result, "data") else None
    if raw is None and result.content:
        raw = result.content[0].text
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "__dict__"):  # fastmcp generated Root wrapper
        return dict(raw.__dict__)
    return raw


async def main() -> None:
    symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "EURUSD"
    transport = StdioTransport(sys.executable, [SERVER])
    async with Client(transport) as client:
        tools = await client.list_tools()
        print(f"Connected. {len(tools)} tools available.\n")

        health = await client.call_tool("health", {})
        print("HEALTH:", payload(health), "\n")

        plan = await client.call_tool("evaluate_strategy", {"symbol": symbol})
        data = payload(plan)
        print(
            f"STRATEGY {symbol}: {data.get('action')} valid={data.get('valid')} "
            f"score={data.get('trade_score')} confidence={data.get('confidence')}%"
        )
        print(
            f"  entry={data.get('entry')} sl={data.get('sl')} tp={data.get('tp')} "
            f"rr={data.get('risk_reward')} lots={data.get('lots')}"
        )
        print(f"  reason: {data.get('reason')}")

        zones = await client.call_tool(
            "detect_zones", {"symbol": symbol, "timeframe": "H1", "min_strength": 70}
        )
        zone_list = payload(zones)
        print(f"\nTOP ZONES (H1): {zone_list.get('count', 0)}")
        for zone in zone_list.get("zones", [])[:3]:
            print(
                f"  {zone['type']} {zone['lower']:.5f}-{zone['upper']:.5f} "
                f"strength={zone['strength']} touches={zone['touches']} broken={zone['broken']}"
            )


if __name__ == "__main__":
    asyncio.run(main())

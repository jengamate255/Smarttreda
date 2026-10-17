"""MCP server launcher - sets env before import."""
import os
os.environ['MCP_TRANSPORT'] = 'streamable-http'
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from mt5_mcp.server import main
main()

@echo off
set MCP_TRANSPORT=streamable-http
python -c "import sys, os; sys.path.insert(0, 'E:\Project\Mt5 Ea'); os.environ['MCP_TRANSPORT'] = 'streamable-http'; from mt5_mcp.server import main; main()"
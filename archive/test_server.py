import sys
sys.path.insert(0, r'E:\Project\Mt5 Ea')
import os
os.environ['MCP_TRANSPORT'] = 'streamable-http'
from mt5_mcp.server import main
main()
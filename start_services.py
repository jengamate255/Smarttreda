"""Quick launcher - non-blocking."""
import subprocess, os, sys, socket, time

os.chdir(os.path.dirname(os.path.abspath(__file__)))

def port_open(port):
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(('127.0.0.1', port))
        s.close()
        return True
    except:
        s.close()
        return False

if not port_open(9000):
    env = os.environ.copy()
    env['MCP_TRANSPORT'] = 'streamable-http'
    subprocess.Popen(
        [sys.executable, '-m', 'mt5_mcp.server'],
        cwd=os.getcwd(), env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("MCP server launching...")

if not port_open(8050):
    subprocess.Popen(
        [sys.executable, 'web_dashboard.py'],
        cwd=os.getcwd(),
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("Dashboard launching...")

time.sleep(4)
print(f"MCP: {'UP' if port_open(9000) else 'DOWN'}")
print(f"Dashboard: {'UP' if port_open(8050) else 'DOWN'}")

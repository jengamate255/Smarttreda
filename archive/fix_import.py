#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'r', errors='ignore') as f:
    content = f.read()

old_import = """    from mt5_mcp.self_tune import current_symbol
    from mt5_mcp.technical_analysis import compute_rsi, compute_macd, compute_ema"""

new_import = """    from mt5_mcp.self_tune import current_symbol"""

if old_import in content:
    content = content.replace(old_import, new_import)
    with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'w', errors='ignore') as f:
        f.write(content)
    print("Import fixed")
else:
    print("Import line not found")
#!/usr/bin/env python
import sys
sys.path.insert(0, r'E:\Project\Mt5 Ea')

with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'r', errors='ignore') as f:
    lines = f.readlines()

# Find /api/quote and add price endpoint after it
quote_idx = None
for i, line in enumerate(lines):
    if '@app.get("/api/quote")' in line:
        quote_idx = i
        break

if quote_idx:
    print(f"/api/quote at line {quote_idx+1}")
    
    # Find the end of the /api/quote function
    insert_idx = len(lines)  # Default: append at end
    
    for i in range(quote_idx + 1, min(quote_idx + 50, len(lines))):
        line = lines[i].strip()
        # Check if this starts a new function
        if line.startswith('def ') and i > quote_idx + 3:
            insert_idx = i
            print(f"Next function at line {i+1}, inserting before it")
            break
    
    # The price endpoint code
    price_code = '''\
@app.get("/api/price")
def get_price(symbol: str = Query("BOOM150")) -> dict:
    \"\"\"Get latest tick price for a symbol.
    
    Returns bid, ask, spread, and timestamp.
    Used by the frontend for real-time price display and chart updates.
    \"\"\"
    from mt5_mcp.client import get_client
    
    try:
        client = get_client()
        tick = client.tick(symbol)
        
        if tick:
            bid = float(tick.get("bid", 0.0))
            ask = float(tick.get("ask", 0.0))
            spread = ask - bid if bid > 0 else 0.0
            
            return {
                "symbol": symbol,
                "bid": bid,
                "ask": ask,
                "spread": spread,
                "time": tick.get("time", 0),
            }
    except Exception:
        pass
    
    return {
        "symbol": symbol,
        "bid": 0.0,
        "ask": 0.0,
        "spread": 0.0,
        "time": 0,
    }
'''
    
    # Insert
    new_lines = lines[:insert_idx] + [price_code + '\n'] + lines[insert_idx:]
    
    with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'w', errors='ignore') as f:
        f.writelines(new_lines)
    
    print(f"Price endpoint inserted at line {insert_idx}")
    print(f"Original: {len(lines)} lines, New: {len(new_lines)} lines")
else:
    print("/api/quote not found")
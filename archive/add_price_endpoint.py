#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'r', errors='ignore') as f:
    content = f.read()

# Add a new endpoint /api/price that returns latest tick data
# This will be used by the frontend for real-time price updates

price_endpoint = '''

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
    
    # Return default values if MT5 not available
    return {
        "symbol": symbol,
        "bid": 0.0,
        "ask": 0.0,
        "spread": 0.0,
        "time": 0,
    }


'''

# Find a good place to insert - after the /api/quote endpoint
# /api/quote is around line 1020ish
quote_idx = None
for i, line in enumerate(lines):
    if '@app.get("/api/quote")' in line:
        quote_idx = i
        break

if quote_idx:
    # Insert after the /api/quote endpoint function
    # Find the end of the /api/quote function
    insert_idx = quote_idx + 1
    # Find the next function or blank line
    for i in range(quote_idx + 1, min(quote_idx + 30, len(lines))):
        if lines[i].strip().startswith('def ') or (lines[i].strip() == '' and i > quote_idx + 5):
            insert_idx = i
            break
    
    # Actually, let me just find /api/quote and add after its return
    # Let me search for the /api/quote function content
    print(f"/api/quote found at line {quote_idx+1}")
    
    # Insert the price endpoint after /api/quote
    new_lines = lines[:quote_idx + 1] + [price_endpoint] + lines[quote_idx + 1:]
    
    with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'w', errors='ignore') as f:
        f.writelines(new_lines)
    
    print(f"Price endpoint added after /api/quote")
    print(f"Original lines: {len(lines)}, New lines: {len(new_lines)}")
else:
    print("/api/quote not found, trying alternative approach")
    # Try adding at the end of the file
    new_lines = lines + [
        '',
        price_endpoint,
    ]
    with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'w', errors='ignore') as f:
        f.writelines(new_lines)
    print("Price endpoint added at end of file")
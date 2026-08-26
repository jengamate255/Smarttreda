#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'r', errors='ignore') as f:
    lines = f.readlines()

# Find the _ws_broadcast_loop function and add price streaming
# The function is at line 1537 (0-indexed: 1536)

# Look for the exact structure
# The function sends: payload = await asyncio.to_thread(_summary_snapshot)
# We want to also send price updates

# Find line 1537 (1-indexed)
ws_broadcast_start = None
for i, line in enumerate(lines):
    if '_ws_broadcast_loop' in line and i > 1530:
        ws_broadcast_start = i
        print(f'Found _ws_broadcast_loop at line {i+1}')
        break

if ws_broadcast_start is None:
    print("Could not find _ws_broadcast_loop")
    # Try finding it differently
    for i, line in enumerate(lines):
        if 'async def _ws_broadcast_loop' in line:
            ws_broadcast_start = i
            break

if ws_broadcast_start:
    # The function body starts after the def line
    # Let's find where the actual work begins
    # Line 1538: while True:
    # We want to add price streaming after the existing payload send
    
    # Let me look at the structure more carefully
    # The function has:
    # - while True loop
    # - asyncio.sleep(WS_INTERVAL)
    # - if not _ws_clients: continue
    # - payload = await asyncio.to_thread(_summary_snapshot)
    # - notify list building
    # - for ws in list(_ws_clients): await ws.send_json(payload)
    
    # I want to add price update sending after the main payload send
    # Or I could modify the payload to include price data
    
    # Let me check what _summary_snapshot returns
    print("\\nLooking at _summary_snapshot function...")
    for i, line in enumerate(lines):
        if '_summary_snapshot' in line and i > 800:
            print(f'Line {i+1}: {line[:80]}')
            # Show next 10 lines
            for j in range(i+1, min(i+15, len(lines))):
                print(f'Line {j+1}: {lines[j][:80]}', end='')
            break

# Instead of modifying the complex WebSocket loop, 
# let me add a simpler approach: 
# Add a new endpoint /api/price-update that returns latest tick data
# And modify the frontend to poll it or use WebSocket for price updates

# Actually, the simpler approach is to modify the existing WebSocket
# to also send price data. Let me check what data is available.

# Let me just add price update sending in the broadcast loop
# I'll modify the code to also send a price_update message

# Find the line where payload is sent to clients
send_line = None
for i in range(ws_broadcast_start, min(ws_broadcast_start + 80, len(lines))):
    if 'await ws.send_json(payload)' in lines[i]:
        send_line = i
        print(f"Found send_json at line {i+1}")
        break

if send_line:
    # Insert a price update send after the main payload send
    # But I need to be careful about the structure
    
    # Let me look at the context around send_line
    for i in range(max(0, send_line-5), min(len(lines), send_line+5)):
        print(f"Line {i+1}: {lines[i][:80]}", end='')
    
    # I'll add price update sending. The idea is:
    # After sending the main payload, also send price updates if available
    # I'll modify the code to include price data in the payload or send separately
    
    print(f"\nWill add price streaming after line {send_line+1}")
else:
    print("Could not find send_json line")
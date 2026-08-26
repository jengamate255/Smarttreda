#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\static\app.js', 'r', errors='ignore') as f:
    lines = f.readlines()

# Remove the old startQuoteTimer function and add updatePriceDisplay
# The old function starts at line 1781 (1-indexed), which is index 1780

# Find the old startQuoteTimer - it uses QUOTE_MS
old_func_start = None
for i, line in enumerate(lines):
    if 'QUOTE_MS' in line and 'state.quoteTimer' in line and i > 1750:
        old_func_start = i
        print(f"Old startQuoteTimer at line {i+1}")
        break

if old_func_start:
    # Find where the old function ends (closing brace)
    brace_count = 0
    old_func_end = old_func_start
    for i in range(old_func_start, min(old_func_start + 50, len(lines))):
        for char in lines[i]:
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    old_func_end = i + 1
                    print(f"Old function ends at line {old_func_end}")
                    break
        if old_func_end > old_func_start:
            break
    
    # Remove the old function (from old_func_start to old_func_end)
    new_lines = lines[:old_func_start] + lines[old_func_end:]
    print(f"Removed old startQuoteTimer ({old_func_end - old_func_start} lines)")
    
    # Now find where to add updatePriceDisplay - after the new startQuoteTimer
    # The new startQuoteTimer should be before the old one we removed
    # Let me find it
    new_func_start = None
    for i, line in enumerate(new_func_start := new_lines, 1):
        if 'setInterval(updatePriceDisplay' in line:
            print(f"updatePriceDisplay already at line {i}")
            break
    
    # Actually, let me just add the updatePriceDisplay function at the end of the file, 
    # or after the new startQuoteTimer
    
    # Find a good place - after the WebSocket latency function or near other functions
    # Let me add it after the renderSignalMarkers function
    
    # First, let me check if updatePriceDisplay already exists
    if 'function updatePriceDisplay' in '\n'.join(new_lines):
        print("updatePriceDisplay already exists")
    else:
        # Add the updatePriceDisplay function
        update_func = '''\nfunction updatePriceDisplay() {
  if (!state.data || !state.symbol) return;
  
  try {
    const priceResp = await api("/api/price?symbol=" + encodeURIComponent(state.symbol));
    if (priceResp && priceResp.bid > 0) {
      const t = priceResp;
      $("chart-tick").textContent = "Bid " + fmtPrice(t.bid) + " Ask " + fmtPrice(t.ask) + " Spread " + t.spread;
      
      // Update the last candle on the chart if data exists
      if (candleSeries && state.data.times && state.data.times.length > 0) {
        const lastIdx = state.data.times.length - 1;
        state.lastTick = {
          bid: t.bid,
          ask: t.ask,
          spread: t.spread,
          time: Date.now()
        };
      }
    }
  } catch (e) {
    console.debug("Price update failed:", e);
  }
}

// Schedule periodic price updates
setInterval(updatePriceDisplay, 3000);
'''
        
        # Insert after the new startQuoteTimer or at a sensible location
        # Let me insert it after the renderSignalMarkers function
        markers_idx = None
        for i, line in enumerate(new_lines):
            if 'function renderSignalMarkers' in line:
                markers_idx = i
                break
        
        if markers_idx:
            # Insert after renderSignalMarkers (find the end)
            insert_idx = markers_idx + 1
            for i in range(markers_idx + 1, min(markers_idx + 30, len(new_lines))):
                if 'setMarkersCommon(markers);' in new_lines[i]:
                    insert_idx = i + 1
                    break
            
            final_lines = new_lines[:insert_idx] + [update_func] + new_lines[insert_idx:]
            print(f"Added updatePriceDisplay after renderSignalMarkers")
        else:
            # Just append at the very end
            final_lines = new_lines + [update_func]
            print("Appending updatePriceDisplay at end")
        
        print(f"Final: {len(final_lines)} lines")
        
        with open(r'E:\Project\Mt5 Ea\static\app.js', 'w', errors='ignore') as f:
            f.write('\n'.join(final_lines))
        print("Fixed: removed duplicate and added updatePriceDisplay")
else:
    print("Could not find old startQuoteTimer")
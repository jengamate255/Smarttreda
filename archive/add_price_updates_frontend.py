#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\static\app.js', 'r', errors='ignore') as f:
    content = f.read()

# Add price update function and modify startQuoteTimer
# I'll add a new function updatePriceDisplay() and modify startQuoteTimer

# First, let me find where to add the price update function
# I'll add it after the renderSignalMarkers function

price_update_func = '''

function updatePriceDisplay() {
  if (!state.data || !state.symbol) return;
  
  try {
    const priceResp = await api(\"/api/price?symbol=\" + encodeURIComponent(state.symbol));
    if (priceResp && priceResp.bid > 0) {
      const t = priceResp;
      $("chart-tick").textContent = \`Bid \${fmtPrice(t.bid)}  Ask \${fmtPrice(t.ask)}  Spread \${t.spread}\`;
      
      // Update the last candle on the chart if data exists
      if (candleSeries && state.data.times && state.data.times.length > 0) {
        const lastIdx = state.data.times.length - 1;
        const lastClose = state.data.close[lastIdx];
        
        // Update the last candle's close price
        // For simplicity, just track the latest price without reloading the whole chart
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
setInterval(updatePriceDisplay, 3000); // 3 second interval
'''

# Find where to insert - after renderSignalMarkers function
# Let me find renderSignalMarkers
markers_idx = None
for i, line in enumerate(content.split('\n')):
    if 'function renderSignalMarkers' in line:
        markers_idx = i
        break

if markers_idx:
    # Insert after the renderSignalMarkers function
    # Find the end of the function (look for the next function or end of file)
    insert_idx = markers_idx + 1
    for i in range(markers_idx + 1, min(markers_idx + 30, len(content.split('\n')))):
        if 'function ' in content.split('\n')[i] and i > markers_idx + 3:
            insert_idx = i
            break
    
    # Actually, let me just insert after the startQuoteTimer function definition
    # Or better, add the setInterval at the end of the file
    
    # Let me take a simpler approach: add the setInterval call at the end of the file,
    # and modify the startQuoteTimer to also fetch prices
    
    print("Will modify startQuoteTimer instead")
else:
    print("Could not find renderSignalMarkers, will modify startQuoteTimer")
    
# Modify startQuoteTimer to also fetch price data
# Find the startQuoteTimer function
qs_idx = None
for i, line in enumerate(content.split('\n')):
    if 'function startQuoteTimer' in line:
        qs_idx = i
        break

if qs_idx:
    print(f"startQuoteTimer at line {qs_idx+1}")
    
    # Get the current startQuoteTimer function content
    # I'll replace the function with an enhanced version
    
    enhanced_qs = '''function startQuoteTimer() {
  if (state.quoteTimer) clearInterval(state.quoteTimer);
  state.quoteTimer = setInterval(async () => {
    if (document.hidden) return;
    try {
      const t = await api("/api/quote?symbol=" + encodeURIComponent(state.symbol));
      $("chart-tick").textContent = "Bid " + fmtPrice(t.bid) + " Ask " + fmtPrice(t.ask) + " Spread " + t.spread;
      if (state.lastBar && candleSeries && state.data) {
        const close = state.symbol.indexOf("XAU") >= 0 || state.symbol.indexOf("XAG") >= 0 ? t.ask : t.bid;
        candleSeries.update({
          ...state.lastBar,
          high: Math.max(state.lastBar.high, close),
          low: Math.min(state.lastBar.low, close),
          close,
        });
      }
    } catch (e) {
      $("chart-tick").textContent = "No data for " + state.symbol;
    }
  }, 3000);
  
  // Also fetch price data for display
  setInterval(updatePriceDisplay, 3000);
}
'''
    
    # Split the content into lines and replace
    lines_arr = content.split('\n')
    
    # Find the startQuoteTimer function and replace it
    # Find where startQuoteTimer starts
    start_idx = None
    for i in range(qs_idx, min(qs_idx + 2, len(lines_arr))):
        if 'function startQuoteTimer' in lines_arr[i]:
            start_idx = i
            break
    
    if start_idx:
        # Find where the function ends (next function or end)
        end_idx = start_idx + 1
        for i in range(start_idx + 1, min(start_idx + 30, len(lines_arr))):
            if lines_arr[i].strip().startswith('function ') and i > start_idx + 3:
                end_idx = i
                break
        
        # Replace the function
        new_lines = lines_arr[:start_idx] + [enhanced_qs + '\n'] + lines_arr[end_idx:]
        content = '\n'.join(new_lines)
        print(f"Replaced startQuoteTimer function")
    else:
        print("Could not find startQuoteTimer function start")
#!/usr/bin/env python
import re

with open(r'E:\Project\Mt5 Ea\static\app.js', 'r', errors='ignore') as f:
    content = f.read()

# Remove duplicate startQuoteTimer functions and keep only the enhanced one
# Keep the enhanced one (the one that uses /api/quote and 3000ms), remove the old one

# Find both startQuoteTimer functions
# Pattern 1: the old one with QUOTE_MS
# Pattern 2: the enhanced one with 3000 and /api/price

# I'll replace ALL startQuoteTimer functions with just the enhanced one

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
  
  // Fetch price data for display (new price endpoint)
  setInterval(updatePriceDisplay, 3000);
}
'''

# Replace all occurrences of startQuoteTimer with the enhanced version
# Use a careful regex that matches the function body
pattern = r'function startQuoteTimer\(\) \{[^}]*\}[^}]*\}'
match = re.search(pattern, content, re.DOTALL)
if match:
    new_content = content[:match.start()] + enhanced_qs + content[match.end():]
    print(f"Replaced startQuoteTimer, removed {match.end() - match.start()} chars")
else:
    print("Could not match startQuoteTimer pattern")
    # Try simpler approach - just append the enhanced function
    new_content = content + '\n' + enhanced_qs

# Add the updatePriceDisplay function before startQuoteTimer, or after renderSignalMarkers
# Let me add it after the drawMarkers function area

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

# Insert the updatePriceDisplay function right before startQuoteTimer
# Find startQuoteTimer and insert before it
idx = new_content.find('function startQuoteTimer()')
if idx >= 0:
    # Insert before startQuoteTimer
    new_content = new_content[:idx] + update_func + '\n' + new_content[idx:]
    print(f"Inserted updatePriceDisplay before startQuoteTimer")
else:
    # Append at the end
    new_content = new_content + update_func
    print("Appended updatePriceDisplay at end")

with open(r'E:\Project\Mt5 Ea\static\app.js', 'w', errors='ignore') as f:
    f.write(new_content)

print("App.js cleaned up")
print(f"startQuoteTimer count: {new_content.count('function startQuoteTimer')}")
print(f"updatePriceDisplay count: {new_content.count('function updatePriceDisplay')}")
#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\static\app.js', 'r', errors='ignore') as f:
    content = f.read()

# Enhanced startQuoteTimer that also fetches price data
enhanced_qs = """function startQuoteTimer() {
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
"""

# Find and replace startQuoteTimer
if 'function startQuoteTimer() {' in content:
    # Find the function and replace it using string operations
    start_marker = 'function startQuoteTimer() {'
    end_marker = '}'
    
    start_idx = content.find(start_marker)
    if start_idx >= 0:
        # Find the matching closing brace
        # Count braces to find the end of the function
        brace_count = 0
        end_idx = start_idx
        for i in range(start_idx, len(content)):
            for char in content[i]:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break
            if brace_count == 0:
                break
        
        # Replace the function
        new_content = content[:start_idx] + enhanced_qs + content[end_idx:]
        
        with open(r'E:\Project\Mt5 Ea\static\app.js', 'w', errors='ignore') as f:
            f.write(new_content)
        
        print("startQuoteTimer enhanced successfully")
    else:
        print("Could not find start of startQuoteTimer")
else:
    print("startQuoteTimer function not found")
#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\static\app.js', 'r', errors='ignore') as f:
    lines = f.readlines()

# Find and replace the broken updatePriceDisplay function structure
# The issue is lines 1776-1781 have incorrect brace placement

# Replace lines 1776-1781 (0-indexed: 1775-1780) with correct try-catch structure
new_structure = '''\\
1776: };
1777: } catch (e) {
1778:   console.debug("Price update failed:", e);
1779: }
1780: '''
  
# Actually, let me just rewrite the whole function properly
# I'll replace from line 1758 (async function) through line 1784 (setInterval)

# The correct structure:
# async function updatePriceDisplay() {
#   if (!state.data || !state.symbol) return;
#   try {
#     const priceResp = await api("/api/price?symbol=" + encodeURIComponent(state.symbol));
#     if (priceResp && priceResp.bid > 0) {
#       const t = priceResp;
#       $("chart-tick").textContent = "Bid " + fmtPrice(t.bid) + " Ask " + fmtPrice(t.ask) + " Spread " + t.spread;
#       
#       // Update the last candle on the chart if data exists
#       if (candleSeries && state.data.times && state.data.times.length > 0) {
#         const lastIdx = state.data.times.length - 1;
#         state.lastTick = {
#           bid: t.bid,
#           ask: t.ask,
#           spread: t.spread,
#           time: Date.now()
#         };
#       }
#     }
#   } catch (e) {
#     console.debug("Price update failed:", e);
#   }
# }
# 
# // Schedule periodic price updates
# setInterval(updatePriceDisplay, 3000);

# Let me find the exact range to replace
# Line 1758 is 'async function updatePriceDisplay() {'
# I need to replace from line 1758 through line 1784

# Let me just replace lines 1758-1784
replace_from = 1757  # 0-indexed: line 1758
replace_to = 1784    # 0-indexed: line 1785 (setInterval line)

# The new content to insert
new_func = '''\\
async function updatePriceDisplay() {
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

# Replace the lines
new_lines = lines[:replace_from] + [new_func.strip() + '\n'] + lines[replace_to + 1:]

with open(r'E:\Project\Mt5 Ea\static\app.js', 'w', errors='ignore') as f:
    f.writelines(new_lines)

print(f"Fixed updatePriceDisplay function")
print(f"Original lines: {len(lines)}, New lines: {len(new_lines)}")
PYEOF
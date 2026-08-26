#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\static\app.js', 'r', errors='ignore') as f:
    lines = f.readlines()

# Find the loadChart function and modify it to fetch and display signals
# The loadChart function starts at line 1687 (0-indexed: 1686)

# We want to add signal fetching after the bot analysis and before setSeriesData
# Insert after line 1707 (setSeriesData call) and before line 1708 (startQuoteTimer)

# The new code to insert - added after the existing bot fetch but before setSeriesData
new_code = '''
    // Fetch trading signals
    try {
      const sigResp = await api(`/api/signals?symbol=${encodeURIComponent(state.symbol)}&timeframe=${state.timeframe}&count=${state.count}`);
      state.signals = sigResp.signals || [];
    } catch (e) {
      state.signals = [];
      console.warn("Could not fetch signals:", e);
    }
    
    // Render signal markers on the chart
    renderSignalMarkers();
'''

# Find the line with 'setSeriesData();' and insert after it
# Also need to add the renderSignalMarkers function

# First, let's find the exact insertion point
insert_idx = None
for i, line in enumerate(lines):
    if 'setSeriesData();' in line and i > 1700 and i < 1715:
        insert_idx = i + 1  # Insert after this line
        break

if insert_idx is None:
    print("Could not find insertion point")
    # Fallback: append at end of loadChart
    # Find the closing brace of loadChart
    for i in range(1700, min(1720, len(lines))):
        if '})' in lines[i] and i > 1705:
            insert_idx = i  # Insert before the closing
            break

if insert_idx:
    # Insert the new code
    new_lines = lines[:insert_idx] + [new_code] + lines[insert_idx:]
    
    # Also need to add the renderSignalMarkers function
    # Find where to insert it - after drawMarkers function
    markers_func = '''
function renderSignalMarkers() {
  const d = state.data, signals = state.signals;
  const markers = [];
  
  if (!signals || !d || !candleSeries) return;
  
  for (const s of signals) {
    if (s.type === "parameter") {
      // Draw parameter indicator - a triangle showing current config
      const color = s.strength > 0.5 ? C.up : C.down;
      markers.push({
        time: d.times[Math.floor(d.times.length / 2)],
        position: "belowBar",
        color: color,
        shape: "triangleDown",
        text: `${s.confidence_floor}% floor`,
        size: 8
      });
      markers.push({
        time: d.times[Math.floor(d.times.length / 2)],
        position: "aboveBar",
        color: color,
        shape: "triangleUp",
        text: `${s.size_multiplier}x size`,
        size: 8
      });
    }
  }
  
  setMarkersCommon(markers);
}
'''

    # Find drawMarkers function and add renderSignalMarkers after it
    # drawMarkers is around line 1547
    markers_insert_idx = None
    for i, line in enumerate(new_lines):
        if 'function drawMarkers' in line and i > 1500:
            markers_insert_idx = i  # Insert after this function
            break
    
    if markers_insert_idx:
        # Insert after drawMarkers (find the end of drawMarkers)
        # drawMarkers ends with setMarkersCommon(markers);
        end_markers = None
        for j in range(markers_insert_idx, min(markers_insert_idx + 30, len(new_lines))):
            if 'setMarkersCommon(markers);' in new_lines[j]:
                end_markers = j + 1  # Insert after this line
                break
        
        if end_markers:
            # Insert the render function
            new_lines_new = new_lines[:end_markers] + [
                '',
                'function renderSignalMarkers() {',
                '  const d = state.data, signals = state.signals;',
                '  const markers = [];',
                '',
                '  if (!signals || !d || !candleSeries) return;',
                '',
                '  for (const s of signals) {',
                '    if (s.type === "parameter") {',
                '      const color = s.strength > 0.5 ? C.up : C.down;',
                '      markers.push({',
                '        time: d.times[Math.floor(d.times.length / 2)],',
                '        position: "belowBar",',
                '        color: color,',
                '        shape: "triangleDown",',
                '        text: `${s.confidence_floor}% floor`',
                '      });',
                '      markers.push({',
                '        time: d.times[Math.floor(d.times.length / 2)],',
                '        position: "aboveBar",',
                '        color: color,',
                '        shape: "triangleUp",',
                '        text: `${s.size_multiplier}x size`',
                '      });',
                '    }',
                '  }',
                '',
                '  setMarkersCommon(markers);',
                '}',
                '',
            ], new_lines
        
        print(f"Inserted signal rendering code at line {end_markers}")
    else:
        print("Could not find drawMarkers function")
    
    with open(r'E:\Project\Mt5 Ea\static\app.js', 'w', errors='ignore') as f:
        f.writelines(new_lines)
    
    print(f"Modified app.js: inserted at line {insert_idx}")
else:
    print("Could not find insertion point in loadChart")
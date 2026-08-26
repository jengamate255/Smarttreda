#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\static\app.js', 'r', errors='ignore') as f:
    content = f.read()

# The render function code to add after drawMarkers
render_func = '''

function renderSignalMarkers() {
  const d = state.data, signals = state.signals;
  const markers = [];
  
  if (!signals || !d || !candleSeries) return;
  
  for (const s of signals) {
    if (s.type === "parameter") {
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

# Find the position after drawMarkers function ends
# drawMarkers ends with "setMarkersCommon(markers);"
# Find that line and add after it
idx = content.find('setMarkersCommon(markers);')
if idx >= 0:
    # Find the end of that line
    line_end = content.find('\n', idx)
    if line_end >= 0:
        # Insert after the newline
        new_content = content[:line_end+1] + render_func + content[line_end+1:]
        with open(r'E:\Project\Mt5 Ea\static\app.js', 'w', errors='ignore') as f:
            f.write(new_content)
        print("renderSignalMarkers function added")
    else:
        print("Could not find line end")
else:
    print("Could not find setMarkersCommon(markers);")
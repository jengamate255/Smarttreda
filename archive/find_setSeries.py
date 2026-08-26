#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\static\app.js', 'r', errors='ignore') as f:
    content = f.read()

# Find setSeriesData function
idx = content.find('function setSeriesData')
if idx >= 0:
    end = min(len(content), idx + 600)
    print(content[idx:end])
else:
    print("setSeriesData not found")
    # Try alternative
    idx2 = content.find('setSeriesData')
    if idx2 >= 0:
        print(f"Found at position {idx2}")
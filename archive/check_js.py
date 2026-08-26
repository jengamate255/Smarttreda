#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\static\app.js', 'r', errors='ignore') as f:
    lines = f.readlines()

# Check the updatePriceDisplay function area more carefully
for i in range(1755, min(1795, len(lines))):
    line = lines[i].strip()
    if line:
        print(f'{i+1}: {line[:80]}')
#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\static\app.js', 'r', errors='ignore') as f:
    lines = f.readlines()

# Find drawMarkers function
for i, line in enumerate(lines):
    if 'function drawMarkers' in line:
        print(f'Found at line {i+1}')
        # Print next 60 lines
        for j in range(i, min(i+70, len(lines))):
            print(f'{j+1}: {lines[j][:150]}', end='')
        print("---")
        break
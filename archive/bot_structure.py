#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'r', errors='ignore') as f:
    lines = f.readlines()

# Find _compute_bot_analysis return structure
for i, line in enumerate(lines):
    if '_compute_bot_analysis' in line:
        print(f'Line {i+1}: {line[:100]}')
        # Print next 40 lines
        for j in range(i+1, min(i+45, len(lines))):
            print(f'{j+1}: {lines[j][:120]}', end='')
        print("---")
        break
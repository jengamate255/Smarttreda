#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'r', errors='ignore') as f:
    lines = f.readlines()

# Find bot_analysis function and insert after it
for i, line in enumerate(lines):
    if 'def bot_analysis' in line:
        # i is 0-indexed, function starts at this line
        # The function returns at around line 504 (1-indexed)
        # Let's find where the function actually ends (next def at same level)
        for j in range(i, min(i+50, len(lines))):
            if lines[j].strip().startswith('def ') and j > i+5:
                # Found next function - insert before this
                insert_at = j  # 0-indexed
                print(f'Next function at line {j+1} (0-indexed: {j})')
                print(f'Insert before line {j+1}')
                break
        else:
            insert_at = len(lines)
            print(f'Insert at end of file (line {len(lines)+1})')
        break
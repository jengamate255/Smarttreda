#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\web_dashboard.py', 'r', errors='ignore') as f:
    content = f.read()

# Find the return structure of bot_analysis
idx = content.find('def bot_analysis')
# Find the next return statement
idx2 = content.find('\n    return ', idx)
if idx2 >= 0:
    print(content[idx2:idx2+600])
else:
    print("Return not found")
#!/usr/bin/env python
with open(r'E:\Project\Mt5 Ea\static\app.js', 'r', errors='ignore') as f:
    content = f.read()

# Find rates-related code
idx = content.find('/api/rates')
if idx >= 0:
    print(f"Found /api/rates at position {idx}")
    start = max(0, idx - 300)
    end = min(len(content), idx + 700)
    print(content[start:end])
else:
    print("/api/rates not found directly")
    # Try searching for 'rates' in general
    import re
    matches = [(m.start(), content[max(0,m.start()-20):m.end()+20]) for m in re.finditer('"rates"', content)]
    for pos, ctx in matches[:5]:
        print(f'Found at {pos}: ...{ctx}...')
import re

path = r"E:\Project\Mt5 Ea\static\app.js"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

lines = content.split("\n")

# Find the problematic top-level await (outside any async function)
# Heuristic: a line with 'await ' at the start (after stripping) that is NOT
# inside a function body. We track brace depth and async context.

new_lines = []
i = 0
removed = []
while i < len(lines):
    line = lines[i]
    stripped = line.strip()
    # Detect function/async boundaries
    if re.search(r"async\s+function", line) or re.search(r"function\s+\w+\s*\(", line):
        # entering a function; track depth
        depth = 0
        # find opening brace
        for j in range(i, len(lines)):
            depth += lines[j].count("{") - lines[j].count("}")
            if depth <= 0 and j > i:
                break
        # keep the whole function block as-is
        new_lines.extend(lines[i:j+1])
        i = j + 1
        continue
    # top-level await check
    if stripped.startswith("await ") and not stripped.startswith("async "):
        # This is a top-level await outside any function
        removed.append((i + 1, line.rstrip()))
        i += 1
        continue
    new_lines.append(line)
    i += 1

if removed:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines))
    print("Removed top-level await lines:")
    for ln, txt in removed:
        print(f"  line {ln}: {txt[:80]}")
else:
    print("No top-level await found.")

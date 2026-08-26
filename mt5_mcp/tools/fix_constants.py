#!/usr/bin/env python3
import sys

filepath = r"E:\Project\Mt5 Ea\mt5_mcp\tools\boom_crash_strategy.py"

with open(filepath, 'r') as f:
    content = f.read()

# Find and replace the constants section
old_section = """# Chandelier trailing exit (research 2026-08-13, Structural Idea B) ---
# Arm the trail once unrealized profit reaches +1R; trail the stop at
# ``TRAIL_ATR_DISTANCE`` ATR behind the extreme since entry; no fixed TP.
# Configurable via settings.json -> "boom_crash" (see mt5_mcp.config).
TRAIL_ARM_R = 1.0
TRAIL_ATR_DISTANCE = 1.0
TRAIL_ATR_DISTANCE_MIN = 1.0
TRAIL_ATR_DISTANCE_MAX = 1.5"""

new_section = """# Chandelier trailing exit (research 2026-08-13, Structural Idea B) ---
# Arm the trail once unrealized profit reaches +1R; trail the stop at
# ``TRAIL_ATR_DISTANCE`` ATR behind the extreme since entry; no fixed TP.
# Configurable via settings.json -> "boom_crash" (see mt5_mcp.config).
DEFAULT_TRAIL_ARM_R = 1.0
DEFAULT_TRAIL_DISTANCE_ATR = 1.0
TRAIL_ARM_R = DEFAULT_TRAIL_ARM_R
TRAIL_ATR_DISTANCE = DEFAULT_TRAIL_DISTANCE_ATR
TRAIL_ATR_DISTANCE_MIN = 1.0
TRAIL_ATR_DISTANCE_MAX = 1.5"""

if old_section in content:
    content = content.replace(old_section, new_section)
    with open(filepath, 'w') as f:
        f.write(content)
    print("Replacement done successfully")
else:
    print("ERROR: Old section not found!")
    # Show what's around that area
    idx = content.find("# Chandelier trailing exit")
    if idx >= 0:
        print("Found at index", idx)
        print("Context:")
        print(repr(content[idx:idx+350]))
    else:
        print("Could not find 'Chandelier trailing exit' in file")
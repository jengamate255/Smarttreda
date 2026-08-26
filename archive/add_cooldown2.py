# Add daily trade limit cooldown function to trade_watch.py

with open(r'E:\Project\Mt5 Ea\trade_watch.py', 'r') as f:
    lines = f.readlines()

# Find a good place to insert - after the _has_exposure function ends
# _has_exposure is at line 82, ends around line 86
# We'll insert after line 86 (index 85 in 0-indexed)

# The cooldown code lines to insert
cooldown_lines = [
    '\n',
    '# Daily trade limit cooldown tracking\n',
    '_daily_trade_count = {}\n',
    '_last_daily_trade_time = {}\n',
    '\n',
    'def _check_daily_trade_cooldown(symbol: str) -> bool:\n',
    '    \"\"\"Check if a symbol\'s daily trade limit cooldown has expired.\"\"\"\n',
    '    from datetime import datetime, timezone, timedelta\n',
    '    now = datetime.now(timezone.utc)\n',
    '    last_time = _last_daily_trade_time.get(symbol)\n',
    '    if last_time is None or (now - last_time).total_seconds() > 86400:\n',
    '        _last_daily_trade_time[symbol] = now\n',
    '        _daily_trade_count[symbol] = 0\n',
    '    if _daily_trade_count.get(symbol, 0) >= 3:\n',
    '        if last_time is not None:\n',
    '            elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()\n',
    '            if elapsed < 60 * 60:  # 60 minute cooldown\n',
    '                return True  # Cooldown active, skip trade\n',
    '    _last_daily_trade_time[symbol] = datetime.now(timezone.utc)\n',
    '    _daily_trade_count[symbol] = _daily_trade_count.get(symbol, 0) + 1\n',
    '    return False\n',
    '\n',
]

# Insert after line 86 (index 85), which is after _has_exposure function
# Line 86 in the file is blank after _has_exposure
insert_pos = 86  # 1-indexed, so we use 86 to insert after line 86

# Actually, let's find the exact position by looking for _has_exposure
found = False
for i, line in enumerate(lines):
    if 'def _has_exposure' in line:
        # Insert after the blank lines following _has_exposure
        # _has_exposure is at line i, and we want to insert after line i+5 (after the return)
        # Let's find the next blank line or function
        insert_pos = i + 6  # After _has_exposure function ends
        found = True
        break

if not found:
    insert_pos = len(lines)  # Append at end

# Insert the cooldown code
new_lines = lines[:insert_pos] + cooldown_lines + lines[insert_pos:]

with open(r'E:\Project\Mt5 Ea\trade_watch.py', 'w') as f:
    f.writelines(new_lines)

print(f'Cooldown code inserted at position {insert_pos}')
print(f'Original lines: {len(lines)}, New lines: {len(new_lines)}')
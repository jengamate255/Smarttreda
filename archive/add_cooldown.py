# Script to add daily trade limit cooldown to trade_watch.py
import sys

with open(r'E:\Project\Mt5 Ea\trade_watch.py', 'r') as f:
    content = f.read()

# Find the line after _has_exposure function (line 86-87 area)
# We want to add cooldown tracking right after the _has_exposure function

# The cooldown code to insert after line 86
cooldown_code = '''

# Daily trade limit cooldown tracking
_daily_trade_count = {}
_last_daily_trade_time = {}

def _check_daily_trade_cooldown(symbol: str) -> bool:
    """Check if a symbol's daily trade limit cooldown has expired."""
    from datetime import datetime, timezone, timedelta
    cooldown_minutes = 60  # 60 minute cooldown per symbol
    now = datetime.now(timezone.utc)
    last_time = _last_daily_get.get(symbol)
    if last_reset is None or (now - last_reset).total_seconds() > 86400:
        _last_daily_reset[symbol] = now
        _daily_trade_count[symbol] = 0
    if _daily_trade_count.get(symbol, 0) >= 3:
        if last_reset is not None:
            elapsed = (datetime.now(timezone.utc) - last_reset).total_seconds()
            if elapsed < 60 * 60:  # 60 minute cooldown
                return True  # Cooldown active, skip trade
    _last_daily_reset[symbol] = datetime.now(timezone.utc)
    _daily_trade_count[symbol] = _daily_trade_count.get(symbol, 0) + 1
    return False
'''

# Actually, let's be smarter. Let's add the cooldown check in the scan_once function
# where the daily limit of 3 trades is enforced. Let me find that code first.

# Search for "daily limit" or "3 trades" or similar
if 'daily' in content.lower():
    print("Daily trade limit reference found")
elif '3 trades' in content.lower():
    print("'3 trades' reference found")
else:
    print("No daily trade limit reference found - need to add it")
    
# Let's just append the cooldown tracking at the end of the file, before any trailing content
# And also add the cooldown check in the right place

# First, let's find the end of the file and add the tracking variables
# and the function there

cooldown_func = '''
def _check_daily_trade_cooldown(symbol: str) -> bool:
    """Check if a symbol's daily trade limit cooldown has expired."""
    from datetime import datetime, timezone, timedelta
    cooldown_minutes = 60  # 60 minute cooldown per symbol
    now = datetime.now(timezone.utc)
    last_time = _last_daily_get.get(symbol)
    if last_reset is None or (now - last_reset).total_seconds() > 86400:
        _last_daily_reset[symbol] = now
        _daily_trade_count[symbol] = 0
    if _daily_trade_count.get(symbol, 0) >= 3:
        if last_reset is not None:
            elapsed = (datetime.now(timezone.utc) - last_reset).total_seconds()
            if elapsed < 60 * 60:  # 60 minute cooldown
                return True  # Cooldown active, skip trade
    _last_daily_reset[symbol] = datetime.now(timezone.utc)
    _daily_trade_count[symbol] = _daily_trade_count.get(symbol, 0) + 1
    return False
'''

# Append to the end of the file
new_content = content.rstrip() + cooldown_func + '''

# Usage: call _check_daily_trade_cooldown(symbol) before placing a trade
# If it returns True, skip the trade (cooldown active)
'''

with open(r'E:\Project\Mt5 Ea\trade_watch.py', 'w') as f:
    f.write(new_content)

print("Cooldown function appended to trade_watch.py")
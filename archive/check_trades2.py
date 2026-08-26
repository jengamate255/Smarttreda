#!/usr/bin/env python
import sys
sys.path.insert(0, r'E:\Project\Mt5 Ea\mt5_mcp')
from memory.trade_journal import get_journal
j = get_journal()
trades = j.list_trades()
print(f'Total trades in journal: {len(trades)}')
for t in trades[:20]:  # Show first 20
    ticket = t.get('ticket', 'N/A')
    symbol = t.get('symbol', 'N/A')
    pnl = t.get('pnl', 0)
    side = t.get('side', 'N/A')
    print(f'  Ticket: {ticket}, Symbol: {symbol}, PnL: {pnl}, Side: {side}')
#!/usr/bin/env python
import sys
sys.path.insert(0, r'E:\Project\Mt5 Ea')

# Check today's trades using the journal/history system
from mt5_mcp.trade_history import trade_history
trades = trade_history(days=1)
print(f'Trades today: {len(trades)}')
for t in trades:
    ticket = t.get('ticket', 'N/A')
    symbol = t.get('symbol', 'N/A')
    pnl = t.get('pnl', 0)
    side = t.get('side', 'N/A')
    print(f'  Ticket: {ticket}, Symbol: {symbol}, PnL: {pnl}, Side: {side}')
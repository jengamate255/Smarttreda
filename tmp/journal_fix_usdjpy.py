# -*- coding: utf-8 -*-
"""Backfill today's two USDJPY round trips into journal.db (audit cleanup 2026-09-25/26)."""
import sqlite3

DB = r"E:\Project\Mt5 Ea\mt5_mcp\memory\journal.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# 1) desk trade #1: ticket 397132112 — signal row
cur = con.execute(
    "UPDATE signals SET filled=1, close_price=?, pnl=?, outcome=?, close_time=?, updated_at=datetime('now') "
    "WHERE signal_id=?",
    (157.528, -27.93, "loss", "2026-09-25T11:52:21+00:00", "desk-USDJPY-1790331917"),
)
print("signals updated:", cur.rowcount)

# 2) desk trade #1: trades row (was the 'details unavailable' stub)
cur = con.execute(
    "UPDATE trades SET entry=?, exit_price=?, sl=?, tp=?, lots=?, open_time=?, close_time=?, "
    "pnl=?, outcome=?, session_hour=?, duration_seconds=?, updated_at=datetime('now') "
    "WHERE ticket=? AND setup='desk_managed'",
    (157.928, 157.528, 157.325, 158.821, 0.11,
     "2026-09-25T10:06:13+00:00", "2026-09-25T11:52:21+00:00",
     -27.93, "loss", 10, 6368, 397132112),
)
print("trades desk row updated:", cur.rowcount)

# 3) mystery 0.15-lot trade: ticket 397151807 — no engine claim (magic 0, empty comment)
exists = con.execute("SELECT id FROM trades WHERE ticket=?", (397151807,)).fetchone()
if exists:
    print("trades 397151807 already present, id", exists["id"])
else:
    con.execute(
        "INSERT INTO trades (ticket, symbol, side, setup, entry, exit_price, sl, lots, open_time, "
        "close_time, pnl, outcome, reason, session_hour, duration_seconds, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
        (397151807, "USDJPY", "BUY", "manual", 157.752, 157.383, 157.427, 0.15,
         "2026-09-25T12:41:04+00:00", "2026-09-25T13:00:07+00:00",
         -35.17, "loss",
         "SL hit; magic 0 + empty comment = no stack engine claims it (manual/unknown origin)",
         12, 1143),
    )
    print("trades 397151807 inserted, id", con.execute("SELECT last_insert_rowid()").fetchone()[0])

con.commit()

# verify
for row in con.execute(
    "SELECT id, ticket, symbol, side, setup, entry, exit_price, lots, pnl, outcome FROM trades "
    "WHERE ticket IN (397132112, 397151807)"
):
    print(dict(row))
for row in con.execute(
    "SELECT signal_id, ticket, filled, close_price, pnl, outcome FROM signals WHERE ticket=397132112"
):
    print(dict(row))
con.close()

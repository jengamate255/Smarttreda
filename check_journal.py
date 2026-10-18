"""Check trades table schema and data quality."""
import sqlite3
conn = sqlite3.connect("E:/Project/Mt5 Ea/mt5_mcp/memory/journal.db")
c = conn.cursor()

# Get schema
c.execute("PRAGMA table_info(trades)")
cols = [(row[1], row[2]) for row in c.fetchall()]
print("Trades columns:")
for name, dtype in cols:
    print(f"  {name}: {dtype}")

# Check how many have outcomes
c.execute("SELECT COUNT(*) FROM trades WHERE outcome IS NOT NULL")
with_outcome = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM trades WHERE outcome IS NULL")
without_outcome = c.fetchone()[0]
print(f"\nWith outcome: {with_outcome}, Without: {without_outcome}")

# Check what data we have for ML features
c.execute("SELECT symbol, side, pnl, outcome FROM trades WHERE outcome IS NOT NULL LIMIT 10")
print("\nSample trades with outcomes:")
for row in c.fetchall():
    print(f"  {row}")

conn.close()

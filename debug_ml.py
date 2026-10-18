"""Check training data distribution for rr and confirmation."""
import sys
sys.path.insert(0, "E:/Project/Mt5 Ea")
import sqlite3
import numpy as np

conn = sqlite3.connect("E:/Project/Mt5 Ea/mt5_mcp/memory/journal.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT rr, zone_strength, confidence, pnl, outcome FROM trades WHERE outcome IS NOT NULL AND rr IS NOT NULL"
).fetchall()
conn.close()

rr_win = [r["rr"] for r in rows if r["outcome"] == "win" and r["rr"] > 0]
rr_loss = [r["rr"] for r in rows if r["outcome"] == "loss" and r["rr"] > 0]

print(f"Winning trades: {len(rr_win)}, avg rr: {np.mean(rr_win):.2f}")
print(f"Losing trades:  {len(rr_loss)}, avg rr: {np.mean(rr_loss):.2f}")
print()

# Win rate by rr bucket
print("Win rate by R:R bucket:")
all_rr = [(r["rr"], r["outcome"]) for r in rows if r["rr"] > 0]
for bucket in [(0, 2), (2, 3), (3, 5), (5, 10), (10, 100)]:
    subset = [o for rr, o in all_rr if bucket[0] <= rr < bucket[1]]
    if subset:
        wr = sum(1 for x in subset if x == "win") / len(subset)
        print(f"  R:R {bucket[0]}-{bucket[1]}: {len(subset)} trades, {wr:.1%} win rate")

# Win rate by zone_strength bucket
print("\nWin rate by zone_strength bucket:")
all_zs = [(r["zone_strength"], r["outcome"]) for r in rows if r["zone_strength"] > 0]
for bucket in [(50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]:
    subset = [o for zs, o in all_zs if bucket[0] <= zs < bucket[1]]
    if subset:
        wr = sum(1 for x in subset if x == "win") / len(subset)
        print(f"  zone {bucket[0]}-{bucket[1]}: {len(subset)} trades, {wr:.1%} win rate")

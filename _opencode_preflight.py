import datetime
import os
import shutil
import sqlite3
import subprocess
import sys

DB = r"C:\Users\Dave\.local\share\opencode\opencode.db"
IDS = [
    "ses_ff5915b6effe0WTTnWxFR0PlL5",
    "ses_fa8e122e8ffeN6iZD4MTVYbWpm",
    "ses_f874b1379ffeiymow66v7aq1Gw",
    "ses_f91a39867ffellnOvFdZlGZo8S",
    "ses_f8779ec70ffeDdezacVd18HQ8a",
    "ses_fc28220aeffeLDHWGkrgSZOpBh",
]
PROTECTED = [
    "ses_f37f3bcdeffex1B56LU4JZJNFS",
    "ses_f368d8152ffejM1nyVe5i21OxP",
]

problems = []


def when(ms):
    if not ms:
        return "none"
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


today = datetime.date.today()
c = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=60)

print("=== delete set ===")
for sid in IDS:
    row = c.execute("SELECT title, time_updated FROM session WHERE id=?", (sid,)).fetchone()
    if row is None:
        problems.append("missing session " + sid)
        print("  MISSING", sid)
        continue
    upd = when(row[1])
    is_today = row[1] and datetime.datetime.fromtimestamp(row[1] / 1000).date() == today
    if is_today:
        problems.append("delete target updated today: " + sid)
    print("  %-40s updated=%s today=%s  %s" % (sid, upd, is_today, row[0][:40]))

print("=== protected ===")
for sid in PROTECTED:
    row = c.execute("SELECT title, time_updated FROM session WHERE id=?", (sid,)).fetchone()
    if row is None:
        problems.append("protected session missing " + sid)
    else:
        print("  %-40s updated=%s  %s" % (sid, when(row[1]), row[0][:40]))
    if sid in IDS:
        problems.append("protected id in delete set " + sid)

ph = ",".join("?" * len(IDS))
for tbl, col in (("event", "aggregate_id"), ("event_sequence", "aggregate_id"),
                 ("part", "session_id"), ("message", "session_id"),
                 ("todo", "session_id"), ("session", "id")):
    n = c.execute("SELECT count(*) FROM %s WHERE %s IN (%s)" % (tbl, col, ph), IDS).fetchone()[0]
    print("rows to delete %-16s %d" % (tbl, n))

page_size = c.execute("PRAGMA page_size").fetchone()[0]
page_count = c.execute("PRAGMA page_count").fetchone()[0]
db_size = os.path.getsize(DB)
total_sessions = c.execute("SELECT count(*) FROM session").fetchone()[0]
c.close()

free_c = shutil.disk_usage("C:\\").free
free_e = shutil.disk_usage("E:\\").free
est_new = db_size * 0.42

print("\ndb=%d bytes (%.2f GB), page_size=%d pages=%d, sessions=%d" % (db_size, db_size / 1e9, page_size, page_count, total_sessions))
print("free C=%.2f GB   free E=%.2f GB" % (free_c / 1e9, free_e / 1e9))
print("estimated post-cleanup db ~ %.2f GB  (VACUUM INTO target on E: needs ~%.2f GB)"
      % (est_new / 1e9, est_new * 1.2 / 1e9))

if est_new * 1.2 > free_e:
    problems.append("E: free space insufficient for VACUUM INTO")
if est_new > free_c:
    problems.append("C: free space insufficient for final install")

out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq OpenCode.exe"],
                     capture_output=True, text=True, errors="ignore")
n_proc = (out.stdout or "").count("OpenCode.exe")
print("OpenCode.exe processes: %d (all will be killed)" % n_proc)
if n_proc == 0:
    print("  note: nothing to kill, script proceeds directly")

print("\n=== RESULT ===")
if problems:
    for p in problems:
        print("PROBLEM:", p)
    sys.exit(1)
print("PREFLIGHT OK - safe to schedule cleanup")
sys.exit(0)

import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback

DB_DIR = r"C:\Users\Dave\.local\share\opencode"
DB = os.path.join(DB_DIR, "opencode.db")
WAL = DB + "-wal"
SHM = DB + "-shm"
WORK = r"E:\Project\Mt5 Ea"
BACKUP = os.path.join(WORK, "_opencode_backup")
NEWDB_C = os.path.join(DB_DIR, "opencode_vacuum.db")
NEWDB_E = os.path.join(WORK, "_opencode_vacuum.db")
REPORT = os.path.join(WORK, "_opencode_cleanup_report.txt")

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
GRACE_SECONDS = 45
TASK_NAME = "MT5 OpenCodeCleanup"

lines = []


def log(msg):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = "[%s] %s" % (stamp, msg)
    lines.append(text)
    print(text, flush=True)


def gb(n):
    return "%.2f GB" % (n / 1e9)


def mb(n):
    return "%.1f MB" % (n / 1e6)


def write_report(status, detail=""):
    body = "\n".join(lines)
    header = "opencode.db cleanup report\nstatus: %s\n%s\n%s\n" % (
        status,
        ("detail: " + detail) if detail else "",
        "=" * 70,
    )
    try:
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write(header + body + "\n")
    except Exception:
        pass


def opencode_running():
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq OpenCode.exe"],
        capture_output=True, text=True, errors="ignore",
    )
    return "OpenCode.exe" in (out.stdout or "")


def kill_opencode():
    log("killing OpenCode.exe ...")
    subprocess.run(["taskkill", "/F", "/IM", "OpenCode.exe"], capture_output=True)
    deadline = time.time() + 600
    while time.time() < deadline:
        if not opencode_running():
            log("OpenCode.exe stopped")
            return True
        time.sleep(3)
    return False


def lock_free():
    try:
        c = sqlite3.connect(DB, timeout=3)
        c.execute("BEGIN IMMEDIATE")
        c.execute("ROLLBACK")
        c.close()
        return True
    except Exception:
        return False


def export_sessions(cur):
    os.makedirs(BACKUP, exist_ok=True)
    log("exporting text transcripts to %s" % BACKUP)
    for sid in IDS:
        cur.execute("SELECT * FROM session WHERE id=?", (sid,))
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("session missing: " + sid)
        sess = {}
        for i, name in enumerate(cols):
            v = row[i]
            sess[name] = "<bytes>" if isinstance(v, bytes) else v

        cur.execute(
            "SELECT id, time_created, data FROM message WHERE session_id=? ORDER BY time_created",
            (sid,),
        )
        msgs = cur.fetchall()
        cur.execute(
            "SELECT id, message_id, time_created, data FROM part WHERE session_id=? ORDER BY time_created",
            (sid,),
        )
        parts = cur.fetchall()

        text_parts, skipped = [], 0
        for _pid, mid, tc, data in parts:
            try:
                obj = json.loads(data)
            except Exception:
                obj = {"type": "unknown", "text": str(data)[:500]}
            if obj.get("type") == "text":
                t = obj.get("text", "") or ""
                if len(t) > 4000:
                    t = t[:4000] + "...[truncated]"
                text_parts.append({"message_id": mid, "time": tc, "text": t})
            else:
                skipped += 1

        payload = {
            "session": sess,
            "message_count": len(msgs),
            "messages": [
                {"id": m[0], "time": m[1], "data": str(m[2])[:4000]}
                for m in msgs
            ],
            "text_parts": text_parts,
            "skipped_non_text_parts": skipped,
        }
        path = os.path.join(BACKUP, sid + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        log(
            "  %s  msgs=%d text=%d skipped=%d file=%s"
            % (sid, len(msgs), len(text_parts), skipped, mb(os.path.getsize(path)))
        )


def payload_bytes(cur):
    total = 0
    for tbl, col in (("event", "data"), ("message", "data"), ("part", "data")):
        r = cur.execute("SELECT sum(length(%s)) FROM %s" % (col, tbl)).fetchone()
        total += r[0] or 0
    return total


def delete_rows(cur):
    ph = ",".join("?" * len(IDS))
    total = 0
    stmts = [
        ("event", "DELETE FROM event WHERE aggregate_id IN (%s)" % ph),
        ("event_sequence", "DELETE FROM event_sequence WHERE aggregate_id IN (%s)" % ph),
        ("part", "DELETE FROM part WHERE session_id IN (%s)" % ph),
        ("message", "DELETE FROM message WHERE session_id IN (%s)" % ph),
        ("todo", "DELETE FROM todo WHERE session_id IN (%s)" % ph),
        ("session", "DELETE FROM session WHERE id IN (%s)" % ph),
    ]
    for name, sql in stmts:
        cur.execute(sql, IDS)
        n = cur.rowcount
        total += n
        log("  delete %-16s %d rows" % (name, n))
    return total


def free_space(path):
    return shutil.disk_usage(path).free


def main():
    log("cleanup started; pid=%d" % os.getpid())
    if set(IDS) & set(PROTECTED):
        raise RuntimeError("refusing: protected session in delete list")
    if not os.path.exists(DB):
        raise RuntimeError("db not found: " + DB)

    before_db = os.path.getsize(DB)
    before_free_c = free_space("C:\\")
    before_free_e = free_space("E:\\")
    log("before: db=%s  free C=%s  free E=%s" % (gb(before_db), gb(before_free_c), gb(before_free_e)))

    log("grace period %ds so the agent can finish its message ..." % GRACE_SECONDS)
    time.sleep(GRACE_SECONDS)

    if opencode_running():
        if not kill_opencode():
            write_report("FAILED", "OpenCode.exe would not stop")
            return 1
    else:
        log("OpenCode.exe already stopped")

    ok = False
    for i in range(30):
        if lock_free():
            ok = True
            break
        log("db write-lock busy, retry %d/30" % (i + 1))
        time.sleep(10)
    if not ok:
        write_report("FAILED", "database still locked by another process")
        return 2
    log("write lock acquired")

    conn = sqlite3.connect(DB, timeout=30)
    cur = conn.cursor()
    cur.execute("PRAGMA temp_store=MEMORY")

    page_size = cur.execute("PRAGMA page_size").fetchone()[0]
    page_count = cur.execute("PRAGMA page_count").fetchone()[0]
    log("page_size=%d page_count=%d logical=%s" % (page_size, page_count, mb(page_size * page_count)))

    export_sessions(cur)

    bytes_before = payload_bytes(cur)
    log("payload before: %s" % gb(bytes_before))

    log("deleting rows ...")
    t0 = time.time()
    deleted = delete_rows(cur)
    conn.commit()
    log("deleted %d rows in %.1fs" % (deleted, time.time() - t0))

    bytes_after = payload_bytes(cur)
    freed = bytes_before - bytes_after
    ratio = float(page_size * page_count) / max(bytes_before, 1)
    est_new = max(1e8, (page_size * page_count) - freed * ratio)
    log("payload freed: %s  estimated new db: %s" % (gb(freed), gb(est_new)))

    free_c = free_space("C:\\")
    free_e = free_space("E:\\")
    target, same_volume = None, False
    if est_new * 1.25 <= free_c:
        target, same_volume = NEWDB_C, True
    elif est_new * 1.25 <= free_e:
        target, same_volume = NEWDB_E, False
    else:
        conn.close()
        write_report("FAILED", "not enough free space for VACUUM INTO")
        return 3
    log("vacuum target=%s same_volume_install=%s (free C=%s free E=%s est=%s)"
        % (target, same_volume, gb(free_c), gb(free_e), gb(est_new)))

    if os.path.exists(target):
        os.remove(target)
    log("VACUUM INTO %s ..." % target)
    t0 = time.time()
    cur.execute("VACUUM INTO '%s'" % target.replace("'", "''"))
    conn.close()
    new_size = os.path.getsize(target)
    log("vacuum done in %.1fs, new db=%s" % (time.time() - t0, gb(new_size)))

    log("integrity check on new db ...")
    chk = sqlite3.connect(target)
    res = chk.execute("PRAGMA integrity_check").fetchone()[0]
    tables = chk.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    left = chk.execute("SELECT count(*) FROM session").fetchone()[0]
    chk.close()
    log("integrity_check=%s tables=%d sessions_remaining=%d" % (res, tables, left))
    if str(res).lower() != "ok":
        write_report("FAILED", "integrity check failed: %s" % res)
        return 4
    if left < 300:
        write_report("FAILED", "session count implausible: %d" % left)
        return 5

    log("swapping: removing old db on C: ...")
    for p in (DB, WAL, SHM):
        if os.path.exists(p):
            os.remove(p)
            log("  removed %s" % p)

    if same_volume:
        log("installing new db (same-volume rename) ...")
        os.replace(target, DB)
    else:
        log("installing new db (cross-volume copy) ...")
        shutil.copy2(target, DB)
    vconn = sqlite3.connect(DB)
    vres = vconn.execute("PRAGMA integrity_check").fetchone()[0]
    vleft = vconn.execute("SELECT count(*) FROM session").fetchone()[0]
    vconn.close()
    log("installed db integrity=%s sessions=%d size=%s" % (vres, vleft, gb(os.path.getsize(DB))))
    if str(vres).lower() != "ok":
        write_report("FAILED", "installed db failed integrity check - source kept at %s" % target)
        return 6

    if os.path.exists(target):
        os.remove(target)
        log("removed temporary vacuum file")

    after_db = os.path.getsize(DB)
    after_free_c = free_space("C:\\")
    after_free_e = free_space("E:\\")
    log("after: db=%s  free C=%s  free E=%s" % (gb(after_db), gb(after_free_c), gb(after_free_e)))
    log("db %s -> %s   C: free %s -> %s" % (gb(before_db), gb(after_db), gb(before_free_c), gb(after_free_c)))

    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True)
    log("scheduled task removed")

    write_report("SUCCESS")
    print("CLEANUP SUCCESS", flush=True)
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except Exception:
        log("EXCEPTION:\n" + traceback.format_exc())
        write_report("FAILED", "unhandled exception")
        print("CLEANUP FAILED", flush=True)
    sys.exit(code)

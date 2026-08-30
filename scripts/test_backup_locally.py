"""
Frame Atlas — local test for the monthly database-backup job (backend/backup.py).

Day 33 (Phase 3) moved run_db_backup() + the scheduler + the folder helper out
of app.py into backup.py. There was NO automated coverage for this before — the
V27 job has only ever been verified by watching the Railway logs once a month.
This exercises it end to end against a fake Drive client:

  · a backup serializes the live DB, gzips it, uploads it to a `_Backups`
    folder, and writes a db_backups row
  · _backup_due() flips correctly across a month boundary
  · backups beyond KEEP_BACKUP_COUNT are pruned from Drive AND the table
  · no admin Google connection → clean skip (returns False, no row, no crash)

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_backup_locally.py
"""

import gzip
import importlib.util
import io
import os
import sys
import sqlite3
import tempfile
from datetime import datetime, timedelta

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, "backend"))

failures = []


def check(label, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


# ── Fake Google Drive (only what backup.py calls) ────────────────────────────
class FakeReq:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeFiles:
    def __init__(self, drive):
        self.d = drive

    def list(self, q=None, fields=None, **kw):
        # get_or_create_backups_folder: "'<root>' in parents and name = '_Backups' and ..."
        def run():
            hits = [{"id": fid} for fid, f in self.d.files_by_id.items()
                    if f["is_folder"] and f["name"] == "_Backups"]
            return {"files": hits}
        return FakeReq(run)

    def create(self, body=None, media_body=None, fields=None, **kw):
        def run():
            self.d.counter += 1
            fid = f"fake-file-{self.d.counter}"
            is_folder = body.get("mimeType") == "application/vnd.google-apps.folder"
            raw = None
            if media_body is not None:
                raw = media_body.getbytes(0, media_body.size())
            self.d.files_by_id[fid] = {"name": body["name"], "is_folder": is_folder, "bytes": raw}
            self.d.creates.append({"name": body["name"], "is_folder": is_folder, "bytes": raw})
            return {"id": fid}
        return FakeReq(run)

    def delete(self, fileId=None, **kw):
        def run():
            self.d.deleted.append(fileId)
            self.d.files_by_id.pop(fileId, None)
            return {}
        return FakeReq(run)


class FakeDrive:
    def __init__(self):
        self.files_by_id = {}
        self.creates = []
        self.deleted = []
        self.counter = 0

    def files(self):
        return FakeFiles(self)


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_backup_test_")
    db_path = os.path.join(workdir, "library.db")
    os.environ["FA_DB_PATH"] = db_path
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    spec = importlib.util.spec_from_file_location("test_app_backup", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("App imported OK.\n")

    backup = mod.backup

    # app.py boot starts backup.start_backup_scheduler() at module scope, so a
    # daemon thread is already running _backup_scheduler_loop. Neuter its
    # trigger so it can't fire run_db_backup() concurrently with our explicit
    # calls below and double every count. We test the real _backup_due()
    # directly in section 4 via the saved reference.
    real_backup_due = backup._backup_due
    backup._backup_due = lambda: False
    import time as _time
    _time.sleep(0.5)  # let any in-flight daemon iteration (real -> None) settle

    # ── 0. Split wiring ──────────────────────────────────────────────────────
    print("0. Module split:")
    check("backup.py is its own module", backup.__name__ == "backup")
    check("app.py did NOT keep run_db_backup", not hasattr(mod, "run_db_backup"))
    check("app.py did NOT keep start_backup_scheduler", not hasattr(mod, "start_backup_scheduler"))
    check("app.py did NOT keep KEEP_BACKUP_COUNT", not hasattr(mod, "KEEP_BACKUP_COUNT"))
    check("backup imports the same drive module object", backup.drive is mod.drive)

    fake = FakeDrive()
    mod.drive.get_user_drive_service = lambda uid: fake
    mod.drive.get_root_folder_id = lambda uid: "ROOT_FOLDER"
    fake.creates.clear()
    fake.deleted.clear()
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM db_backups")
    conn.commit()
    conn.close()

    # ── 1. A backup uploads a gzipped SQLite snapshot + writes a row ─────────
    print("\n1. A single backup run:")
    ok = backup.run_db_backup()
    check("run_db_backup() returned True", ok is True)

    folder_creates = [c for c in fake.creates if c["is_folder"]]
    file_creates = [c for c in fake.creates if not c["is_folder"]]
    check("the _Backups folder was created once", len(folder_creates) == 1 and folder_creates[0]["name"] == "_Backups")
    check("exactly one backup file was uploaded", len(file_creates) == 1)
    check("the filename is library-backup-<date>.db.gz",
          file_creates[0]["name"].startswith("library-backup-") and file_creates[0]["name"].endswith(".db.gz"))

    payload = file_creates[0]["bytes"]
    unzipped = b""
    try:
        unzipped = gzip.decompress(payload)
    except Exception as e:
        pass
    check("the upload body is valid gzip", len(unzipped) > 0)
    check("...and decompresses to a real SQLite database",
          unzipped[:16] == b"SQLite format 3\x00", unzipped[:16])

    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT drive_file_id, filename FROM db_backups").fetchall()
    conn.close()
    check("one db_backups row was written", len(rows) == 1 and rows[0][1] == file_creates[0]["name"])

    # ── 2. A second run reuses the same folder (no duplicate _Backups) ──────
    print("\n2. Second run reuses the folder:")
    backup.run_db_backup()
    check("still only one _Backups folder ever created",
          len([c for c in fake.creates if c["is_folder"]]) == 1)
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM db_backups").fetchone()[0]
    conn.close()
    check("two db_backups rows now (KEEP_BACKUP_COUNT is 2, nothing pruned yet)", n == 2)

    # ── 3. Pruning beyond KEEP_BACKUP_COUNT ────────────────────────────────
    print("\n3. Pruning old backups:")
    # Start from a known state: wipe the rows from sections 1-2, then seed
    # exactly 3 old backups (Jan/Feb/Mar) each with a matching fake Drive file.
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM db_backups")
    for month in ("01", "02", "03"):
        fid = f"old-drive-{month}"
        fake.files_by_id[fid] = {"name": f"old-{month}.db.gz", "is_folder": False, "bytes": b""}
        conn.execute("INSERT INTO db_backups (drive_file_id, filename, created_at) VALUES (?, ?, ?)",
                     (fid, f"old-{month}.db.gz", f"2026-{month}-01 00:00:00"))
    conn.commit()
    conn.close()

    fake.deleted.clear()
    backup.run_db_backup()  # adds today's backup -> 4 rows, keep 2, prune 2

    conn = sqlite3.connect(db_path)
    remaining = [r[0] for r in conn.execute("SELECT filename FROM db_backups ORDER BY created_at DESC").fetchall()]
    conn.close()
    check(f"exactly KEEP_BACKUP_COUNT ({backup.KEEP_BACKUP_COUNT}) rows remain after prune",
          len(remaining) == backup.KEEP_BACKUP_COUNT, remaining)
    check("the newest kept pair is [today's backup, the March backup]",
          remaining[0].startswith("library-backup-") and remaining[1] == "old-03.db.gz", remaining)
    check("the 2 oldest Drive files (Jan, Feb) were deleted",
          set(fake.deleted) == {"old-drive-01", "old-drive-02"}, fake.deleted)
    check("March survived (still inside KEEP_BACKUP_COUNT)", "old-drive-03" not in fake.deleted)
    check("the pruned rows are gone from the table",
          "old-01.db.gz" not in remaining and "old-02.db.gz" not in remaining)

    # ── 4. _backup_due() across a month boundary ───────────────────────────
    print("\n4. _backup_due():")
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM db_backups")
    conn.commit()
    conn.close()
    check("due when there has never been a backup", real_backup_due() is True)

    now = datetime.now()
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO db_backups (drive_file_id, filename, created_at) VALUES ('x', 'x', ?)",
                 (now.strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit()
    conn.close()
    check("NOT due when a backup already ran this calendar month", real_backup_due() is False)

    prev_month = (now.replace(day=1) - timedelta(days=1))
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM db_backups")
    conn.execute("INSERT INTO db_backups (drive_file_id, filename, created_at) VALUES ('x', 'x', ?)",
                 (prev_month.strftime("%Y-%m-%d %H:%M:%S"),))
    conn.commit()
    conn.close()
    check("due again once the calendar month rolls over", real_backup_due() is True)

    # ── 5. No admin Google connection → clean skip ────────────────────────
    print("\n5. Admin hasn't connected Google:")
    mod.drive.get_user_drive_service = lambda uid: None
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM db_backups")
    conn.commit()
    conn.close()
    ok = backup.run_db_backup()
    check("run_db_backup() returned False", ok is False)
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM db_backups").fetchone()[0]
    conn.close()
    check("no db_backups row was written on the skip", n == 0)

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED ❌")
        for f in failures:
            print(f"   - {f}")
        sys.exit(1)
    print("ALL BACKUP TESTS PASSED ✅")


if __name__ == "__main__":
    main()

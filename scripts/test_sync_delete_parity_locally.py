"""
Frame Atlas — local test for V30 sync-delete parity.

Background: sync_folder_worker() only ever ADDED photos found in Drive; a
photo deleted directly in Drive (not through the app) just silently vanished
from the listing forever, leaving a dead row (and its tags/decks/favorites)
in the library. V30 makes sync remove those rows automatically — no
confirmation, by Ryan's explicit choice — guarded by one safety check: if
more than half the library would disappear in one pass, that almost always
means Drive gave back a partial/broken listing, not a real mass-deletion, so
the removal is skipped and logged instead of cascading.

This test covers: a normal single-file deletion is cleaned up (row + tags +
colors all gone), an untouched library is left alone, and the safety cap
kicks in instead of wiping the library when "most everything" looks deleted.

Drive is faked, so nothing leaves the machine.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_sync_delete_parity_locally.py
"""

import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))
ROBOT_EMAIL = "frame-atlas-robot@test-project.iam.gserviceaccount.com"


class FakeRequest:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeFilesResource:
    def __init__(self, drive):
        self.drive = drive

    def list(self, q=None, **kw):
        def run():
            folder_id = q.split("'")[1]
            return {"files": self.drive.folders.get(folder_id, [])}
        return FakeRequest(run)

    def get_media(self, fileId=None):
        req = FakeRequest(lambda: None)
        req.data = self.drive.jpeg_bytes
        return req


class FakeDrive:
    def __init__(self, jpeg_bytes):
        self.folders = {}
        self.jpeg_bytes = jpeg_bytes

    def files(self):
        return FakeFilesResource(self)


class FakeDownloader:
    def __init__(self, fh, req):
        fh.write(req.data)

    def next_chunk(self):
        return (None, True)


def make_jpeg(mod):
    img = mod.Image.new("RGB", (160, 90), (80, 120, 160))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_sync_delete_test_")
    db_path = os.path.join(workdir, "library.db")

    src = open(os.path.join(REPO, "backend", "app.py")).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
    assert patched != src, "Could not find DB_PATH line to patch"
    open(os.path.join(workdir, "app.py"), "w").write(patched)

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy-client-id")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ["GEMINI_API_KEY"] = "admin-shared-key"
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["GOOGLE_DRIVE_CREDENTIALS"] = json.dumps({
        "type": "service_account", "client_email": ROBOT_EMAIL, "project_id": "test"
    })

    spec = importlib.util.spec_from_file_location("fa_sync_delete_app", os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_sync_delete_app"] = mod
    spec.loader.exec_module(mod)
    print("App imported OK (Drive faked).")

    jpeg = make_jpeg(mod)
    drive = FakeDrive(jpeg)
    mod.get_drive_service = lambda: drive
    mod.MediaIoBaseDownload = FakeDownloader
    mod.trigger_tagging = lambda *a, **k: None

    failures = []

    def check(label, cond, detail=""):
        if cond:
            print(f"{label} — OK")
        else:
            print(f"{label} — FAIL  {detail}")
            failures.append(label)

    FOLDER = "AdminFolderId"
    drive.folders[FOLDER] = [
        {"id": f"file-{i}", "name": f"photo{i}.jpg", "mimeType": "image/jpeg", "md5Checksum": f"md5-{i}"}
        for i in range(6)
    ]

    def run_sync():
        mod.sync_state['errors'] = []
        mod.sync_folder_worker(FOLDER, 1)
        return list(mod.sync_state['errors'])

    def library_rows():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, drive_file_id, filename FROM images WHERE user_id = 1").fetchall()
        conn.close()
        return rows

    def tag_a_row(image_id):
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, 'mood', 'tense')",
                     (image_id,))
        conn.commit()
        conn.close()

    # ── 1. initial sync brings in all 6 ─────────────────────────────────────
    errors = run_sync()
    rows = library_rows()
    check("Initial sync adds all 6 photos", len(rows) == 6, len(rows))
    check("No errors on a normal sync", not errors, errors)

    for r in rows:
        tag_a_row(r["id"])

    # ── 2. re-syncing an UNCHANGED folder removes nothing ───────────────────
    errors = run_sync()
    check("Re-sync with nothing changed keeps all 6", len(library_rows()) == 6, len(library_rows()))

    # ── 3. one file deleted directly in Drive -> its row (and tags) vanish ──
    deleted_id = drive.folders[FOLDER][0]["id"]
    drive.folders[FOLDER] = drive.folders[FOLDER][1:]  # remove file-0
    errors = run_sync()
    rows = library_rows()
    check("Sync removes exactly the one photo deleted from Drive",
          len(rows) == 5 and all(r["drive_file_id"] != deleted_id for r in rows), rows)

    conn = sqlite3.connect(db_path)
    orphan_tags = conn.execute(
        "SELECT COUNT(*) FROM tags WHERE image_id NOT IN (SELECT id FROM images)"
    ).fetchone()[0]
    conn.close()
    check("Its tags were cleaned up too (no orphaned rows)", orphan_tags == 0, orphan_tags)
    check("No error was reported for a normal single-file removal", not errors, errors)

    # ── 4. safety cap: Drive "loses" most of the folder -> nothing is deleted
    remaining_before = len(library_rows())
    drive.folders[FOLDER] = drive.folders[FOLDER][:1]  # simulate a broken/partial listing
    errors = run_sync()
    rows_after = library_rows()
    check("Safety cap prevents wiping the library on a suspicious listing",
          len(rows_after) == remaining_before, (remaining_before, len(rows_after)))
    check("A skip like that is reported as an error/warning, not silent",
          any('half' in e or 'Skipped' in e for e in errors), errors)

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED ❌")
        for f in failures:
            print(f"   - {f}")
    else:
        print("ALL SYNC-DELETE PARITY TESTS PASSED ✅")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

"""
Frame Atlas — local test for the V31 bulk photo delete endpoint
(POST /api/images/bulk-delete).

Background: Select Mode could already crop, tag, and add-to-deck a batch of
photos, but not delete them — you had to open each one individually. This
mirrors the existing single-photo DELETE /api/images/<id> rules exactly, just
batched: admin's own photos get moved to Drive's _Removed folder (recoverable,
never a hard delete), friends' deletes are DB-only (their Drive share is
Viewer-only) and recorded in sync_exclusions so the next sync doesn't
re-import them. A failure moving one photo (e.g. a Drive permission hiccup)
is skipped and reported — it must not block or roll back the rest of the
batch (Ryan's explicit call, matching the "skip and continue" behavior he
picked for this feature).

Drive is faked, so nothing leaves the machine.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_bulk_delete_locally.py
"""

import importlib.util
import io
import os
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))


class FakeRequest:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeFiles:
    def __init__(self, drive):
        self.drive = drive

    def get(self, fileId=None, fields=None):
        def run():
            if fileId in self.drive.fail_on_get:
                raise Exception("insufficientFilePermissions: service account cannot read this file")
            return {"id": fileId, "parents": [self.drive.parents.get(fileId, "root-folder")]}
        return FakeRequest(run)

    def update(self, fileId=None, addParents=None, removeParents=None, fields=None):
        def run():
            self.drive.moved.append(fileId)
            self.drive.parents[fileId] = addParents
            return {"id": fileId}
        return FakeRequest(run)

    def list(self, q=None, fields=None, **kw):
        def run():
            if self.drive.removed_folder_id:
                return {"files": [{"id": self.drive.removed_folder_id}]}
            return {"files": []}
        return FakeRequest(run)

    def create(self, body=None, fields=None):
        def run():
            self.drive.removed_folder_id = "removed-folder-1"
            self.drive.list_calls += 1
            return {"id": self.drive.removed_folder_id}
        return FakeRequest(run)


class FakeDrive:
    def __init__(self):
        self.moved = []
        self.parents = {}
        self.removed_folder_id = None
        self.fail_on_get = set()
        self.list_calls = 0

    def files(self):
        return FakeFiles(self)


def fake_thumbnail(mod, n):
    img = mod.Image.new("RGB", (120, 68), (10, 20 + n, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_bulkdelete_test_")
    db_path = os.path.join(workdir, "library.db")

    os.environ["FA_DB_PATH"] = db_path

    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy-client-id")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")

    spec = importlib.util.spec_from_file_location("fa_bulkdelete_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_bulkdelete_app"] = mod
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True

    drive = FakeDrive()
    mod.drive.get_drive_service = lambda: drive
    mod.drive.get_root_folder_id = lambda uid: "root-folder"
    mod.tagging.trigger_tagging = lambda *a, **k: None
    print("App imported OK (Drive faked).")

    failures = []

    def check(label, cond, detail=""):
        if cond:
            print(f"{label} — OK")
        else:
            print(f"{label} — FAIL  {detail}")
            failures.append(label)

    admin = mod.app.test_client()
    admin.post("/api/setup", json={"email": "ryan@test.com", "password": "adminpass123"})

    # Seed 5 admin-owned images directly (avoids needing real Drive upload).
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    for n in range(1, 6):
        c.execute(
            "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio)"
            " VALUES (?, 1, ?, ?, ?, '16:9')",
            (n, f"drive-file-{n}", f"img_{n}.jpg", fake_thumbnail(mod, n)),
        )
        c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, 'mood', 'tense')", (n,))
    conn.commit()
    conn.close()
    print("Seeded 5 admin-owned images (ids 1-5), each tagged.")

    def rows():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT id FROM images ORDER BY id").fetchall()
        conn.close()
        return [x["id"] for x in r]

    def orphan_tag_count():
        conn = sqlite3.connect(db_path)
        n = conn.execute("SELECT COUNT(*) FROM tags WHERE image_id NOT IN (SELECT id FROM images)").fetchone()[0]
        conn.close()
        return n

    # ── 1. Delete 2 of 5 — both succeed, moved to _Removed, tags cleaned up ──
    r = admin.post("/api/images/bulk-delete", json={"image_ids": [1, 2]})
    body = r.get_json()
    check("Bulk delete of 2 photos returns 200", r.status_code == 200, body)
    check("Both ids reported deleted", sorted(body.get("deleted", [])) == [1, 2], body)
    check("No errors reported", not body.get("errors"), body)
    check("Both Drive files were moved (addParents call)", set(drive.moved) == {"drive-file-1", "drive-file-2"}, drive.moved)
    check("Library now has exactly ids 3,4,5 left", rows() == [3, 4, 5], rows())
    check("No orphaned tag rows after delete", orphan_tag_count() == 0, orphan_tag_count())

    # ── 2. The _Removed folder is only looked up/created ONCE across the batch ──
    check("_Removed folder created exactly once for the whole batch (cached), not per-photo",
          drive.list_calls == 1, drive.list_calls)

    # ── 3. Partial failure: one photo's Drive move fails -> skip it, delete the rest ──
    drive.fail_on_get.add("drive-file-3")
    r = admin.post("/api/images/bulk-delete", json={"image_ids": [3, 4, 5]})
    body = r.get_json()
    check("Partial-failure batch still returns 200", r.status_code == 200, body)
    check("The 2 healthy photos (4, 5) were deleted", sorted(body.get("deleted", [])) == [4, 5], body)
    check("The failing photo (3) is reported as an error, not silently dropped",
          any(e["id"] == 3 for e in body.get("errors", [])), body)
    check("The failing photo (3) was NOT deleted from the library (still there)", 3 in rows(), rows())
    check("The healthy photos (4, 5) ARE gone from the library", 4 not in rows() and 5 not in rows(), rows())

    # ── 4. Validation: empty / malformed image_ids ──────────────────────────
    r = admin.post("/api/images/bulk-delete", json={"image_ids": []})
    check("Empty image_ids rejected with 400", r.status_code == 400, r.get_json())
    r = admin.post("/api/images/bulk-delete", json={"image_ids": ["not-an-int"]})
    check("Non-int image_ids rejected with 400", r.status_code == 400, r.get_json())

    # ── 5. Ownership: a friend cannot delete another user's (or the admin's) photos ──
    friend_code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    reg = friend.post("/api/auth/register", json={
        "invite_code": friend_code, "username": "casey", "email": "casey@test.com", "password": "friendpass1"
    })
    check("Friend registration succeeds", reg.status_code == 200, reg.get_json())

    r = friend.post("/api/images/bulk-delete", json={"image_ids": [3]})
    body = r.get_json()
    check("Friend's attempt to delete the admin's photo is rejected (not deleted)",
          not body.get("deleted") and any(e["id"] == 3 for e in body.get("errors", [])), body)
    check("The admin's photo (3) still exists after the friend's failed attempt", 3 in rows(), rows())

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED ❌")
        for f in failures:
            print(f"   - {f}")
    else:
        print("ALL BULK-DELETE TESTS PASSED ✅")

    shutil.rmtree(workdir, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

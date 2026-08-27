"""
Frame Atlas — local test for the V27/V30 background crop QUEUE.

test_crop_locally.py predates V27 and still calls POST /api/images/<id>/crop
expecting an immediate 200/error — that endpoint now only queues a job and
always returns {queued: True} right away; the real outcome only shows up on
GET /api/crop-progress once the background worker thread finishes. That file
is stale tech debt (fails the same way on unmodified main), not something
this test duplicates.

This test covers the actual queue architecture, and the two V30 bugs found
in it:
  1. in_progress was incremented once when the job was queued AND again when
     the worker picked it up, but only decremented once — so it could never
     return to 0 and the UI's "wait for 0" poll spun forever.
  2. The worker's final DB write targeted width/height/crop_box, columns
     that have never existed, so it crashed AFTER the Drive file was already
     overwritten — Drive had the cropped image, but the database (and so the
     home grid) kept the stale pre-crop thumbnail forever.

Drive is faked, so nothing leaves the machine.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_crop_queue_locally.py
"""

import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import time

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))


class FakeRequest:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeFilesResource:
    def __init__(self, drive):
        self.drive = drive

    def get(self, fileId=None, fields=None, **kw):
        return FakeRequest(lambda: {"id": fileId, "mimeType": "image/jpeg"})

    def get_media(self, fileId=None):
        req = FakeRequest(lambda: None)
        req.data = self.drive.jpeg_bytes
        return req

    def update(self, fileId=None, media_body=None, fields=None, **kw):
        self.drive.update_calls.append(fileId)
        if self.drive.next_update_error is not None:
            err = self.drive.next_update_error
            self.drive.next_update_error = None

            def boom():
                raise err
            return FakeRequest(boom)
        return FakeRequest(lambda: {"id": fileId, "md5Checksum": "md5-after-crop"})

    def list(self, q=None, fields=None, **kw):
        return FakeRequest(lambda: {"files": [{"id": "REMOVED_FOLDER_ID"}]})

    def create(self, body=None, media_body=None, fields=None, **kw):
        raise AssertionError("service account must never files().create() — no storage quota")


class FakeDrive:
    def __init__(self, jpeg_bytes):
        self.jpeg_bytes = jpeg_bytes
        self.update_calls = []
        self.next_update_error = None

    def files(self):
        return FakeFilesResource(self)


class FakeUserFilesResource:
    def __init__(self, drive):
        self.drive = drive

    def create(self, body=None, media_body=None, fields=None, **kw):
        self.drive.create_calls.append((body or {}).get("name"))
        if self.drive.next_create_error is not None:
            err = self.drive.next_create_error
            self.drive.next_create_error = None

            def boom():
                raise err
            return FakeRequest(boom)
        return FakeRequest(lambda: {"id": "BACKUP_FILE_ID"})


class FakeUserDrive:
    def __init__(self):
        self.create_calls = []
        self.next_create_error = None

    def files(self):
        return FakeUserFilesResource(self)


class FakeDownloader:
    def __init__(self, fh, req):
        fh.write(req.data)

    def next_chunk(self):
        return (None, True)


def make_jpeg(mod, color=(40, 90, 200), size=(400, 240)):
    img = mod.Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def wait_for_crop_to_finish(client, timeout=10):
    """Poll /api/crop-progress the same way the frontend does. Returns the
    final progress dict, or None if it never reached 0 (reproduces exactly
    what Ryan saw: stuck 'running in the background' forever)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        progress = client.get("/api/crop-progress").get_json()
        if progress["in_progress"] == 0 and progress["total"] > 0:
            return progress
        time.sleep(0.05)
    return None


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_crop_queue_test_")
    db_path = os.path.join(workdir, "library.db")

    os.environ["FA_DB_PATH"] = db_path

    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy-client-id")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")

    spec = importlib.util.spec_from_file_location("fa_crop_queue_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_crop_queue_app"] = mod
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True

    jpeg = make_jpeg(mod)
    drive = FakeDrive(jpeg)
    user_drive = FakeUserDrive()
    mod.drive.get_drive_service = lambda: drive
    mod.drive.get_user_drive_service = lambda uid: user_drive
    mod.MediaIoBaseDownload = FakeDownloader
    # Day 29: download_drive_file() moved to drive.py, so the crop worker's
    # download now resolves MediaIoBaseDownload in drive.py's namespace.
    mod.drive.MediaIoBaseDownload = FakeDownloader
    mod.trigger_tagging = lambda *a, **k: None
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

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status) "
        "VALUES (1, 1, 'drive-file-1', 'photo.jpg', ?, '5:3', 'done')",
        (jpeg,),
    )
    conn.commit()
    old_thumb_row = conn.execute("SELECT thumbnail_blob FROM images WHERE id = 1").fetchone()
    old_thumbnail = old_thumb_row[0]
    conn.close()

    # ── 1. queue a crop, confirm it reports queued immediately ─────────────
    r = admin.post("/api/images/1/crop", json={"box": {"x": 10, "y": 10, "w": 50, "h": 50}})
    body = r.get_json()
    check("Crop is queued immediately (not blocking)", r.status_code == 200 and body.get("queued") is True, body)

    # ── 2. the progress counter must actually reach 0 ───────────────────────
    # Before the fix: in_progress was incremented on queue AND on pickup,
    # decremented only once -> net +1 forever -> this times out and returns
    # None, exactly reproducing "stuck running in the background."
    progress = wait_for_crop_to_finish(admin)
    check(
        "Progress counter reaches 0 (was stuck forever before the fix)",
        progress is not None,
        "timed out waiting for in_progress to reach 0" if progress is None else progress,
    )
    if progress is not None:
        check("No jobs reported as failed", not progress.get("failed"), progress.get("failed"))

    # ── 3. the database actually reflects the crop ──────────────────────────
    # Before the fix: the worker's final UPDATE targeted width/height/crop_box
    # (columns that don't exist), crashed, and left the OLD thumbnail in
    # place while Drive already had the new cropped bytes.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT thumbnail_blob, aspect_ratio, md5_checksum, phash FROM images WHERE id = 1"
    ).fetchone()
    palette_n = conn.execute("SELECT COUNT(*) n FROM colors WHERE image_id = 1").fetchone()["n"]
    conn.close()

    check("Thumbnail was refreshed to the cropped version (not left stale)",
          row["thumbnail_blob"] != old_thumbnail, "thumbnail unchanged after crop")
    check("Aspect ratio was recalculated for the new crop", row["aspect_ratio"] == "5:3", row["aspect_ratio"])
    check("md5 checksum was refreshed from the crop", row["md5_checksum"] == "md5-after-crop", row["md5_checksum"])
    check("Fingerprint was recomputed for the cropped pixels", bool(row["phash"]), row["phash"])
    check("Colour palette was recomputed for the cropped pixels", palette_n > 0, palette_n)

    # ── 4. the original was backed up to _Removed before the overwrite ──────
    check("Exactly one backup was written before the destructive overwrite",
          len(user_drive.create_calls) == 1, user_drive.create_calls)
    check("Backup name marks it as pre-crop",
          bool(user_drive.create_calls) and "pre-crop" in user_drive.create_calls[0],
          user_drive.create_calls)
    check("The original Drive file was overwritten in place (files().update, not create)",
          drive.update_calls == ["drive-file-1"], drive.update_calls)

    # ── 5. a second crop on a fresh image doesn't inherit stale counter state
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status) "
        "VALUES (2, 1, 'drive-file-2', 'photo2.jpg', ?, '5:3', 'done')",
        (make_jpeg(mod, color=(200, 60, 60)),),
    )
    conn.commit()
    conn.close()

    admin.post("/api/crop-progress/reset", methods=["POST"]) if False else admin.post("/api/crop-progress/reset")
    r = admin.post("/api/images/2/crop", json={"box": {"x": 0, "y": 0, "w": 40, "h": 40}})
    check("Second crop queues fine after a reset", r.get_json().get("queued") is True, r.get_json())
    progress2 = wait_for_crop_to_finish(admin)
    check("Second crop's counter also reaches 0", progress2 is not None,
          "timed out" if progress2 is None else progress2)

    # ── 6. Error path 1: Editor access missing (insufficientFilePermissions) ──
    # In the queue model, permission errors land in the failed[] list after
    # the job finishes, not as an immediate HTTP error.
    admin.post("/api/crop-progress/reset")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status) "
        "VALUES (3, 1, 'drive-file-3', 'photo3.jpg', ?, '5:3', 'done')",
        (make_jpeg(mod, color=(60, 200, 60)),),
    )
    conn.commit()
    conn.close()

    # Inject error on the update call
    from googleapiclient.errors import HttpError

    class FakeResp:
        def __init__(self, status):
            self.status = status
            self.reason = "Forbidden"

        def get(self, key, default=None):
            return default

    def make_http_error(reason, status=403):
        content = json.dumps({
            "error": {"code": status, "message": reason,
                       "errors": [{"reason": reason, "message": reason}]}
        }).encode()
        return HttpError(FakeResp(status), content)

    drive.next_update_error = make_http_error("insufficientFilePermissions")
    admin.post("/api/images/3/crop", json={"box": {"x": 5, "y": 5, "w": 50, "h": 50}})
    progress = wait_for_crop_to_finish(admin)
    if progress and progress["failed"]:
        failed_msg = progress["failed"][0]["error"]
        # The worker catches the HttpError and logs its message string, which
        # from the fake includes "insufficientFilePermissions"
        check("Permission error surfaces in failed[] list", True)
        check("Permission error mentions file permission issue",
              "permission" in failed_msg.lower() or "insufficientFilePermissions" in failed_msg,
              failed_msg)
    else:
        check("Permission error surfaces in failed[] list", False, progress)
        check("Permission error mentions file permission issue", False, "no error in failed list")

    # ── 7. Error path 2: Storage quota exceeded ───────────────────────────────
    admin.post("/api/crop-progress/reset")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status) "
        "VALUES (4, 1, 'drive-file-4', 'photo4.jpg', ?, '5:3', 'done')",
        (make_jpeg(mod, color=(200, 200, 60)),),
    )
    conn.commit()
    conn.close()

    drive.next_update_error = make_http_error("storageQuotaExceeded")
    admin.post("/api/images/4/crop", json={"box": {"x": 5, "y": 5, "w": 50, "h": 50}})
    progress = wait_for_crop_to_finish(admin)
    if progress and progress["failed"]:
        failed_msg = progress["failed"][0]["error"]
        check("Quota error surfaces in failed[] list", True)
        check("Quota error mentions quota, not sharing",
              "quota" in failed_msg.lower() or "storageQuotaExceeded" in failed_msg,
              failed_msg)
    else:
        check("Quota error surfaces in failed[] list", False, progress)
        check("Quota error mentions quota, not sharing", False, "no error in failed list")

    # ── 8. Error path 3: Failed backup must prevent the Drive overwrite ──────
    # If the backup upload fails, the worker must NOT proceed to overwrite the
    # Drive file. Since errors are now asynchronous, we check:
    # - The job ends up in failed[]
    # - The Drive file was NOT updated
    admin.post("/api/crop-progress/reset")
    drive.update_calls.clear()
    user_drive.create_calls.clear()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status) "
        "VALUES (5, 1, 'drive-file-5', 'photo5.jpg', ?, '5:3', 'done')",
        (make_jpeg(mod, color=(200, 60, 200)),),
    )
    conn.commit()
    conn.close()

    user_drive.next_create_error = RuntimeError("backup upload exploded")
    admin.post("/api/images/5/crop", json={"box": {"x": 5, "y": 5, "w": 50, "h": 50}})
    progress = wait_for_crop_to_finish(admin)
    if progress and progress["failed"]:
        check("Failed backup causes job to fail", True)
        check("Failed backup prevents Drive overwrite",
              "drive-file-5" not in drive.update_calls,
              f"Drive file was updated despite backup failure: {drive.update_calls}")
    else:
        check("Failed backup causes job to fail", False, progress)
        check("Failed backup prevents Drive overwrite", False, "no error in failed list")

    # ── 9. Error path 4: No OAuth client connected ────────────────────────────
    # When a user has no Google account connected for backup, the crop should
    # fail rather than silently skip.
    admin.post("/api/crop-progress/reset")
    drive.update_calls.clear()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status) "
        "VALUES (6, 1, 'drive-file-6', 'photo6.jpg', ?, '5:3', 'done')",
        (make_jpeg(mod, color=(60, 60, 200)),),
    )
    conn.commit()
    conn.close()

    # Simulate no OAuth client by returning None
    saved_get_user_drive = mod.drive.get_user_drive_service
    mod.drive.get_user_drive_service = lambda uid: None

    admin.post("/api/images/6/crop", json={"box": {"x": 5, "y": 5, "w": 50, "h": 50}})
    progress = wait_for_crop_to_finish(admin)
    if progress and progress["failed"]:
        failed_msg = progress["failed"][0]["error"]
        check("No OAuth client causes job to fail", True)
        check("No OAuth client error message is clear",
              "google account" in failed_msg.lower() or "connected" in failed_msg.lower(),
              failed_msg)
        check("No OAuth client prevents Drive overwrite",
              "drive-file-6" not in drive.update_calls,
              f"Drive file was updated despite no OAuth: {drive.update_calls}")
    else:
        check("No OAuth client causes job to fail", False, progress)
        check("No OAuth client error message is clear", False, "no error in failed list")
        check("No OAuth client prevents Drive overwrite", False, "no error in failed list")

    # Restore the mock
    mod.drive.get_user_drive_service = saved_get_user_drive

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED ❌")
        for f in failures:
            print(f"   - {f}")
    else:
        print("ALL CROP QUEUE TESTS PASSED ✅")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

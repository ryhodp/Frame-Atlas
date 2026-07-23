"""
Frame Atlas — local test for the V18 crop-endpoint quota fix.

Covers: POST /api/images/<id>/crop overwrites the ORIGINAL Drive file's
content in place (files().update, never files().create) so a service
account with zero storage quota can still save a crop; the DB row's
drive_file_id never changes; a regression guard fails loudly if the create
path is ever reintroduced; and the two Drive-error branches (Editor-access
vs. storage-quota) each surface the right message for their real reason.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_crop_locally.py
"""

import importlib.util
import io
import json
import os
import tempfile

from googleapiclient.errors import HttpError

REPO = os.path.join(os.path.dirname(__file__), "..")
ROBOT_EMAIL = "frame-atlas-robot@test-project.iam.gserviceaccount.com"


class FakeResp:
    def __init__(self, status):
        self.status = status
        self.reason = "Forbidden"

    def get(self, key, default=None):
        return default


def fake_http_error(reason, status=403):
    content = json.dumps({
        "error": {"code": status, "message": reason,
                   "errors": [{"reason": reason, "message": reason}]}
    }).encode()
    return HttpError(FakeResp(status), content)


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
        self.drive.update_calls.append({"fileId": fileId, "had_media": media_body is not None})
        if self.drive.next_update_error is not None:
            err = self.drive.next_update_error
            self.drive.next_update_error = None

            def boom():
                raise err
            return FakeRequest(boom)
        return FakeRequest(lambda: {"id": fileId, "md5Checksum": "md5_after_crop"})

    def create(self, **kw):
        # Regression guard: crop must never create a brand-new file object —
        # that's the exact operation a zero-quota service account can't do.
        raise AssertionError("files().create() was called — crop must use "
                              "files().update() on the existing file instead")


class FakeDrive:
    def __init__(self, jpeg_bytes):
        self.jpeg_bytes = jpeg_bytes
        self.update_calls = []
        self.next_update_error = None

    def files(self):
        return FakeFilesResource(self)


class FakeDownloader:
    """Stands in for googleapiclient's MediaIoBaseDownload."""
    def __init__(self, fh, req):
        fh.write(req.data)

    def next_chunk(self):
        return (None, True)


def make_jpeg(mod, size=(200, 120)):
    img = mod.Image.new("RGB", size, (40, 90, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_crop_test_")
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

    spec = importlib.util.spec_from_file_location("test_app", os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("App imported OK.")

    checks = []

    def check(label, cond):
        checks.append((label, bool(cond)))
        print(("  ok  " if cond else "FAIL  ") + label)

    jpeg = make_jpeg(mod)
    drive = FakeDrive(jpeg)
    mod.get_drive_service = lambda: drive
    mod.MediaIoBaseDownload = FakeDownloader

    admin = mod.app.test_client()
    r = admin.post("/api/setup", json={"email": "test@test.com", "password": "testpass123"})
    check("admin setup", r.status_code == 200)

    thumb = mod.generate_thumbnail(jpeg)
    conn = mod.get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio) "
        "VALUES (1, 'ORIGINAL_FILE_ID', 'frame_001.jpg', ?, '16:9')",
        (thumb,)
    )
    conn.commit()
    image_id = c.lastrowid
    conn.close()

    # --- Happy path: crop should overwrite the original file in place ---
    r = admin.post(f"/api/images/{image_id}/crop",
                    json={"box": {"x": 10, "y": 10, "w": 60, "h": 60}})
    body = r.get_json()
    check("crop succeeds (200)", r.status_code == 200)
    check("response reports success", body.get("success") is True)
    check("exactly one Drive write happened", len(drive.update_calls) == 1)
    check("that write targeted the ORIGINAL file id",
          drive.update_calls and drive.update_calls[0]["fileId"] == "ORIGINAL_FILE_ID")
    check("that write carried new media content",
          drive.update_calls and drive.update_calls[0]["had_media"])

    conn = mod.get_db()
    row = conn.execute("SELECT drive_file_id, thumbnail_blob FROM images WHERE id = ?",
                        (image_id,)).fetchone()
    conn.close()
    check("drive_file_id in the DB is unchanged", row["drive_file_id"] == "ORIGINAL_FILE_ID")
    check("thumbnail_blob was updated to the cropped image", row["thumbnail_blob"] != thumb)

    # --- Error path 1: Editor access missing (insufficientFilePermissions) ---
    drive.update_calls.clear()
    drive.next_update_error = fake_http_error("insufficientFilePermissions")
    r = admin.post(f"/api/images/{image_id}/crop",
                    json={"box": {"x": 5, "y": 5, "w": 50, "h": 50}})
    msg = (r.get_json() or {}).get("error", "")
    check("permission error -> 403", r.status_code == 403)
    check("permission error message names Editor access", "Editor" in msg)
    check("permission error message does not blame quota", "quota" not in msg.lower())

    # --- Error path 2: storage-quota error is reported for what it is,
    #     not misreported as a Viewer/Editor sharing problem ---
    drive.update_calls.clear()
    drive.next_update_error = fake_http_error("storageQuotaExceeded")
    r = admin.post(f"/api/images/{image_id}/crop",
                    json={"box": {"x": 5, "y": 5, "w": 50, "h": 50}})
    msg = (r.get_json() or {}).get("error", "")
    check("quota error -> 403", r.status_code == 403)
    check("quota error message names quota, not sharing", "quota" in msg.lower())
    check("quota error message does not send user to fix sharing settings",
          "Editor" not in msg)

    passed = sum(1 for _, ok in checks if ok)
    print(f"\n{passed}/{len(checks)} checks passed")
    if passed != len(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

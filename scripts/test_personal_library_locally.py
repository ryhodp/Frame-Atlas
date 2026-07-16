"""
Frame Atlas — local test for V17 personal libraries.

Covers: folder-link parsing, /api/sync/connect-folder (happy path + not-shared
+ not-a-folder + junk), /api/account/setup-status, syncing a friend's folder
through the shared service account (with a fake Drive), library isolation,
the 1000-image soft cap, friend-owned delete + sync exclusions (deleted
images never re-import), sync-status privacy scoping, and the post-sync
auto-tag trigger honoring per-user Gemini keys.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_personal_library_locally.py
"""

import importlib.util
import io
import json
import os
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")

ROBOT_EMAIL = "frame-atlas-robot@test-project.iam.gserviceaccount.com"


# ── Fake Google Drive ────────────────────────────────────────────────────────
# folders: {folder_id: {"name": ..., "shared": bool, "files": [file dicts]}}
class FakeRequest:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeFilesResource:
    def __init__(self, drive):
        self.drive = drive

    def get(self, fileId=None, fields=None, **kw):
        def run():
            folder = self.drive.folders.get(fileId)
            if folder is None or not folder["shared"]:
                raise Exception(f"<HttpError 404 ... 'File not found: {fileId}' notFound>")
            return {"id": fileId, "name": folder["name"],
                    "mimeType": "application/vnd.google-apps.folder"}
        # Plain files (not folders) — for the not-a-folder test
        if fileId in self.drive.plain_files:
            meta = self.drive.plain_files[fileId]
            return FakeRequest(lambda: {"id": fileId, "name": meta["name"], "mimeType": meta["mimeType"]})
        return FakeRequest(run)

    def list(self, q=None, **kw):
        def run():
            # q looks like: '<folder_id>' in parents and trashed=false
            folder_id = q.split("'")[1]
            folder = self.drive.folders.get(folder_id)
            if folder is None or not folder["shared"]:
                raise Exception(f"<HttpError 404 ... 'File not found: {folder_id}' notFound>")
            return {"files": folder["files"]}
        return FakeRequest(run)

    def get_media(self, fileId=None):
        req = FakeRequest(lambda: None)
        req.data = self.drive.jpeg_bytes
        return req


class FakeDrive:
    def __init__(self, jpeg_bytes):
        self.folders = {}
        self.plain_files = {}
        self.jpeg_bytes = jpeg_bytes

    def files(self):
        return FakeFilesResource(self)


class FakeDownloader:
    """Stands in for googleapiclient's MediaIoBaseDownload."""
    def __init__(self, fh, req):
        fh.write(req.data)

    def next_chunk(self):
        return (None, True)


def make_jpeg(mod):
    img = mod.Image.new("RGB", (160, 90), (200, 60, 40))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_personal_lib_test_")
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

    jpeg = make_jpeg(mod)
    drive = FakeDrive(jpeg)
    mod.get_drive_service = lambda: drive
    mod.MediaIoBaseDownload = FakeDownloader

    FRIEND_FOLDER = "FriendFolderId_ABC123xyz"
    drive.folders[FRIEND_FOLDER] = {
        "name": "Casey Inspo", "shared": False,
        "files": [
            {"id": f"file_{i}", "name": f"frame_{i}.jpg", "mimeType": "image/jpeg",
             "size": "1000", "md5Checksum": f"md5_{i}"}
            for i in range(5)
        ],
    }
    drive.plain_files["PlainFileId_999999999"] = {"name": "single.jpg", "mimeType": "image/jpeg"}

    admin = mod.app.test_client()
    r = admin.post("/api/setup", json={"email": "test@test.com", "password": "testpass123"})
    assert r.status_code == 200, r.get_json()

    friend_code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    r = friend.post("/api/auth/register", json={"invite_code": friend_code, "username": "casey", "password": "friendpass1"})
    assert r.status_code == 200, r.get_json()
    FRIEND_ID = 2

    # 1. Folder-link parsing: every paste format friends will realistically use.
    p = mod.parse_drive_folder_id
    assert p("https://drive.google.com/drive/folders/ABC123_-xyz?usp=sharing") == "ABC123_-xyz"
    assert p("https://drive.google.com/drive/u/0/folders/ABC123_-xyz") == "ABC123_-xyz"
    assert p("https://drive.google.com/open?id=ABC123_-xyz") == "ABC123_-xyz"
    assert p("  FriendFolderId_ABC123xyz  ") == "FriendFolderId_ABC123xyz"
    assert p("not a link at all") is None
    assert p("") is None
    print("1. parse_drive_folder_id handles URLs, ?id= form, bare IDs, junk.")

    # 2. Connect before sharing → 403 with a message that names the robot email.
    r = friend.post("/api/sync/connect-folder", json={"folder": FRIEND_FOLDER})
    body = r.get_json()
    assert r.status_code == 403 and body.get("not_shared") and ROBOT_EMAIL in body["error"], body
    print("2. Unshared folder → clear 'share it with <robot email>' error.")

    # 3. Junk input and non-folder links get friendly 400s.
    r = friend.post("/api/sync/connect-folder", json={"folder": "banana"})
    assert r.status_code == 400, r.get_json()
    r = friend.post("/api/sync/connect-folder", json={"folder": "PlainFileId_999999999"})
    assert r.status_code == 400 and "not a folder" in r.get_json()["error"], r.get_json()
    print("3. Junk input → 400; a file link (not folder) → 400.")

    # 4. Share it, connect again → saved, real name + image count returned.
    drive.folders[FRIEND_FOLDER]["shared"] = True
    r = friend.post("/api/sync/connect-folder", json={"folder": f"https://drive.google.com/drive/folders/{FRIEND_FOLDER}?usp=sharing"})
    body = r.get_json()
    assert r.status_code == 200 and body["folder_name"] == "Casey Inspo" and body["image_count"] == 5, body
    print("4. Shared folder connects: name pulled from Drive, 5 images counted.")

    # 5. Setup-status reflects reality for both roles.
    s = friend.get("/api/account/setup-status").get_json()
    assert s["service_account_email"] == ROBOT_EMAIL
    assert s["folder_connected"] and s["folder_name"] == "Casey Inspo"
    assert s["image_count"] == 0 and s["image_cap"] == 1000 and s["has_gemini_key"] is False, s
    s_admin = admin.get("/api/account/setup-status").get_json()
    assert s_admin["image_cap"] is None and s_admin["has_gemini_key"] is True, s_admin
    print("5. setup-status: robot email, folder, 0/1000 images, key states correct.")

    # 6. Friend sync runs through the SERVICE ACCOUNT (no OAuth needed at all).
    tag_calls = []
    mod.trigger_tagging = lambda user_id=None: tag_calls.append(user_id)
    mod.sync_folder_worker(FRIEND_FOLDER, FRIEND_ID)
    assert not mod.sync_state["errors"], mod.sync_state["errors"]
    s = friend.get("/api/account/setup-status").get_json()
    assert s["image_count"] == 5, s
    print("6. Friend's 5 images synced via the robot account — no Google sign-in.")

    # 7. Isolation: friend sees 5, admin sees 0 (and vice-versa privacy holds).
    friend_imgs = friend.get("/api/search").get_json()["images"]
    admin_imgs = admin.get("/api/search").get_json()["images"]
    assert len(friend_imgs) == 5 and len(admin_imgs) == 0, (len(friend_imgs), len(admin_imgs))
    print("7. Libraries fully isolated: friend sees 5, admin sees 0.")

    # 8. Keyless friend's sync did NOT auto-tag; with a key saved it does.
    assert tag_calls == [], tag_calls
    r = friend.post("/api/account/gemini-key", json={"key": "friend-key-xyz"})
    assert r.status_code == 200, r.get_json()
    mod.sync_folder_worker(FRIEND_FOLDER, FRIEND_ID)
    assert tag_calls == [FRIEND_ID], tag_calls
    print("8. Post-sync auto-tag: skipped while keyless, fires (scoped) once a key exists.")

    # 9. Soft cap: shrink the cap, add more files, sync stops politely.
    mod.PERSONAL_LIBRARY_CAP = 7
    drive.folders[FRIEND_FOLDER]["files"] += [
        {"id": f"file_extra_{i}", "name": f"extra_{i}.jpg", "mimeType": "image/jpeg",
         "size": "1000", "md5Checksum": f"md5x_{i}"}
        for i in range(5)
    ]
    mod.sync_folder_worker(FRIEND_FOLDER, FRIEND_ID)
    s = friend.get("/api/account/setup-status").get_json()
    assert s["image_count"] == 7, s
    assert any("limit" in e for e in mod.sync_state["errors"]), mod.sync_state["errors"]
    print("9. Soft cap: sync stopped at the limit with a clear message.")

    # 10. Friend deletes their own image: DB-only, excluded from future syncs.
    victim = friend.get("/api/search").get_json()["images"][0]
    r = friend.delete(f"/api/images/{victim['id']}")
    assert r.status_code == 200 and r.get_json()["moved_to"] is None, r.get_json()
    s = friend.get("/api/account/setup-status").get_json()
    assert s["image_count"] == 6, s
    mod.sync_folder_worker(FRIEND_FOLDER, FRIEND_ID)  # room under cap now
    s = friend.get("/api/account/setup-status").get_json()
    assert s["image_count"] == 7, s  # refilled from remaining folder files, victim NOT re-imported
    ids_now = {i["filename"] for i in friend.get("/api/search").get_json()["images"]}
    assert victim["filename"] not in ids_now, "deleted image was re-imported by sync"
    print("10. Friend delete sticks: removed from library, never re-imported.")

    # 11. Friend cannot delete someone else's image.
    drive.folders["AdminFolderId_ABC123xyz"] = {"name": "Ryan Inspo", "shared": True, "files": [
        {"id": "admin_file_1", "name": "ryan_1.jpg", "mimeType": "image/jpeg", "size": "1000", "md5Checksum": "md5r"}
    ]}
    r = admin.post("/api/sync/connect-folder", json={"folder": "AdminFolderId_ABC123xyz"})
    assert r.status_code == 200, r.get_json()
    mod.sync_folder_worker("AdminFolderId_ABC123xyz", 1)
    admin_img = admin.get("/api/search").get_json()["images"][0]
    r = friend.delete(f"/api/images/{admin_img['id']}")
    assert r.status_code == 404, r.get_json()
    print("11. Friend deleting an admin image → 404 (ownership enforced).")

    # 12. Sync-status privacy: friend's sync details hidden from other users.
    mod.sync_state.update({"in_progress": True, "user_id": FRIEND_ID,
                           "current_file": "casey_secret_project.jpg", "errors": []})
    other_code = admin.post("/api/admin/invite-codes").get_json()["code"]
    other = mod.app.test_client()
    r = other.post("/api/auth/register", json={"invite_code": other_code, "username": "sam", "password": "sampass12"})
    assert r.status_code == 200, r.get_json()
    view_other = other.get("/api/sync/status").get_json()
    assert view_other["yours"] is False and view_other["current_file"] == "", view_other
    view_owner = friend.get("/api/sync/status").get_json()
    assert view_owner["yours"] is True and view_owner["current_file"] == "casey_secret_project.jpg", view_owner
    view_admin = admin.get("/api/sync/status").get_json()
    assert view_admin["current_file"] == "casey_secret_project.jpg", view_admin
    mod.sync_state.update({"in_progress": False, "user_id": None, "current_file": ""})
    print("12. Sync status: owner + admin see details, other users just see 'busy'.")

    # 13. start_sync no longer demands a Google sign-in for friends.
    r = friend.post("/api/sync/start")
    assert r.status_code == 200, r.get_json()
    import time
    for _ in range(50):
        if not mod.sync_state["in_progress"]:
            break
        time.sleep(0.1)
    print("13. /api/sync/start works for a friend with zero OAuth setup.")

    print("\nAll 13 personal-library checks passed.")


if __name__ == "__main__":
    main()

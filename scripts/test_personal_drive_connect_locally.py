"""
Frame Atlas — local test for Day 14 Stage 2a: per-user Google Drive connect
(generalizing what used to be admin-only). Covers what's testable WITHOUT a
real Google account: route access (no longer admin-gated), /api/config
exposing the Picker credentials, the get_root_folder_id() multi-user fix,
and start_sync's "not connected yet" guard. Real OAuth + Picker flow still
needs a live manual check in the browser.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_personal_drive_connect_locally.py
"""

import importlib.util
import os
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_drive_connect_test_")
    db_path = os.path.join(workdir, "library.db")

    src = open(os.path.join(REPO, "backend", "app.py")).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
    assert patched != src, "Could not find DB_PATH line to patch"
    open(os.path.join(workdir, "app.py"), "w").write(patched)

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy-client-id")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")
    os.environ["GOOGLE_PICKER_API_KEY"] = "dummy-picker-key"
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    spec = importlib.util.spec_from_file_location("test_app", os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("App imported OK.")

    admin = mod.app.test_client()
    r = admin.post("/api/setup", json={"email": "test@test.com", "password": "testpass123"})
    assert r.status_code == 200, r.get_json()

    friend_code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    r = friend.post("/api/auth/register", json={"invite_code": friend_code, "username": "casey", "password": "friendpass1"})
    assert r.status_code == 200, r.get_json()

    # 1. /api/config exposes the OAuth client id + Picker key to any logged-in user.
    r = friend.get("/api/config")
    body = r.get_json()
    assert body["google_client_id"] == "dummy-client-id", body
    assert body["google_picker_api_key"] == "dummy-picker-key", body
    print("1. /api/config exposes google_client_id and google_picker_api_key.")

    # 2. A non-admin friend can now reach /api/auth/status, /api/sync/settings
    # (previously 403 — Day 14 Stage 1 gated these admin-only).
    assert friend.get("/api/auth/status").status_code == 200, friend.get("/api/auth/status").get_json()
    assert friend.get("/api/sync/settings").status_code == 200
    print("2. Friend can reach /api/auth/status and /api/sync/settings (no longer admin-only).")

    # 3. Friend can set their own sync folder.
    r = friend.post("/api/sync/settings", json={"folder_id": "friend-folder-id", "folder_name": "My Shots"})
    assert r.status_code == 200, r.get_json()
    r = friend.get("/api/sync/settings").get_json()
    assert r == {"folder_id": "friend-folder-id", "folder_name": "My Shots", "last_sync": None}, r
    print("3. Friend's own sync folder saved and read back correctly.")

    # 4. Admin's sync settings are untouched/independent from the friend's.
    admin.post("/api/sync/settings", json={"folder_id": "1LHPVyo3QjOEcizc1Io2UVjxzX4FQ7yDG", "folder_name": "Inspiration Images"})
    assert admin.get("/api/sync/settings").get_json()["folder_id"] == "1LHPVyo3QjOEcizc1Io2UVjxzX4FQ7yDG"
    assert friend.get("/api/sync/settings").get_json()["folder_id"] == "friend-folder-id"
    print("4. Admin's and the friend's sync settings don't clobber each other.")

    # 5. get_root_folder_id(user_id) returns the RIGHT user's folder, not
    # just whichever sync_settings row is newest overall (the old bug).
    assert mod.get_root_folder_id(1) == "1LHPVyo3QjOEcizc1Io2UVjxzX4FQ7yDG", mod.get_root_folder_id(1)
    assert mod.get_root_folder_id(2) == "friend-folder-id", mod.get_root_folder_id(2)
    print("5. get_root_folder_id() is correctly scoped per user (admin's row is older, still returns correctly).")

    # 6. Friend trying to sync without connecting Google first gets a clear
    # error, not a crash or a silent no-op.
    r = friend.post("/api/sync/start")
    body = r.get_json()
    assert r.status_code == 400 and body["error"] == "not_signed_in", body
    print("6. Starting a sync with no Google connection returns a clean 'not_signed_in' error.")

    # 7. Picker-token endpoint requires a Google connection.
    r = friend.get("/api/drive/picker-token")
    assert r.status_code == 401 and r.get_json()["error"] == "not_signed_in", r.get_json()
    print("7. /api/drive/picker-token 401s for a user with no Google connection yet.")

    # 8. Both routes still require login at all (before_request gate intact).
    anon = mod.app.test_client()
    assert anon.get("/api/auth/status").status_code == 401
    assert anon.get("/api/drive/picker-token").status_code == 401
    print("8. Logged-out requests still get 401 on both routes.")

    # 9. /api/upload stays admin-only (Stage 2 doesn't touch this).
    assert friend.post("/api/upload").status_code == 403
    print("9. /api/upload is still admin-only.")

    print("\nAll personal-Drive-connect (Stage 2a) checks passed.")


if __name__ == "__main__":
    main()

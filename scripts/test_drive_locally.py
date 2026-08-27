"""
Frame Atlas — local test for Day 29 (V71): the Google Drive layer split into
backend/drive.py.

drive.py was previously exercised only indirectly, through the ~11 test scripts
that swap its functions for fakes. This gives it direct coverage:
  - parse_drive_folder_id (pure string logic — URLs, ?id= form, bare IDs, junk)
  - get_root_folder_id (per-user scoping + the hardcoded fallback)
  - drive_error_reason (HttpError reason extraction, degrades to None)
  - get_service_account_email (reads GOOGLE_DRIVE_CREDENTIALS, never raises)
  - the split wiring: app.py exposes `drive`, the moved names are NOT bare
    globals on app.py anymore, and the qualified call sites resolve.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_drive_locally.py
"""

import importlib.util
import json
import os
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, "backend"))

PASS = 0
FAIL = 0


def check(label, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f"FAIL  {label}" + (f"  ({extra!r})" if extra is not None else ""))


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_drive_test_")
    os.environ["FA_DB_PATH"] = os.path.join(workdir, "library.db")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"
    # Set BEFORE app import so get_service_account_email() has something to read.
    os.environ["GOOGLE_DRIVE_CREDENTIALS"] = json.dumps(
        {"client_email": "robot@frame-atlas.iam.gserviceaccount.com", "type": "service_account"}
    )

    spec = importlib.util.spec_from_file_location("fa_drive_test_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_drive_test_app"] = mod
    spec.loader.exec_module(mod)
    print("App imported OK.")

    drive = mod.drive

    # ── 1. the split wiring ───────────────────────────────────────────────────
    print("\n1. Split wiring")
    moved = [
        "get_drive_service", "get_user_drive_service", "get_user_credentials",
        "get_oauth_flow", "get_service_account_email", "parse_drive_folder_id",
        "list_images_in_folder", "get_root_folder_id", "get_or_create_removed_folder",
        "download_drive_file", "drive_error_reason",
        "REMOVED_FOLDER_NAME", "PERSONAL_LIBRARY_CAP", "UPLOAD_SCOPES",
    ]
    for name in moved:
        check(f"drive.{name} exists", hasattr(drive, name))
    # The whole point of qualifying: these are NOT bare attributes on app.py.
    leaked = [n for n in moved if n in vars(mod)]
    check("no moved name leaked back onto app.py's namespace", leaked == [], leaked)
    check("app.py still imports MediaIoBaseDownload (sync worker uses it)",
          hasattr(mod, "MediaIoBaseDownload"))
    check("drive.py has its own MediaIoBaseDownload (download_drive_file uses it)",
          hasattr(drive, "MediaIoBaseDownload"))

    # ── 2. parse_drive_folder_id ─────────────────────────────────────────────
    print("\n2. parse_drive_folder_id")
    p = drive.parse_drive_folder_id
    cases = {
        "https://drive.google.com/drive/folders/1AbC_def-GHI2345?usp=sharing": "1AbC_def-GHI2345",
        "https://drive.google.com/drive/u/0/folders/1AbC_def-GHI2345": "1AbC_def-GHI2345",
        "https://drive.google.com/open?id=1AbC_def-GHI2345": "1AbC_def-GHI2345",
        "1AbC_def-GHI2345extra": "1AbC_def-GHI2345extra",  # bare ID, 15+ url-safe chars
        "": None,
        "   ": None,
        "not a folder link": None,
        "short": None,
    }
    for raw, expected in cases.items():
        check(f"parse({raw!r}) -> {expected!r}", p(raw) == expected, p(raw))

    # ── 3. get_root_folder_id: per-user + fallback ───────────────────────────
    print("\n3. get_root_folder_id")
    admin = mod.app.test_client()
    assert admin.post("/api/setup", json={"email": "a@a.com", "password": "testpass123"}).status_code == 200
    HARDCODED = "1LHPVyo3QjOEcizc1Io2UVjxzX4FQ7yDG"
    check("unknown user falls back to the hardcoded folder",
          drive.get_root_folder_id(999) == HARDCODED, drive.get_root_folder_id(999))
    admin.post("/api/sync/settings", json={"folder_id": "admin-folder", "folder_name": "Mine"})
    code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    assert friend.post("/api/auth/register", json={
        "invite_code": code, "username": "casey", "email": "c@c.com", "password": "friendpass1"
    }).status_code == 200
    friend.post("/api/sync/settings", json={"folder_id": "casey-folder", "folder_name": "Hers"})
    check("admin's folder is admin's", drive.get_root_folder_id(1) == "admin-folder", drive.get_root_folder_id(1))
    check("friend's folder is the friend's (not whichever row is newest)",
          drive.get_root_folder_id(2) == "casey-folder", drive.get_root_folder_id(2))

    # ── 4. drive_error_reason ───────────────────────────────────────────────
    print("\n4. drive_error_reason")
    from googleapiclient.errors import HttpError

    class FakeResp:
        status = 403
        reason = "Forbidden"

    body = json.dumps({"error": {"errors": [{"reason": "insufficientFilePermissions"}]}}).encode()
    check("extracts reason from a real HttpError",
          drive.drive_error_reason(HttpError(FakeResp(), body)) == "insufficientFilePermissions")
    check("non-HttpError -> None", drive.drive_error_reason(ValueError("nope")) is None)
    check("HttpError with unparseable body -> None (does not raise)",
          drive.drive_error_reason(HttpError(FakeResp(), b"not json")) is None)

    # ── 5. get_service_account_email ────────────────────────────────────────
    print("\n5. get_service_account_email")
    check("reads client_email out of GOOGLE_DRIVE_CREDENTIALS",
          drive.get_service_account_email() == "robot@frame-atlas.iam.gserviceaccount.com",
          drive.get_service_account_email())

    print(f"\n{PASS} passed, {FAIL} failed")
    if FAIL:
        sys.exit(1)
    print("All drive.py checks passed.")


if __name__ == "__main__":
    main()

"""
Frame Atlas — boots a patched local backend (throwaway DB) that serves the
REAL built frontend (frontend/dist) from the same origin, for eyeballing UI
changes in an actual browser. Unlike the test_*_locally.py scripts (which
only drive Flask's test client, no real server), this stays running so you
can click around.

Seeds one admin (ryan@test.com / adminpass123) and one friend account
(casey / friendpass1) — printed on startup.

Run `npm run build` in frontend/ first so frontend/dist is up to date.

Note: port 5000 collides with macOS ControlCenter's AirPlay Receiver on
this machine — defaults to 8080 (matches the real Railway deploy) instead.

Usage:
    scripts/.venv/bin/python scripts/run_local_for_browser_check.py
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")

# Reuse the fake Google Drive from the V17 test harness so the personal-
# library flow (connect folder → sync) is fully clickable locally.
sys.path.insert(0, os.path.dirname(__file__))
from test_personal_library_locally import FakeDrive, FakeDownloader, ROBOT_EMAIL

workdir = tempfile.mkdtemp(prefix="frame_atlas_browser_check_")
db_path = os.path.join(workdir, "library.db")

src = open(os.path.join(REPO, "backend", "app.py")).read()
patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
assert patched != src
open(os.path.join(workdir, "app.py"), "w").write(patched)

# Serve the real built frontend (frontend/dist, built via `npm run build`) so
# the browser can hit ONE origin (port 5000) for both the app and /api — no
# vite dev-server proxy involved, sidestepping whatever's intercepting /api
# on 5173 in this environment.
dist_dir = os.path.join(REPO, "frontend", "dist")
assert os.path.isdir(dist_dir), "Run `npm run build` in frontend/ first"
shutil.copytree(dist_dir, os.path.join(workdir, "static"))

os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy-client-id")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
os.environ["GEMINI_API_KEY"] = "admin-shared-key"
os.environ["GOOGLE_PICKER_API_KEY"] = "dummy-picker-key"
os.environ["FLASK_SECRET_KEY"] = "local-browser-check-not-for-prod"
os.environ["GOOGLE_DRIVE_CREDENTIALS"] = json.dumps({
    "type": "service_account", "client_email": ROBOT_EMAIL, "project_id": "test"
})

spec = importlib.util.spec_from_file_location("test_app", os.path.join(workdir, "app.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Fake Drive: one folder already "shared" with the robot (paste its ID to
# connect), one not shared yet (to see the friendly error). Images are
# generated JPEGs in assorted colors/sizes so the grid looks real.
def _jpeg(color, size):
    img = mod.Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()

drive = FakeDrive(_jpeg((180, 90, 40), (800, 450)))
COLORS = [(180, 90, 40), (40, 90, 180), (30, 140, 90), (140, 40, 120),
          (200, 170, 60), (60, 60, 70), (220, 120, 100), (90, 140, 200)]
SIZES = [(800, 450), (450, 800), (600, 600), (800, 340)]
drive.folders["SharedDemoFolder_ABC123"] = {
    "name": "Casey Inspo", "shared": True,
    "files": [
        {"id": f"demo_{i}", "name": f"inspo_{i:02d}.jpg", "mimeType": "image/jpeg",
         "size": "1000", "md5Checksum": f"demo_md5_{i}"}
        for i in range(8)
    ],
}
drive.folders["UnsharedDemoFolder_XYZ789"] = {"name": "Locked", "shared": False, "files": []}

# Vary the downloaded image per file id so thumbnails differ
_orig_files = drive.files
def _files():
    res = _orig_files()
    orig_get_media = res.get_media
    def get_media(fileId=None):
        idx = int(fileId.rsplit("_", 1)[1]) if fileId.startswith("demo_") else 0
        req = orig_get_media(fileId=fileId)
        req.data = _jpeg(COLORS[idx % len(COLORS)], SIZES[idx % len(SIZES)])
        return req
    res.get_media = get_media
    return res
drive.files = _files

mod.get_drive_service = lambda: drive
mod.MediaIoBaseDownload = FakeDownloader
print("Fake Drive active:")
print("  shareable folder ID:  SharedDemoFolder_ABC123  (connects + syncs 8 images)")
print("  unshared folder ID:   UnsharedDemoFolder_XYZ789 (shows the share-first error)")

# Seed an admin + one friend account with credentials printed below so the
# browser check can log straight in without going through /register by hand.
admin = mod.app.test_client()
admin.post("/api/setup", json={"email": "ryan@test.com", "password": "adminpass123"})
code = admin.post("/api/admin/invite-codes").get_json()["code"]
friend = mod.app.test_client()
friend.post("/api/auth/register", json={"invite_code": code, "username": "casey", "password": "friendpass1"})

port = int(os.environ.get("PORT", 8080))
print(f"Admin login:  ryan@test.com / adminpass123")
print(f"Friend login: casey / friendpass1")
print(f"DB: {db_path}")
print(f"Starting server on http://localhost:{port} ...")

mod.app.run(port=port, debug=False)

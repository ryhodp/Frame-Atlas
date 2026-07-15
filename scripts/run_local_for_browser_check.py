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
import os
import shutil
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")

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

spec = importlib.util.spec_from_file_location("test_app", os.path.join(workdir, "app.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

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

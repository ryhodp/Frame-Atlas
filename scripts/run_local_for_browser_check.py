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

# ── V18: crop-workflow support ────────────────────────────────────────────────
# The crop endpoint needs more from Drive than sync ever did: file metadata
# with parents, uploading the cropped copy, moving the original to _Removed,
# and delete for the rollback path. Wire those into the fake, and replace the
# flat-color demo images with crop test cases (letterbox bars, IG-style white
# chrome, full-bleed photos with nothing to crop).
import hashlib
import random as _random
from PIL import Image as PILImage, ImageDraw
from test_personal_library_locally import FakeFilesResource, FakeRequest

ROOT_REMOVED_NAME = "_Removed"


def _noise_photo(w, h, seed, base):
    """A block with real photographic texture (noise + soft shapes) so the
    crop detector reads it as content, never as flat chrome."""
    rnd = _random.Random(seed)
    noise = PILImage.effect_noise((w, h), 48).convert("RGB")
    img = PILImage.new("RGB", (w, h), base)
    img = PILImage.blend(img, noise, 0.45)
    d = ImageDraw.Draw(img)
    for _ in range(14):
        x0, y0 = rnd.randint(0, w - 1), rnd.randint(0, h - 1)
        x1, y1 = min(w, x0 + rnd.randint(40, w // 2)), min(h, y0 + rnd.randint(40, h // 2))
        color = tuple(min(255, max(0, c + rnd.randint(-70, 70))) for c in base)
        d.ellipse([x0, y0, x1, y1], fill=color)
    return img


def _to_jpeg(img):
    # PNG, despite the name: lossless keeps the letterbox bars EXACTLY black.
    # JPEG encoding rippled the last MCU row of the bottom bar (lum 1-11,
    # varying per encode since the noise is unseeded), which occasionally
    # read as a bottom-anchored landmark band and legitimately changed the
    # detected crop between downloads. Browsers and Pillow both sniff bytes,
    # so the .jpg filenames and image/jpeg mimeType in the fake metadata
    # still work fine.
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _demo_image_uncached(idx):
    """Each demo file is a crop-workflow test case."""
    base_colors = [(150, 90, 50), (50, 90, 150), (40, 130, 90), (130, 50, 110),
                   (170, 140, 60), (80, 80, 95), (180, 110, 90), (90, 130, 170)]
    base = base_colors[idx % len(base_colors)]
    if idx in (0, 5):        # vertical letterbox: black bars top+bottom
        img = PILImage.new("RGB", (1080, 1920), (0, 0, 0))
        img.paste(_noise_photo(1080, 608, idx, base), (0, 656))
    elif idx == 1:           # pillarbox: black bars left+right
        img = PILImage.new("RGB", (1920, 1080), (0, 0, 0))
        img.paste(_noise_photo(1440, 1080, idx, base), (240, 0))
    elif idx in (2, 6):      # IG screenshot: white chrome above and below
        img = PILImage.new("RGB", (1080, 1850), (255, 255, 255))
        img.paste(_noise_photo(1080, 1350, idx, base), (0, 200))
    elif idx == 4:           # dark-gray letterbox (uniform but not pure black)
        img = PILImage.new("RGB", (1080, 1920), (12, 12, 12))
        img.paste(_noise_photo(1080, 810, idx, base), (0, 555))
    else:                    # idx 3, 7: full-bleed photo — nothing to crop
        img = _noise_photo(1080, 1350, idx, base)
    return _to_jpeg(img)


# Generate each demo image exactly ONCE at boot. PIL's effect_noise is
# unseeded, so regenerating per download would hand every request different
# bytes — sync, detection, and any debugging probe would each be looking at
# a different image, which makes crop results impossible to reason about.
_DEMO_CACHE = {}
def _demo_image(idx):
    if idx not in _DEMO_CACHE:
        _DEMO_CACHE[idx] = _demo_image_uncached(idx)
    return _DEMO_CACHE[idx]


drive.uploaded = {}          # id -> cropped bytes (from files().create)
drive.file_meta = {f"demo_{i}": {"parents": ["SharedDemoFolder_ABC123"], "mimeType": "image/jpeg"}
                   for i in range(8)}
drive.removed_folders = {}   # root folder id -> _Removed folder id
drive.id_counter = 0

# A second folder with its own file ids so the friend account can sync
# without colliding with the admin's rows (images.drive_file_id is UNIQUE —
# in real life every friend syncs their own folder, so ids never collide).
drive.folders["CaseyOwnFolder_DEF456"] = {
    "name": "Casey Personal", "shared": True,
    "files": [
        {"id": f"casey_{i}", "name": f"casey_{i:02d}.jpg", "mimeType": "image/jpeg",
         "size": "1000", "md5Checksum": f"casey_md5_{i}"}
        for i in range(4)
    ],
}
for _i in range(4):
    drive.file_meta[f"casey_{_i}"] = {"parents": ["CaseyOwnFolder_DEF456"], "mimeType": "image/jpeg"}


class CropFakeFilesResource(FakeFilesResource):
    """Adds what the V18 crop endpoint needs on top of the sync-era fake."""

    def get(self, fileId=None, fields=None, **kw):
        if fileId in self.drive.file_meta:
            meta = dict(self.drive.file_meta[fileId])
            meta["id"] = fileId
            return FakeRequest(lambda: meta)
        return super().get(fileId=fileId, fields=fields, **kw)

    def list(self, q=None, **kw):
        if q and f"name = '{ROOT_REMOVED_NAME}'" in q:
            root_id = q.split("'")[1]
            fid = self.drive.removed_folders.get(root_id)
            return FakeRequest(lambda: {"files": [{"id": fid}] if fid else []})
        return super().list(q=q, **kw)

    def create(self, body=None, media_body=None, fields=None, **kw):
        d = self.drive
        def run():
            d.id_counter += 1
            parents = (body or {}).get("parents", [])
            if (body or {}).get("mimeType") == "application/vnd.google-apps.folder":
                new_id = f"removed_folder_{d.id_counter}"
                if parents:
                    d.removed_folders[parents[0]] = new_id
                return {"id": new_id}
            new_id = f"cropped_{d.id_counter}"
            data = media_body.fh.getvalue()
            md5 = hashlib.md5(data).hexdigest()
            d.uploaded[new_id] = data
            d.file_meta[new_id] = {"parents": parents, "mimeType": "image/jpeg"}
            for folder_id in parents:
                folder = d.folders.get(folder_id)
                if folder is not None:
                    folder["files"].append({
                        "id": new_id, "name": (body or {}).get("name", "cropped.jpg"),
                        "mimeType": "image/jpeg", "size": str(len(data)), "md5Checksum": md5,
                    })
            return {"id": new_id, "md5Checksum": md5}
        return FakeRequest(run)

    def update(self, fileId=None, addParents=None, removeParents=None, fields=None, **kw):
        d = self.drive
        def run():
            for parent in (removeParents or "").split(","):
                folder = d.folders.get(parent)
                if folder is not None:
                    folder["files"] = [f for f in folder["files"] if f["id"] != fileId]
            if fileId in d.file_meta:
                d.file_meta[fileId]["parents"] = [addParents] if addParents else []
            return {"id": fileId}
        return FakeRequest(run)

    def delete(self, fileId=None, **kw):
        d = self.drive
        def run():
            d.uploaded.pop(fileId, None)
            d.file_meta.pop(fileId, None)
            for folder in d.folders.values():
                folder["files"] = [f for f in folder["files"] if f["id"] != fileId]
            return {}
        return FakeRequest(run)

    def get_media(self, fileId=None):
        d = self.drive
        req = FakeRequest(lambda: None)
        if fileId in d.uploaded:
            req.data = d.uploaded[fileId]
        elif fileId and (fileId.startswith("demo_") or fileId.startswith("casey_")):
            req.data = _demo_image(int(fileId.rsplit("_", 1)[1]))
        else:
            req.data = d.jpeg_bytes
        return req


drive.files = lambda: CropFakeFilesResource(drive)


class FakeMediaUpload:
    """Stands in for googleapiclient's MediaIoBaseUpload."""
    def __init__(self, fh, mimetype=None, **kw):
        self.fh = fh
        self.mimetype = mimetype


mod.get_drive_service = lambda: drive
mod.MediaIoBaseDownload = FakeDownloader
mod.MediaIoBaseUpload = FakeMediaUpload
print("Fake Drive active:")
print("  shareable folder ID:  SharedDemoFolder_ABC123  (connects + syncs 8 images)")
print("  unshared folder ID:   UnsharedDemoFolder_XYZ789 (shows the share-first error)")
print("  demo images: 0/5 letterbox · 1 pillarbox · 2/6 IG chrome · 4 dark-gray bars · 3/7 full-bleed")

# Seed an admin + one friend account with credentials printed below so the
# browser check can log straight in without going through /register by hand.
admin = mod.app.test_client()
admin.post("/api/setup", json={"email": "ryan@test.com", "password": "adminpass123"})
code = admin.post("/api/admin/invite-codes").get_json()["code"]
friend = mod.app.test_client()
friend.post("/api/auth/register", json={"invite_code": code, "username": "casey", "email": "casey@test.com", "password": "friendpass1"})

# V18: pre-sync the admin's library with the 8 demo images so the crop
# workflow is one click away on boot — no manual connect+sync needed.
import time
r = admin.post("/api/sync/connect-folder", json={"folder": "SharedDemoFolder_ABC123"})
assert r.status_code == 200, f"connect-folder failed: {r.get_json()}"
r = admin.post("/api/sync/start")
assert r.status_code == 200, f"sync start failed: {r.get_json()}"
for _ in range(120):
    time.sleep(0.25)
    if not admin.get("/api/sync/status").get_json().get("in_progress"):
        break
count = admin.get("/api/sync/status").get_json()
print(f"Admin library pre-synced: {count.get('total_images', '?')} images")

port = int(os.environ.get("PORT", 8080))
print(f"Admin login:  ryan@test.com / adminpass123")
print(f"Friend login: casey / friendpass1")
print(f"DB: {db_path}")
print(f"Starting server on http://localhost:{port} ...")

mod.app.run(port=port, debug=False)

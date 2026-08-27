"""
Frame Atlas — local test for the V25 web-clipping endpoint (POST /api/clip)
used by the browser extension.

Drive is faked, so nothing leaves the machine. Covers the happy path,
duplicate detection, every rejection branch, the X-FA-Session header the
extension authenticates with, and a regression check that /api/upload still
behaves after being refactored onto the shared _ingest_image() helper.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_v25_clip_locally.py
"""

import base64
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))


# ── fake Drive (just enough for files().create()) ──────────────────────────
class FakeRequest:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeFiles:
    def __init__(self, drive):
        self.drive = drive

    def create(self, body=None, media_body=None, fields=None):
        def run():
            if self.drive.fail:
                raise Exception("drive exploded")
            self.drive.created.append(body)
            fid = f"drive-file-{len(self.drive.created)}"
            return {"id": fid, "md5Checksum": f"md5-{len(self.drive.created)}"}
        return FakeRequest(run)


class FakeDrive:
    def __init__(self):
        self.created = []
        self.fail = False

    def files(self):
        return FakeFiles(self)


def jpeg_bytes(mod, seed=1, size=(240, 160)):
    """A JPEG with real structure in it.

    Flat colour fields are useless here: a perceptual hash keys off structure,
    so two solid rectangles fingerprint almost identically no matter their
    colour and every image after the first reads as a near-duplicate. Each
    seed draws a distinct arrangement of shapes instead.
    """
    import random
    from PIL import ImageDraw

    rnd = random.Random(seed)
    img = mod.Image.new("RGB", size, (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    draw = ImageDraw.Draw(img)
    for _ in range(14):
        x0, y0 = rnd.randrange(size[0]), rnd.randrange(size[1])
        x1, y1 = x0 + rnd.randrange(20, 110), y0 + rnd.randrange(20, 90)
        fill = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
        (draw.ellipse if rnd.random() < 0.5 else draw.rectangle)([x0, y0, x1, y1], fill=fill)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def data_url(raw, mime="image/jpeg"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_clip_test_")
    db_path = os.path.join(workdir, "library.db")

    os.environ["FA_DB_PATH"] = db_path

    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy-client-id")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")

    spec = importlib.util.spec_from_file_location("fa_clip_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_clip_app"] = mod
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True

    drive = FakeDrive()
    mod.drive.get_user_drive_service = lambda uid: drive
    mod.drive.get_root_folder_id = lambda uid: "folder-root"
    mod.tagging.trigger_tagging = lambda *a, **k: None      # no Gemini calls in tests
    print("App imported OK (Drive faked).")

    failures = []

    def check(label, cond, detail=""):
        if cond:
            print(f"{label} — OK")
        else:
            print(f"{label} — FAIL  {detail}")
            failures.append(label)

    # ── accounts ───────────────────────────────────────────────────────────
    admin = mod.app.test_client()
    admin.post("/api/setup", json={"email": "ryan@test.com", "password": "adminpass123"})
    code = admin.post("/api/admin/invite-codes").get_json()["code"]

    friend = mod.app.test_client()
    friend.post("/api/auth/register", json={
        "invite_code": code, "username": "alex",
        "email": "alex@test.com", "password": "friendpass1",
    })

    red = jpeg_bytes(mod, seed=1)
    blue = jpeg_bytes(mod, seed=2)

    # ── 1. happy path ──────────────────────────────────────────────────────
    r = admin.post("/api/clip", json={
        "image": data_url(red),
        "source_url": "https://example.com/stills/the-shot.jpg?w=1200",
    })
    body = r.get_json()
    check("Clipping an image succeeds", r.status_code == 200 and body.get("status") == "clipped", body)
    clipped_id = body.get("image_id")

    check("Image was written to Drive", len(drive.created) == 1, drive.created)
    check(
        "Filename comes from the source URL, query string stripped",
        body.get("filename") == "the-shot.jpg",
        body.get("filename"),
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT filename, source_url, thumbnail_blob, phash, aspect_ratio, tagging_status, user_id "
        "FROM images WHERE id = ?", (clipped_id,)
    ).fetchone()
    palette_n = conn.execute(
        "SELECT COUNT(*) n FROM colors WHERE image_id = ?", (clipped_id,)
    ).fetchone()["n"]
    conn.close()

    check("Source URL is recorded", row["source_url"] == "https://example.com/stills/the-shot.jpg?w=1200", row["source_url"])
    check("Thumbnail was generated", bool(row["thumbnail_blob"]))
    check("Fingerprint was computed", bool(row["phash"]))
    check("Aspect ratio was detected", bool(row["aspect_ratio"]), row["aspect_ratio"])
    check("Queued for AI tagging", row["tagging_status"] == "pending", row["tagging_status"])
    check("Palette was extracted", palette_n > 0, f"{palette_n} colours")
    check("Owned by the admin library", row["user_id"] == 1, row["user_id"])

    # ── 2. duplicate detection ─────────────────────────────────────────────
    r = admin.post("/api/clip", json={"image": data_url(red), "source_url": "https://other.com/copy.jpg"})
    body = r.get_json()
    check("Clipping the same image again is caught", body.get("status") == "duplicate", body)
    check("Duplicate names the image already held", body.get("existing", {}).get("id") == clipped_id, body)
    check("Duplicate did NOT touch Drive", len(drive.created) == 1, drive.created)

    r = admin.post("/api/clip", json={"image": data_url(red), "force": True})
    check("force=true overrides the duplicate check", r.get_json().get("status") == "clipped", r.get_json())
    check("Forced clip did reach Drive", len(drive.created) == 2)

    # a genuinely different image is not a duplicate
    r = admin.post("/api/clip", json={"image": data_url(blue), "source_url": "https://example.com/blue.png"})
    check("A different image clips normally", r.get_json().get("status") == "clipped", r.get_json())

    # ── 3. rejections ──────────────────────────────────────────────────────
    r = admin.post("/api/clip", json={})
    check("Missing image is rejected", r.status_code == 400 and r.get_json().get("error") == "no_image", r.get_json())

    r = admin.post("/api/clip", json={"image": "https://example.com/not-a-data-url.jpg"})
    check("A bare URL is rejected", r.status_code == 400 and r.get_json().get("error") == "bad_image", r.get_json())

    r = admin.post("/api/clip", json={"image": "data:image/jpeg;base64,!!!not base64!!!"})
    check("Corrupt base64 is rejected", r.status_code == 400 and r.get_json().get("error") == "bad_image", r.get_json())

    r = admin.post("/api/clip", json={"image": data_url(b"this is not an image at all")})
    check("Non-image bytes are rejected", r.status_code == 400 and r.get_json().get("error") == "bad_image", r.get_json())

    r = admin.post("/api/clip", json={"image": data_url(red, "image/svg+xml")})
    check("SVG is rejected as unsupported", r.status_code == 400 and r.get_json().get("error") == "unsupported_type", r.get_json())

    oversize = data_url(b"\xff\xd8\xff" + b"x" * (mod.CLIP_MAX_BYTES + 10))
    r = admin.post("/api/clip", json={"image": oversize})
    check("Oversized clip is rejected with 413", r.status_code == 413, r.status_code)

    before = len(drive.created)
    drive.fail = True
    r = admin.post("/api/clip", json={"image": data_url(jpeg_bytes(mod, seed=3))})
    check("A Drive failure surfaces as an error, not a crash", r.status_code == 500 and r.get_json().get("status") == "error", r.get_json())
    drive.fail = False
    check("Failed clip stored no image row", len(drive.created) == before)

    # ── 4. filename fallback for junk URLs ─────────────────────────────────
    r = admin.post("/api/clip", json={
        "image": data_url(jpeg_bytes(mod, seed=4)),
        "source_url": "https://cdn.example.com/",
    })
    fn = r.get_json().get("filename", "")
    check("A URL with no filename still gets a sane name", fn.startswith("clip-") and fn.endswith(".jpg"), fn)

    r = admin.post("/api/clip", json={
        "image": data_url(jpeg_bytes(mod, seed=5), "image/png"),
        "source_url": "https://ex.com/shot.jpg",
    })
    check("Extension follows the real image type, not the URL", r.get_json().get("filename") == "shot.png", r.get_json())

    # ── 5. auth & personal libraries ────────────────────────────────────────
    anon = mod.app.test_client()
    r = anon.post("/api/clip", json={"image": data_url(red)})
    check("Anonymous clipping is refused", r.status_code == 401, r.status_code)

    # V25 personal clipping: friends clip to their own folders (not admin-only)
    # In this test the friend has a faked drive service, so the clip succeeds
    r = friend.post("/api/clip", json={"image": data_url(blue)})
    check("Friend can clip to their own folder (using faked Drive)",
          r.status_code == 200 and r.get_json().get("status") in ["clipped", "duplicate"],
          r.get_json())

    # The extension can't rely on the cookie riding along cross-origin, so it
    # copies the value into X-FA-Session instead. That has to be equivalent.
    login = mod.app.test_client()
    resp = login.post("/api/auth/login", json={"username": "ryan", "password": "adminpass123"})
    cookie_value = None
    for raw in resp.headers.getlist("Set-Cookie"):
        if raw.startswith("session="):
            cookie_value = raw.split(";")[0].split("=", 1)[1]
    check("Got a session cookie to echo", bool(cookie_value))

    header_client = mod.app.test_client()   # deliberately holds no cookies
    r = header_client.post(
        "/api/clip",
        json={"image": data_url(jpeg_bytes(mod, seed=6))},
        headers={"X-FA-Session": cookie_value},
    )
    check("X-FA-Session header authenticates the extension", r.status_code == 200, r.get_json())

    bad = mod.app.test_client()
    r = bad.post(
        "/api/clip",
        json={"image": data_url(red)},
        headers={"X-FA-Session": "forged.nonsense.value"},
    )
    check("A forged session header is refused", r.status_code == 401, r.status_code)

    tampered = (cookie_value or "")[:-3] + "aaa"
    r = mod.app.test_client().post(
        "/api/clip",
        json={"image": data_url(red)},
        headers={"X-FA-Session": tampered},
    )
    check("A tampered session header is refused", r.status_code == 401, r.status_code)

    # ── 6. regression: /api/upload after the refactor ──────────────────────
    fresh = jpeg_bytes(mod, seed=7)
    r = admin.post(
        "/api/upload",
        data={"files": (io.BytesIO(fresh), "manual-upload.jpg")},
        content_type="multipart/form-data",
    )
    results = (r.get_json() or {}).get("results", [])
    check("Uploads still work after the refactor",
          r.status_code == 200 and results and results[0]["status"] == "uploaded", r.get_json())

    r = admin.post(
        "/api/upload",
        data={"files": (io.BytesIO(fresh), "manual-upload-again.jpg")},
        content_type="multipart/form-data",
    )
    results = (r.get_json() or {}).get("results", [])
    check("Upload duplicate detection still works",
          results and results[0]["status"] == "duplicate", r.get_json())

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    up = conn.execute(
        "SELECT source_url FROM images WHERE filename = 'manual-upload.jpg'"
    ).fetchone()
    conn.close()
    check("Normal uploads carry no source_url", up is not None and up["source_url"] is None, up and up["source_url"])

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED ❌")
        for f in failures:
            print(f"   - {f}")
    else:
        print("ALL V25 CLIP TESTS PASSED ✅")

    shutil.rmtree(workdir, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

"""
Frame Atlas — local test for V90: clipping respects a friend's library cap.

The folder sync has stopped friends at PERSONAL_LIBRARY_CAP (1,000 images)
since V17, but /api/clip never checked it — so the browser extension was a
way around the limit. This pins the fix:

  - a friend under the cap can clip (and that clip can take them TO the cap)
  - a friend AT the cap is refused with 409 + a readable message (the
    extension shows a 409's message; it shows a 403 as "cannot clip")
  - a refused clip writes NOTHING: no Drive file, no database row
  - the admin is exempt, exactly like the folder sync
  - a friend who deletes a photo is back under the cap and can clip again

The cap is patched down to 3 so the test doesn't need 1,000 rows. Drive and
Gemini are faked, so nothing leaves the machine.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_clip_library_cap_locally.py
"""

import base64
import importlib.util
import io
import os
import random
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


class FakeDrive:
    def __init__(self):
        self.created = []

    def files(self):
        drive = self

        class Files:
            def create(self, body=None, media_body=None, fields=None):
                def run():
                    drive.created.append(body)
                    n = len(drive.created)
                    return {"id": f"drive-file-{n}", "md5Checksum": f"md5-{n}"}
                return FakeRequest(run)
        return Files()


def jpeg_bytes(seed):
    """Structured JPEG — flat fills fingerprint alike and would read as dupes."""
    from PIL import Image, ImageDraw
    rnd = random.Random(seed)
    img = Image.new("RGB", (240, 160), (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    draw = ImageDraw.Draw(img)
    for _ in range(14):
        x0, y0 = rnd.randrange(240), rnd.randrange(160)
        fill = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
        (draw.ellipse if rnd.random() < 0.5 else draw.rectangle)(
            [x0, y0, x0 + rnd.randrange(20, 110), y0 + rnd.randrange(20, 90)], fill=fill)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def data_url(raw):
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_clip_cap_")
    db_path = os.path.join(workdir, "library.db")
    os.environ["FA_DB_PATH"] = db_path
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")

    spec = importlib.util.spec_from_file_location("fa_clip_cap_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_clip_cap_app"] = mod
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True

    drive = FakeDrive()
    mod.drive.get_user_drive_service = lambda uid: drive
    mod.drive.get_root_folder_id = lambda uid: f"folder-of-user-{uid}"
    mod.drive.PERSONAL_LIBRARY_CAP = 3
    mod.tagging.trigger_tagging = lambda user_id=None: None

    failures = []

    def check(label, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {label}" + ("" if cond else f"  {detail}"))
        if not cond:
            failures.append(label)

    def count(uid):
        con = sqlite3.connect(db_path)
        try:
            return con.execute("SELECT COUNT(*) FROM images WHERE user_id = ?", (uid,)).fetchone()[0]
        finally:
            con.close()

    admin = mod.app.test_client()
    admin.post("/api/setup", json={"email": "ryan@test.com", "password": "adminpass123"})
    code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    friend.post("/api/auth/register", json={
        "invite_code": code, "username": "alex", "email": "alex@test.com", "password": "friendpass1"})
    con = sqlite3.connect(db_path)
    friend_id = con.execute("SELECT id FROM users WHERE username = 'alex'").fetchone()[0]
    con.close()

    print("1. A friend under the cap can clip, up to the cap")
    for n in range(3):
        r = friend.post("/api/clip", json={"image": data_url(jpeg_bytes(100 + n))}).get_json()
        check(f"clip {n + 1} of 3 succeeds", r.get("status") == "clipped", r)
    check("friend library is now exactly at the cap", count(friend_id) == 3, count(friend_id))

    print("2. At the cap, a clip is refused and writes nothing")
    drive_before = len(drive.created)
    r = friend.post("/api/clip", json={"image": data_url(jpeg_bytes(200))})
    body = r.get_json() or {}
    check("refused with 409 (the extension shows a 409's message)", r.status_code == 409, r.status_code)
    check("error code is library_full", body.get("error") == "library_full", body)
    check("message names the limit", "3-image limit" in (body.get("message") or ""), body)
    check("no Drive file was written", len(drive.created) == drive_before, len(drive.created))
    check("no database row was written", count(friend_id) == 3, count(friend_id))

    print("3. The admin is exempt, exactly like the folder sync")
    for n in range(4):
        r = admin.post("/api/clip", json={"image": data_url(jpeg_bytes(300 + n))}).get_json()
        check(f"admin clip {n + 1} succeeds past the (patched) cap", r.get("status") == "clipped", r)
    check("admin library went past the cap", count(1) == 4, count(1))

    print("4. Deleting a photo makes room again")
    con = sqlite3.connect(db_path)
    first = con.execute("SELECT id FROM images WHERE user_id = ? ORDER BY id LIMIT 1", (friend_id,)).fetchone()[0]
    con.close()
    r = friend.delete(f"/api/images/{first}")
    check("friend deletes one photo", r.status_code == 200 and count(friend_id) == 2, (r.status_code, count(friend_id)))
    r = friend.post("/api/clip", json={"image": data_url(jpeg_bytes(400))}).get_json()
    check("friend can clip again", r.get("status") == "clipped", r)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("All clip-cap checks passed.")


if __name__ == "__main__":
    main()

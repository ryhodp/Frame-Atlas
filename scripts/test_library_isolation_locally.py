"""
Frame Atlas — local test for V86 (Day 39): one person's library must never
leak into another's.

Personal libraries have been fully isolated since V17, but five code paths
still read across every library. Each check below fails on the pre-V86 code
and passes after it:

  1. /api/tags/suggestions — suggested tag words + counts came from every
     library (a friend with one "lonely, dog" photo was offered the admin's
     "night", "low-key", "car").
  2. /api/search?film= — the exact-vs-substring decision checked every
     library, so another user's exact "Her" gave a friend zero results for
     their own "Her Story".
  3. Duplicate Review (/api/duplicates, /api/duplicates/scan) compared every
     library, so a friend's copy of an admin photo could appear in the
     admin's group, pre-ticked for deletion.
  4. Upload/clip duplicate check compared every library — blocking a photo
     over someone else's copy and showing that person's thumbnail.
  5. A friend's clip was filed under user 1 (the admin), so it appeared in
     the admin's grid and never the friend's, and auto-tagged on the admin's
     key.

Drive and Gemini are faked, so nothing leaves the machine.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_library_isolation_locally.py
"""

import base64
import importlib.util
import io
import os
import random
import sqlite3
import sys
import tempfile
import time

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))


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
            self.drive.created.append(body)
            n = len(self.drive.created)
            return {"id": f"drive-file-{n}", "md5Checksum": f"md5-created-{n}"}
        return FakeRequest(run)


class FakeDrive:
    def __init__(self):
        self.created = []

    def files(self):
        return FakeFiles(self)


def jpeg_bytes(seed, size=(240, 160)):
    """A JPEG with real structure (a flat fill fingerprints like every other
    flat fill, so every image would read as a near-duplicate)."""
    from PIL import Image, ImageDraw
    rnd = random.Random(seed)
    img = Image.new("RGB", size, (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    draw = ImageDraw.Draw(img)
    for _ in range(14):
        x0, y0 = rnd.randrange(size[0]), rnd.randrange(size[1])
        x1, y1 = x0 + rnd.randrange(20, 110), y0 + rnd.randrange(20, 90)
        fill = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
        (draw.ellipse if rnd.random() < 0.5 else draw.rectangle)([x0, y0, x1, y1], fill=fill)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def data_url(raw):
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_isolation_")
    db_path = os.path.join(workdir, "library.db")
    os.environ["FA_DB_PATH"] = db_path
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ["GEMINI_API_KEY"] = "dummy-admin-key"
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")

    spec = importlib.util.spec_from_file_location("fa_isolation_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_isolation_app"] = mod
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True

    drive = FakeDrive()
    mod.drive.get_user_drive_service = lambda uid: drive
    mod.drive.get_root_folder_id = lambda uid: f"folder-of-user-{uid}"
    mod.sync.reconcile_drive_changes = lambda: None
    tag_calls = []
    mod.tagging.trigger_tagging = lambda user_id=None: tag_calls.append(user_id)

    failures = []

    def check(label, cond, detail=""):
        if cond:
            print(f"  ok  {label}")
        else:
            print(f"  FAIL {label}  {detail}")
            failures.append(label)

    def q(sql, *args):
        con = sqlite3.connect(db_path)
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    # ── accounts: admin (user 1) + friend (user 2) ─────────────────────────
    admin = mod.app.test_client()
    admin.post("/api/setup", json={"email": "ryan@test.com", "password": "adminpass123"})
    code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    friend.post("/api/auth/register", json={
        "invite_code": code, "username": "alex", "email": "alex@test.com", "password": "friendpass1"})
    friend_id = q("SELECT id FROM users WHERE username = 'alex'")[0][0]
    check("setup: friend account exists", friend_id != 1)

    # ── seed a small library for each user ────────────────────────────────
    thumb = mod.generate_thumbnail(jpeg_bytes(100))
    con = sqlite3.connect(db_path)
    c = con.cursor()

    def add_image(iid, uid, md5, tags, film=None):
        c.execute("""INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob,
                     aspect_ratio, md5_checksum, phash, date_added)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                  (iid, uid, f"seed-{iid}", f"{iid}.jpg", thumb, "1.78", md5,
                   mod.compute_phash(thumb), f"2026-01-{iid:02d}"))
        for cat, val in tags:
            c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?,?,?,?)",
                      (iid, uid, cat, val))
        if film:
            c.execute("INSERT INTO filmography (image_id, title) VALUES (?, ?)", (iid, film))

    add_image(1, 1, "md5-a", [("mood", "lonely"), ("time_of_day_weather", "night"),
                              ("lighting_quality", "low-key")], film="Her")
    add_image(2, 1, "md5-a", [("mood", "lonely"), ("location_type", "car")])     # admin's own dup of 1
    add_image(3, friend_id, "md5-a", [("mood", "lonely"), ("subjects", "dog")], film="Her Story")
    add_image(4, friend_id, "md5-f", [("mood", "lonely"), ("mood", "warm")])
    con.commit()
    con.close()

    # ── 1. tag suggestions ─────────────────────────────────────────────────
    print("1. Tag suggestions stay inside the caller's library")
    body = friend.post("/api/tags/suggestions", json={"image_ids": [3]}).get_json()
    vals = {s["value"] for s in body["suggestions"]}
    check("friend is not offered the admin's tags", not vals & {"night", "low-key", "car"}, vals)
    check("friend still gets suggestions from their own photos", "warm" in vals, vals)
    body = admin.post("/api/tags/suggestions", json={"image_ids": [2]}).get_json()
    vals = {s["value"] for s in body["suggestions"]}
    check("admin is not offered the friend's tags", not vals & {"dog", "warm"}, vals)
    check("admin still gets suggestions from their own photos", {"night", "low-key"} <= vals, vals)
    counts = {s["value"]: s["count"] for s in body["suggestions"]}
    check("suggestion counts only count the admin's own photos", counts.get("night") == 1, counts)

    # ── 2. film-name search ────────────────────────────────────────────────
    print("2. Film search decides exact-vs-substring from the caller's own credits")
    ids = [i["id"] for i in friend.get("/api/search?film=Her").get_json()["images"]]
    check("friend searching 'Her' finds their own 'Her Story'", ids == [3], ids)
    ids = [i["id"] for i in admin.get("/api/search?film=Her").get_json()["images"]]
    check("admin searching 'Her' still gets an exact match on their own 'Her'", ids == [1], ids)
    ids = [i["id"] for i in admin.get("/api/search?film=Her Story").get_json()["images"]]
    check("admin never sees the friend's 'Her Story'", ids == [], ids)

    # ── 3. Duplicate Review ────────────────────────────────────────────────
    print("3. Duplicate Review compares only the scanning user's photos")
    groups = admin.get("/api/duplicates").get_json()["groups"]
    grouped = [sorted(i["id"] for i in g["images"]) for g in groups]
    check("GET /api/duplicates: admin's own pair is still found", [1, 2] in grouped, grouped)
    check("GET /api/duplicates: friend's copy is never in admin's groups",
          all(3 not in g for g in grouped), grouped)
    r = admin.post("/api/duplicates/scan").get_json()
    check("background scan starts", r.get("started") or r.get("already_running"), r)
    body = {}
    for _ in range(200):
        body = admin.get("/api/duplicates/scan-progress").get_json()
        if body.get("phase") == "done":
            break
        time.sleep(0.05)
    grouped = [sorted(i["id"] for i in g["images"]) for g in (body.get("groups") or [])]
    check("background scan finished cleanly", body.get("phase") == "done" and not body.get("error"), body.get("error"))
    check("background scan: admin's own pair found, friend's copy absent",
          grouped == [[1, 2]], grouped)

    # ── 4 + 5. clip: owner, dedupe scope, tagging handoff ──────────────────
    print("4/5. Clips are filed under, and deduped against, the clipper's own library")
    shot = jpeg_bytes(7)
    r = admin.post("/api/clip", json={"image": data_url(shot)}).get_json()
    admin_clip = r.get("image_id")
    check("admin clip succeeds", r.get("status") == "clipped", r)
    check("admin clip is owned by the admin", q("SELECT user_id FROM images WHERE id = ?", admin_clip) == [(1,)])
    check("admin clip auto-tags on the shared key", tag_calls == [None], tag_calls)

    tag_calls.clear()
    r = friend.post("/api/clip", json={"image": data_url(shot)}).get_json()
    friend_clip = r.get("image_id")
    check("friend clipping the SAME image is not blocked by the admin's copy",
          r.get("status") == "clipped", r)
    check("friend's clip is owned by the friend (not user 1)",
          q("SELECT user_id FROM images WHERE id = ?", friend_clip) == [(friend_id,)])
    check("friend's clip palette rows are owned by the friend",
          {u for (u,) in q("SELECT user_id FROM colors WHERE image_id = ?", friend_clip)} == {friend_id})
    check("friend without a Gemini key: clip does not auto-tag", tag_calls == [], tag_calls)
    ids = [i["id"] for i in friend.get("/api/search").get_json()["images"]]
    check("friend's clip shows in the friend's grid", friend_clip in ids, ids)
    ids = [i["id"] for i in admin.get("/api/search").get_json()["images"]]
    check("friend's clip does NOT show in the admin's grid", friend_clip not in ids, ids)

    r = friend.post("/api/clip", json={"image": data_url(shot)}).get_json()
    check("friend clipping it again IS a duplicate — of their own copy",
          r.get("status") == "duplicate" and r["existing"]["id"] == friend_clip, r)
    r = admin.post("/api/clip", json={"image": data_url(shot)}).get_json()
    check("admin clipping it again is a duplicate of the admin's own copy",
          r.get("status") == "duplicate" and r["existing"]["id"] == admin_clip, r)

    friend.post("/api/account/gemini-key", json={"key": "friend-own-key-1234"})
    tag_calls.clear()
    r = friend.post("/api/clip", json={"image": data_url(jpeg_bytes(8))}).get_json()
    check("friend WITH a key: clip auto-tags on their own library",
          r.get("status") == "clipped" and tag_calls == [friend_id], (r, tag_calls))

    # ── 4b. upload (admin-only, always the admin's library) ────────────────
    print("4b. Upload dedupes against the admin's library only")
    friend_only = jpeg_bytes(9)
    r = friend.post("/api/clip", json={"image": data_url(friend_only)}).get_json()
    check("friend clips a photo the admin doesn't have", r.get("status") == "clipped", r)
    r = admin.post("/api/upload", data={"files": (io.BytesIO(friend_only), "mine.jpg")},
                   content_type="multipart/form-data").get_json()
    results = r.get("results") or []
    check("admin uploading that photo is NOT blocked by the friend's copy",
          len(results) == 1 and results[0].get("status") == "uploaded", r)
    check("uploaded row belongs to the admin",
          results and q("SELECT user_id FROM images WHERE id = ?", results[0].get("image_id")) == [(1,)])

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("All library-isolation checks passed.")


if __name__ == "__main__":
    main()

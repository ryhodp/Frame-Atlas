"""
Frame Atlas — local test for bulk filmography set/clear (Day 14 follow-up).

Same pattern as the other test_*_locally.py scripts (patched DB_PATH, admin
logged in via the real /api/setup flow, then exercises the new endpoints
through Flask's test client) EXCEPT for one thing: now that Day 14's auth
gate is live in production, the old trick of pulling sample images from the
live site's /api/search anonymously no longer works (that route requires a
session now). This script seeds a few synthetic in-memory JPEGs with Pillow
instead — no network dependency, and image content doesn't matter for
testing filmography logic anyway.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_bulk_filmography_locally.py
"""

import importlib.util
import io
import os
import sqlite3
import tempfile

from PIL import Image

REPO = os.path.join(os.path.dirname(__file__), "..")
NUM_IMAGES = 4


def _fake_jpeg(color):
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, format="JPEG")
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_bulkfilm_test_")
    db_path = os.path.join(workdir, "library.db")

    src = open(os.path.join(REPO, "backend", "app.py")).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
    assert patched != src, "Could not find DB_PATH line to patch"
    open(os.path.join(workdir, "app.py"), "w").write(patched)

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    spec = importlib.util.spec_from_file_location("test_app", os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("App imported OK.")

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    ids = []
    colors = [(200, 50, 50), (50, 200, 50), (50, 50, 200), (200, 200, 50)]
    for i, color in enumerate(colors[:NUM_IMAGES]):
        c.execute(
            "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio)"
            " VALUES (1, ?, ?, ?, '4:3')",
            (f"test-{i}", f"synthetic-{i}.jpg", _fake_jpeg(color)),
        )
        ids.append(c.lastrowid)
    # ids[0] already has (wrong) filmography set; ids[1..3] have none.
    c.execute("INSERT INTO filmography (image_id, title, director, dp, year) VALUES (?, ?, ?, ?, ?)",
              (ids[0], "Some Other Film", "Someone Else", "Someone", "1999"))
    conn.commit()
    conn.close()
    print(f"Seeded {len(ids)} synthetic images, ids={ids}")

    admin = mod.app.test_client()
    r = admin.post("/api/setup", json={"email": "test@test.com", "password": "testpass123"})
    assert r.status_code == 200, r.get_json()

    # 1. Validation: missing image_ids, empty fields.
    assert admin.post("/api/filmography/bulk-set", json={"image_ids": []}).status_code == 400
    assert admin.post("/api/filmography/bulk-set", json={"image_ids": ids}).status_code == 400  # no fields at all
    print("1. Bulk-set validation rejects empty image_ids and all-blank fields.")

    # 2. Bulk-set fills in ALL 4 fields on every selected image, including the
    # one with different prior data, when all 4 fields are provided.
    r = admin.post("/api/filmography/bulk-set", json={
        "image_ids": ids + [999999],  # 999999 = invalid, should be reported not crash
        "title": "Interstellar", "director": "Christopher Nolan",
        "dp": "Hoyte van Hoytema", "year": "2014",
    })
    body = r.get_json()
    assert r.status_code == 200 and body["updated"] == len(ids) and body["invalid_ids"] == [999999], body
    search_body = admin.get("/api/search").get_json()
    by_id = {img["id"]: img for img in search_body["images"]}
    for image_id in ids:
        f = by_id[image_id]["filmography"]
        assert f == {"title": "Interstellar", "director": "Christopher Nolan",
                      "dp": "Hoyte van Hoytema", "year": "2014"}, (image_id, f)
    print("2. Bulk-set with all 4 fields overwrote all 4 images (incl. the one with different prior data) and flagged the bad id.")

    # 2b. Bulk-set with ONLY dp provided leaves title/director/year exactly as
    # they were (per-field merge — a blank field means "don't touch it").
    r = admin.post("/api/filmography/bulk-set", json={"image_ids": ids, "dp": "Someone New"})
    body = r.get_json()
    assert r.status_code == 200 and body["updated"] == len(ids) and body["fields_applied"] == {"dp": "Someone New"}, body
    search_body = admin.get("/api/search").get_json()
    by_id = {img["id"]: img for img in search_body["images"]}
    for image_id in ids:
        f = by_id[image_id]["filmography"]
        assert f == {"title": "Interstellar", "director": "Christopher Nolan",
                      "dp": "Someone New", "year": "2014"}, (image_id, f)
    print("2b. Bulk-set with only DP provided changed DP alone — title/director/year untouched on all 4 images.")

    # 2c. common_filmography in selection-summary reflects the now-shared fields.
    r = admin.post("/api/tags/selection-summary", json={"image_ids": ids})
    common = r.get_json()["common_filmography"]
    assert common == {"title": "Interstellar", "director": "Christopher Nolan",
                       "dp": "Someone New", "year": "2014"}, common
    print("2c. selection-summary reports all 4 fields as common across the selection.")

    # 2d. Selection where one image disagrees on `year` -> year drops out of consensus.
    admin.post("/api/filmography/bulk-set", json={"image_ids": [ids[0]], "year": "1999"})
    r = admin.post("/api/tags/selection-summary", json={"image_ids": ids})
    common = r.get_json()["common_filmography"]
    assert common["year"] is None and common["title"] == "Interstellar", common
    print("2d. One image disagreeing on year breaks consensus for that field only.")
    admin.post("/api/filmography/bulk-set", json={"image_ids": [ids[0]], "year": "2014"})  # restore

    # 3. Non-admin cannot bulk-set or bulk-clear.
    friend_code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    friend.post("/api/auth/register", json={"invite_code": friend_code, "username": "casey", "password": "friendpass1"})
    assert friend.post("/api/filmography/bulk-set", json={"image_ids": ids, "title": "x"}).status_code == 403
    assert friend.post("/api/filmography/bulk-clear", json={"image_ids": ids}).status_code == 403
    print("3. Non-admin blocked from both bulk filmography endpoints.")

    # 4. Bulk-clear wipes filmography from all selected.
    r = admin.post("/api/filmography/bulk-clear", json={"image_ids": ids})
    body = r.get_json()
    assert r.status_code == 200 and body["cleared"] == len(ids), body
    search_body = admin.get("/api/search").get_json()
    by_id = {img["id"]: img for img in search_body["images"]}
    for image_id in ids:
        assert by_id[image_id]["filmography"] is None, by_id[image_id]
    print("4. Bulk-clear wiped filmography from all 4 images.")

    print("\nAll bulk filmography checks passed.")


if __name__ == "__main__":
    main()

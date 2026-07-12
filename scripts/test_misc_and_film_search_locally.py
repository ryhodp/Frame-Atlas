"""
Frame Atlas — local test for misc-category tags + filmography search (Day 14
follow-up #3). Synthetic seed images, same pattern as
test_bulk_filmography_locally.py (no live-site dependency).

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_misc_and_film_search_locally.py
"""

import importlib.util
import io
import os
import sqlite3
import tempfile

from PIL import Image

REPO = os.path.join(os.path.dirname(__file__), "..")


def _fake_jpeg(color):
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, format="JPEG")
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_misc_film_test_")
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
    for i, color in enumerate([(200, 50, 50), (50, 200, 50)]):
        c.execute(
            "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio)"
            " VALUES (1, ?, ?, ?, '4:3')",
            (f"test-{i}", f"synthetic-{i}.jpg", _fake_jpeg(color)),
        )
        ids.append(c.lastrowid)
    c.execute("INSERT INTO filmography (image_id, title, director, dp, year) VALUES (?, 'Her', 'Spike Jonze', 'Hoyte van Hoytema', '2013')", (ids[0],))
    c.execute("INSERT INTO filmography (image_id, title, director, dp, year) VALUES (?, 'Interstellar', 'Christopher Nolan', 'Hoyte van Hoytema', '2014')", (ids[1],))
    conn.commit()
    conn.close()
    print(f"Seeded {len(ids)} synthetic images with filmography, ids={ids}")

    admin = mod.app.test_client()
    r = admin.post("/api/setup", json={"email": "test@test.com", "password": "testpass123"})
    assert r.status_code == 200, r.get_json()

    # 1. Single-image tag add with NO category defaults to misc.
    r = admin.post(f"/api/images/{ids[0]}/tags", json={"category": "", "value": "Her"})
    body = r.get_json()
    assert r.status_code == 200 and {"category": "misc", "value": "her"} in body["tags"], body
    print("1. Single-image tag add with blank category landed in misc.")

    # 2. Bulk-apply with no category also defaults to misc.
    r = admin.post("/api/tags/bulk-apply", json={"image_ids": ids, "category": "", "value": "needs-review"})
    assert r.status_code == 200, r.get_json()
    search_body = admin.get("/api/search").get_json()
    for img in search_body["images"]:
        assert {"category": "misc", "value": "needs-review"} in img["tags"], img
    print("2. Bulk-apply with blank category landed in misc on both images.")

    # 3. Bulk-apply with a garbage category string is still rejected.
    r = admin.post("/api/tags/bulk-apply", json={"image_ids": ids, "category": "not_a_real_category", "value": "x"})
    assert r.status_code == 400, r.get_json()
    print("3. Bulk-apply still rejects an invalid (non-misc, non-taxonomy) category.")

    # 4. /api/tag-categories does NOT include misc (stays out of the picker).
    cats = admin.get("/api/tag-categories").get_json()
    assert "misc" not in [c["key"] for c in cats], cats
    print("4. misc does not appear in /api/tag-categories (not pickable on purpose).")

    # 5. Autocomplete finds a film by TITLE prefix ("he" -> Her).
    r = admin.get("/api/autocomplete?q=he")
    body = r.get_json()
    film_matches = [x for x in body if x["type"] == "film"]
    assert any(f["value"] == "Her" and f["field"] == "title" for f in film_matches), body
    print("5. Autocomplete matched film title 'Her' from prefix 'he'.")

    # 6. Autocomplete finds a film by DIRECTOR prefix ("spike" -> Spike Jonze).
    r = admin.get("/api/autocomplete?q=spike")
    body = r.get_json()
    film_matches = [x for x in body if x["type"] == "film"]
    assert any(f["value"] == "Spike Jonze" and f["field"] == "director" for f in film_matches), body
    print("6. Autocomplete matched director 'Spike Jonze' from prefix 'spike'.")

    # 7. A shared DP ("hoyte") returns ONE consolidated match, not two.
    r = admin.get("/api/autocomplete?q=hoyte")
    body = r.get_json()
    film_matches = [x for x in body if x["type"] == "film" and x["field"] == "dp"]
    assert len(film_matches) == 1 and film_matches[0]["count"] == 2, film_matches
    print("7. Shared DP across both films returns one grouped match with count=2.")

    # 8. Regular tag matches are still labeled type='tag' alongside film matches.
    r = admin.get("/api/autocomplete?q=needs")
    body = r.get_json()
    assert any(x["type"] == "tag" and x["value"] == "needs-review" for x in body), body
    print("8. Ordinary tag matches still come back with type='tag'.")

    # 9. Autocomplete only searches this user's own filmography (scoped).
    friend_code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    friend.post("/api/auth/register", json={"invite_code": friend_code, "username": "casey", "password": "friendpass1"})
    r = friend.get("/api/autocomplete?q=he")
    assert r.get_json() == [], r.get_json()
    print("9. A friend with no images of their own gets zero film/tag matches for the same query.")

    print("\nAll misc-category + film-search checks passed.")


if __name__ == "__main__":
    main()

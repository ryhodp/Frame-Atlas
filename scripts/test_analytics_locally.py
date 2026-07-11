"""
Frame Atlas — local test for the Day 13 analytics + utility views backend.

Same trick as test_storyboard_locally.py: boots a patched copy of the server
against a throwaway database, loads a handful of REAL images from the live
site, then exercises /api/analytics, /api/views/*, /api/flags/clear-all,
and confirms the debug endpoints are gone (and /api/models is not).

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_analytics_locally.py
"""

import base64
import importlib.util
import os
import sqlite3
import tempfile

import requests

REPO = os.path.join(os.path.dirname(__file__), "..")
SITE = "https://frame-atlas-production.up.railway.app"
NUM_IMAGES = 5


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_analytics_test_")
    db_path = os.path.join(workdir, "library.db")

    src = open(os.path.join(REPO, "backend", "app.py")).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
    assert patched != src, "Could not find DB_PATH line to patch"
    open(os.path.join(workdir, "app.py"), "w").write(patched)

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")

    spec = importlib.util.spec_from_file_location("test_app", os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("App imported OK.")

    data = requests.get(f"{SITE}/api/search?per={NUM_IMAGES}", timeout=120).json()
    live = data["images"][:NUM_IMAGES]
    assert len(live) == NUM_IMAGES, f"Expected {NUM_IMAGES} live images, got {len(live)}"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    for img in live:
        blob = base64.b64decode(img["thumbnail"].split(",", 1)[1])
        c.execute(
            "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, caption, aspect_ratio)"
            " VALUES (?, 1, ?, ?, ?, ?, ?)",
            (img["id"], f"test-{img['id']}", img["filename"], blob,
             img.get("caption"), img.get("aspect_ratio")),
        )
    ids = [img["id"] for img in live]

    # Curate the test state:
    #   ids[0] — favorite, tagged, added 10 days ago (too old for "recent")
    #   ids[1] — favorite + flagged, tagged
    #   ids[2] — flagged
    #   ids[3], ids[4] — plain recent images
    c.execute("UPDATE images SET is_favorite = 1 WHERE id IN (?, ?)", (ids[0], ids[1]))
    c.execute("UPDATE images SET is_flagged = 1 WHERE id IN (?, ?)", (ids[1], ids[2]))
    c.execute("UPDATE images SET date_added = datetime('now', '-10 days') WHERE id = ?", (ids[0],))
    for image_id, cat, val in [
        (ids[0], "mood", "lonely"),
        (ids[0], "source_type", "film-still"),
        (ids[1], "mood", "lonely"),
        (ids[1], "mood", "tense"),
        (ids[1], "location_type", "interior"),
        (ids[1], "time_of_day_weather", "night"),
    ]:
        c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, ?, ?)",
                  (image_id, cat, val))
    conn.commit()
    conn.close()
    print(f"Inserted {len(ids)} real images with curated favorites/flags/tags: {ids}")

    client = mod.app.test_client()

    # 1. Favorites view: exactly the two starred images, full search-shaped dicts
    r = client.get("/api/views/favorites")
    fav = r.get_json()
    assert r.status_code == 200 and fav["total"] == 2, fav
    fav_ids = {img["id"] for img in fav["images"]}
    assert fav_ids == {ids[0], ids[1]}, fav_ids
    sample = fav["images"][0]
    for key in ("thumbnail", "tags", "palette", "filmography", "ar_float", "is_favorite"):
        assert key in sample, f"missing {key} in view payload"
    tagged = next(img for img in fav["images"] if img["id"] == ids[1])
    assert any(t["value"] == "tense" for t in tagged["tags"]), tagged["tags"]
    print("1. /api/views/favorites: right images, same rich payload as /api/search.")

    # 2. Flagged view
    r = client.get("/api/views/flagged")
    flg = r.get_json()
    assert flg["total"] == 2 and {img["id"] for img in flg["images"]} == {ids[1], ids[2]}, flg
    print("2. /api/views/flagged: right images.")

    # 3. Recent view: 7-day window excludes the 10-day-old image; limit works
    r = client.get("/api/views/recent?days=7")
    rec = r.get_json()
    rec_ids = {img["id"] for img in rec["images"]}
    assert rec["total"] == 4 and ids[0] not in rec_ids, (rec["total"], rec_ids)
    r = client.get("/api/views/recent?days=30")
    assert r.get_json()["total"] == 5
    r = client.get("/api/views/recent?days=7&limit=2")
    rec = r.get_json()
    assert len(rec["images"]) == 2 and rec["total"] == 4, "limit caps images, total stays honest"
    print("3. /api/views/recent: 7-day window excludes old image, days/limit params work.")

    # 4. Unknown view 404s; junk params fall back to defaults instead of crashing
    assert client.get("/api/views/nonsense").status_code == 404
    assert client.get("/api/views/recent?days=potato&limit=banana").status_code == 200
    print("4. Unknown view 404s; junk query params don't crash.")

    # 5. Analytics rollups
    a = client.get("/api/analytics").get_json()
    t = a["totals"]
    assert t["images"] == 5 and t["favorites"] == 2 and t["flagged"] == 2, t
    assert t["added_last_7_days"] == 4 and t["tags"] == 6 and t["decks"] == 0, t
    assert t["distinct_tags"] == 5, t  # lonely, tense, film-still, interior, night
    # "lonely" used twice -> top of the mood category
    moods = a["categories"]["mood"]
    assert moods[0] == {"value": "lonely", "count": 2}, moods
    assert a["category_labels"]["mood"] == "Mood" and a["category_colors"]["mood"]
    # Growth: two buckets (10 days ago may share the current month, so just
    # verify the running total ends at 5 and months are ascending
    growth = a["growth"]
    assert growth[-1]["total"] == 5 and sum(g["added"] for g in growth) == 5, growth
    assert [g["month"] for g in growth] == sorted(g["month"] for g in growth)
    print("5. /api/analytics: totals, category counts, and cumulative growth all correct.")

    # 6. Clear-all flags: clears both, second call is a harmless no-op
    r = client.post("/api/flags/clear-all")
    assert r.get_json() == {"success": True, "cleared": 2}, r.get_json()
    assert client.get("/api/views/flagged").get_json()["total"] == 0
    assert client.post("/api/flags/clear-all").get_json()["cleared"] == 0
    # Images were NOT deleted — favorites and library untouched
    assert client.get("/api/views/favorites").get_json()["total"] == 2
    a = client.get("/api/analytics").get_json()
    assert a["totals"]["images"] == 5 and a["totals"]["flagged"] == 0
    print("6. /api/flags/clear-all: unflags everything, deletes nothing, idempotent.")

    # 7. Debug endpoints are gone; /api/models survives (Day 13 decision)
    assert client.get("/api/debug").status_code == 404
    assert client.get("/api/debug/failed-images").status_code == 404
    assert client.get("/api/models").status_code != 404, "/api/models must stay routed"
    print("7. /api/debug* removed; /api/models still routed (Gemini diagnostic).")

    # 8. Search still works after the hydration refactor
    s = client.get("/api/search?per=10").get_json()
    assert s["total"] == 5 and len(s["images"]) == 5
    assert any(t["value"] == "lonely" for img in s["images"] for t in img["tags"])
    s = client.get(f"/api/search?chips=lonely,tense").get_json()
    assert s["total"] == 1 and s["images"][0]["id"] == ids[1], "AND-filter regression"
    print("8. /api/search unaffected by the shared-hydration refactor (incl. AND filters).")

    print("\nAll analytics/utility-view checks passed. ✅")


if __name__ == "__main__":
    main()

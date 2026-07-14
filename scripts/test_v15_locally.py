"""
Frame Atlas — local test for V15: aspect-ratio search + "My Work" role tags.

Boots a patched copy of the server against a throwaway database (same trick
as test_shuffle_locally.py) and exercises:
  - ar_query_labels(): typed ratios, decimals, aliases, substrings
  - /api/autocomplete returning aspect-ratio suggestions with real counts
  - /api/search's new ar= filter (alone, with chips, junk values)
  - ar filter counting as a filter for the V14 shuffle (seed ignored)
  - the my_work category: listed in /api/tag-categories, bulk-appliable,
    searchable as a chip
  - clear_ai_tags(): re-tagging wipes AI tags but never my_work/misc

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_v15_locally.py
"""

import importlib.util
import os
import sqlite3
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")

# id → aspect_ratio string as sync would store it (exact, unrounded).
# Expected bucket in the comment.
TEST_IMAGES = {
    1: "16:9",     # 16:9 exactly
    2: "1920:817", # ~2.35 → 2.39:1 bucket
    3: "1024:429", # ~2.387 → 2.39:1 bucket
    4: "9:16",     # 9:16 vertical
    5: "1:1",      # square
    6: "37:20",    # 1.85 → 1.85:1 bucket
    7: "80:43",    # ~1.860 → 1.85:1 bucket (nearest standard)
    8: "16:9",     # 16:9 again
}


def search_ids(client, query):
    r = client.get(f"/api/search?{query}")
    data = r.get_json()
    assert r.status_code == 200, data
    return [img["id"] for img in data["images"]]


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_v15_test_")
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

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    for i, ar in TEST_IMAGES.items():
        c.execute(
            "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob,"
            " aspect_ratio, date_added) VALUES (?, 1, ?, ?, ?, ?, datetime('now', ?))",
            (i, f"fake-{i}", f"img_{i}.jpg", b"fakeblob", ar, f"-{i} minutes"),
        )
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (1, 1, 'mood', 'tense')")
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (2, 1, 'mood', 'tense')")
    conn.commit()
    conn.close()
    print(f"Inserted {len(TEST_IMAGES)} fake images across aspect buckets.")

    client = mod.app.test_client()
    setup_r = client.post("/api/setup", json={"email": "test@test.com", "password": "testpass123"})
    assert setup_r.status_code == 200, setup_r.get_json()

    # 1. ar_query_labels: pure matching logic
    f = mod.ar_query_labels
    assert f("2.35")[0] == "2.39:1", f("2.35")
    assert f("2.35:1")[0] == "2.39:1"
    assert f("2.39:1")[0] == "2.39:1"
    assert f("9:16")[0] == "9:16"
    assert f("16x9")[0] == "16:9"
    assert f("scope")[0] == "2.39:1"
    assert f("vertical")[0] == "9:16"
    assert f("square")[0] == "1:1"
    assert set(f("16")) == {"16:9", "9:16"}, f("16")  # substring both ways
    # "7" appears in no standard label; if bare integers snapped to the
    # nearest ratio (7.0 → widest bucket), this would wrongly return 2.39:1
    assert f("7") == [], f("7")
    assert f("night") == [], "normal tag queries must produce no AR suggestions"
    print("1. ar_query_labels: ratios, decimals, aliases, substrings all match right.")

    # 2. Autocomplete: AR suggestions carry real counts, empty buckets omitted
    sugg = client.get("/api/autocomplete?q=2.35").get_json()
    ar_sugg = [s for s in sugg if s["type"] == "ar"]
    assert len(ar_sugg) == 1 and ar_sugg[0]["value"] == "2.39:1" and ar_sugg[0]["count"] == 2, ar_sugg
    sugg = client.get("/api/autocomplete?q=2:3").get_json()  # no 2:3 images exist
    assert not [s for s in sugg if s["type"] == "ar"], "empty buckets must not be suggested"
    sugg = client.get("/api/autocomplete?q=9:16").get_json()
    ar_sugg = [s for s in sugg if s["type"] == "ar"]
    assert ar_sugg and ar_sugg[0]["value"] == "9:16" and ar_sugg[0]["count"] == 1
    print("2. /api/autocomplete: AR suggestions with correct counts, no empty buckets.")

    # 3. /api/search ar= filter: right images per bucket
    assert set(search_ids(client, "ar=2.39:1")) == {2, 3}
    assert set(search_ids(client, "ar=16:9")) == {1, 8}
    assert set(search_ids(client, "ar=1.85:1")) == {6, 7}
    assert search_ids(client, "ar=9:16") == [4]
    assert search_ids(client, "ar=4:3") == [], "empty bucket returns nothing"
    assert search_ids(client, "ar=junk") == [], "junk value returns nothing, not everything"
    print("3. /api/search ar=: exact bucket membership for every format.")

    # 4. ar combines with tag chips (AND)
    assert search_ids(client, "ar=2.39:1&chips=tense") == [2]
    print("4. ar + tag chips AND together.")

    # 5. ar counts as a filter: seed must be ignored (stable newest-first order)
    with_seed = search_ids(client, "ar=16:9&seed=abc123")
    assert with_seed == [1, 8], with_seed  # date order, not shuffled
    print("5. Shuffle: ar filter disables the seeded shuffle, as all filters do.")

    # 6. my_work category is exposed and bulk-appliable
    cats = client.get("/api/tag-categories").get_json()
    assert any(cat["key"] == "my_work" and cat["label"] == "My Work" for cat in cats), cats
    r = client.post("/api/tags/bulk-apply",
                    json={"image_ids": [2, 3], "category": "my_work", "value": "gaffed"})
    assert r.status_code == 200, r.get_json()
    assert set(search_ids(client, "chips=gaffed")) == {2, 3}
    print("6. my_work: in category list, bulk-applies, searchable as a chip.")

    # 7. Re-tagging keeps manual tags: clear_ai_tags wipes AI categories only
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (2, 1, 'misc', 'personal note')")
    conn.commit()
    before = c.execute("SELECT category, value FROM tags WHERE image_id = 2").fetchall()
    assert ("mood", "tense") in before and ("my_work", "gaffed") in before
    mod.clear_ai_tags(c, 2)
    conn.commit()
    after = c.execute("SELECT category, value FROM tags WHERE image_id = 2").fetchall()
    conn.close()
    assert ("mood", "tense") not in after, "AI tag should be cleared for re-tagging"
    assert ("my_work", "gaffed") in after, "my_work must survive a re-tag"
    assert ("misc", "personal note") in after, "misc must survive a re-tag"
    print("7. Re-tag safety: AI tags cleared, my_work + misc preserved.")

    print("\nAll 7 checks passed.")


if __name__ == "__main__":
    main()

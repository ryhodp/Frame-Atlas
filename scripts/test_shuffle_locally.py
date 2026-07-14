"""
Frame Atlas — local test for the V14 shuffled home feed + view log.

Same trick as test_analytics_locally.py: boots a patched copy of the server
against a throwaway database, then exercises the new `seed` param on
/api/search and the new POST /api/views/log endpoint. Uses locally generated
fake images (no live-site fetch — the live site is login-gated since Day 14).

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_shuffle_locally.py
"""

import importlib.util
import os
import sqlite3
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
NUM_IMAGES = 40


def fetch_order(client, seed=None, per=NUM_IMAGES, page=0, extra=""):
    params = f"per={per}&page={page}{extra}"
    if seed is not None:
        params += f"&seed={seed}"
    r = client.get(f"/api/search?{params}")
    data = r.get_json()
    assert r.status_code == 200, data
    return [img["id"] for img in data["images"]], data


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_shuffle_test_")
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

    # 40 fake images, each a minute apart so date-DESC order is unambiguous
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    for i in range(1, NUM_IMAGES + 1):
        c.execute(
            "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob,"
            " caption, aspect_ratio, date_added)"
            " VALUES (?, 1, ?, ?, ?, ?, '16:9', datetime('now', ?))",
            (i, f"fake-{i}", f"img_{i}.jpg", b"fakeblob", f"caption {i}", f"-{i} minutes"),
        )
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (1, 1, 'mood', 'lonely')")
    conn.commit()
    conn.close()
    print(f"Inserted {NUM_IMAGES} fake images.")

    client = mod.app.test_client()
    setup_r = client.post("/api/setup", json={"email": "test@test.com", "password": "testpass123"})
    assert setup_r.status_code == 200, setup_r.get_json()

    date_desc = list(range(1, NUM_IMAGES + 1))  # newest first = id 1 first (id N is N minutes old)

    # 1. No seed → existing newest-first behavior untouched
    order, data = fetch_order(client)
    assert order == date_desc, order
    assert data["total"] == NUM_IMAGES
    print("1. No seed: newest-first order preserved, existing behavior intact.")

    # 2. Seed shuffles, and the same seed gives the same order every time
    shuffled, _ = fetch_order(client, seed="visit-A")
    assert sorted(shuffled) == sorted(date_desc), "shuffle must contain every image exactly once"
    assert shuffled != date_desc, "seeded order should differ from date order"
    again, _ = fetch_order(client, seed="visit-A")
    assert again == shuffled, "same seed must reproduce the same order"
    print("2. Seed: full shuffle, deterministic per seed.")

    # 3. Pagination walks one fixed order — no repeats, no gaps
    p0, d0 = fetch_order(client, seed="visit-A", per=15, page=0)
    p1, d1 = fetch_order(client, seed="visit-A", per=15, page=1)
    p2, d2 = fetch_order(client, seed="visit-A", per=15, page=2)
    assert p0 + p1 + p2 == shuffled, "pages must stitch into the full seeded order"
    assert d0["has_more"] and d1["has_more"] and not d2["has_more"]
    print("3. Pagination: pages stitch seamlessly into one seeded order.")

    # 4. A different seed gives a different order
    other, _ = fetch_order(client, seed="visit-B")
    assert other != shuffled, "different seeds should give different orders"
    assert sorted(other) == sorted(date_desc)
    print("4. New seed: fresh order.")

    # 5. Any active filter ignores the seed and stays newest-first
    filtered, _ = fetch_order(client, seed="visit-A", extra="&chips=lonely")
    assert filtered == [1], filtered
    print("5. Filters: seed ignored, normal search ordering kept.")

    # 6. View log: upsert rows, bump seen_count on repeat, reject junk
    r = client.post("/api/views/log", json={"image_ids": [1, 2, 3]})
    assert r.status_code == 200 and r.get_json()["logged"] == 3, r.get_json()
    r = client.post("/api/views/log", json={"image_ids": [2, 3, 4]})
    assert r.get_json()["logged"] == 3
    conn = sqlite3.connect(db_path)
    rows = dict(conn.execute("SELECT image_id, seen_count FROM image_views WHERE user_id = 1").fetchall())
    conn.close()
    assert rows == {1: 1, 2: 2, 3: 2, 4: 1}, rows
    r = client.post("/api/views/log", json={"image_ids": [9999]})
    assert r.get_json()["logged"] == 0, "ids the user doesn't own must be ignored"
    r = client.post("/api/views/log", json={"image_ids": "junk"})
    assert r.status_code == 400
    r = client.post("/api/views/log", json={"image_ids": []})
    assert r.get_json()["logged"] == 0
    print("6. /api/views/log: upserts, counts repeats, ignores foreign/junk ids.")

    # 7. Recently seen images sink below unseen ones in the seeded order
    recently_seen = {1, 2, 3, 4}
    order, _ = fetch_order(client, seed="visit-C")
    positions = {img_id: idx for idx, img_id in enumerate(order)}
    max_unseen = max(positions[i] for i in date_desc if i not in recently_seen)
    min_seen = min(positions[i] for i in recently_seen)
    assert max_unseen < min_seen, "every unseen image must come before every recently-seen one"
    print("7. Recency: seen-this-week images demoted below unseen ones.")

    # 8. Views older than 7 days no longer demote
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE image_views SET last_seen_at = datetime('now', '-10 days') WHERE image_id = 1")
    conn.commit()
    conn.close()
    order, _ = fetch_order(client, seed="visit-C")
    positions = {img_id: idx for idx, img_id in enumerate(order)}
    still_seen = {2, 3, 4}
    assert positions[1] < min(positions[i] for i in still_seen), \
        "a view from 10 days ago should count as fresh again"
    print("8. Recency window: 10-day-old views count as fresh again.")

    print("\nAll 8 checks passed.")


if __name__ == "__main__":
    main()

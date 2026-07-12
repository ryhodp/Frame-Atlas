"""
Frame Atlas — local test for the new "Find Similar" backend (Day 9).

Boots a patched copy of the server on this Mac (pointed at a throwaway
database instead of Railway's), loads a handful of REAL images + tags from
the live site, loads their real CLIP fingerprints from the seed file, and
then calls the new /similar endpoint to prove the whole path works before
we deploy.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_similar_locally.py
"""

import base64
import importlib.util
import os
import shutil
import sqlite3
import sys
import tempfile

import requests

REPO = os.path.join(os.path.dirname(__file__), "..")
SITE = "https://frame-atlas-production.up.railway.app"
NUM_IMAGES = 8


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_test_")
    db_path = os.path.join(workdir, "library.db")

    # 1. Patched copy of the app: only the DB path changes.
    src = open(os.path.join(REPO, "backend", "app.py")).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
    assert patched != src, "Could not find DB_PATH line to patch"
    open(os.path.join(workdir, "app.py"), "w").write(patched)
    shutil.copy(os.path.join(REPO, "backend", "embeddings_seed.json.gz"), workdir)

    # Dummy env vars in case the app reads them at import time.
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")

    # 2. Import the patched app (this runs init_db + the seed loader on an empty DB).
    spec = importlib.util.spec_from_file_location("test_app", os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("App imported OK — routes registered, empty-DB seed load didn't crash.")

    # 3. Pull a few real images (with tags) from the live site and insert them.
    data = requests.get(f"{SITE}/api/search", timeout=120).json()
    live = data["images"][:NUM_IMAGES]
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
        for t in img.get("tags", []):
            c.execute(
                "INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, ?, ?)",
                (img["id"], t["category"], t["value"]),
            )
    # One extra image that has NO fingerprint, to test the 404 path.
    c.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob) VALUES (999999, 1, 'test-nofp', 'no_fingerprint.jpg', ?)",
        (b"\xff\xd8\xff",),
    )
    conn.commit()
    conn.close()
    print(f"Inserted {len(live)} real images (+1 without fingerprint).")

    # 4. Re-run the seed loader now that images exist — vectors should attach.
    mod.load_embeddings_seed()

    # 5. Hit the new endpoint.
    client = mod.app.test_client()
    setup_r = client.post('/api/setup', json={'email': 'test@test.com', 'password': 'testpass123'})
    assert setup_r.status_code == 200, setup_r.get_json()  # Day 14: log in as admin before hitting protected routes
    source_id = live[0]["id"]

    r = client.get(f"/api/images/{source_id}/similar?limit=5")
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.get_json()}"
    body = r.get_json()
    assert body["source"]["id"] == source_id
    results = body["images"]
    assert 1 <= len(results) <= 5, f"unexpected result count {len(results)}"
    sims = [x["similarity"] for x in results]
    assert sims == sorted(sims, reverse=True), "results not sorted by similarity"
    assert all(x["id"] != source_id for x in results), "source image leaked into results"
    for field in ("thumbnail", "filename", "tags", "similarity"):
        assert field in results[0], f"missing field {field}"
    print(f"/similar OK — source '{body['source']['filename']}' → "
          + ", ".join(f"{x['filename']} ({x['similarity']:.3f})" for x in results))

    r = client.get("/api/images/999999/similar")
    assert r.status_code == 404 and r.get_json().get("error") == "no_embedding", \
        f"expected 404 no_embedding, got {r.status_code}: {r.get_json()}"
    print("404 no_embedding path OK (unfingerprinted image handled gracefully).")

    r = client.get("/api/images/123456789/similar")
    assert r.status_code == 404, f"expected 404 for missing image, got {r.status_code}"
    print("404 missing-image path OK.")

    shutil.rmtree(workdir)
    print("\nALL LOCAL TESTS PASSED ✅")


if __name__ == "__main__":
    sys.exit(main())

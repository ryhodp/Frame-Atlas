"""
Frame Atlas — local test for the new "Find Similar" backend (Day 9).

Boots the server on this Mac (pointed at a throwaway
database instead of Railway's), seeds images carrying REAL CLIP fingerprints
taken from embeddings_seed.json.gz, and calls the /similar endpoint to prove
the whole path works before we deploy.

The image ids come from the seed file rather than the live site: the app has
been login-gated since Day 14, so an unauthenticated fetch of real images
fails. The vectors being real is what makes the ranking meaningful; the
thumbnails themselves are synthetic and don't affect the result.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_similar_locally.py
"""

import gzip
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
NUM_IMAGES = 8


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_test_")
    db_path = os.path.join(workdir, "library.db")

    # 1. Point the app at a throwaway DB via the env var, nothing else changes.
    os.environ["FA_DB_PATH"] = db_path
    shutil.copy(os.path.join(REPO, "backend", "embeddings_seed.json.gz"), workdir)

    # Dummy env vars in case the app reads them at import time.
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")

    # 2. Import the app (this runs init_db + the seed loader on an empty DB).
    spec = importlib.util.spec_from_file_location("test_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("App imported OK — routes registered, empty-DB seed load didn't crash.")

    # 3. Seed images whose ids come from the fingerprint file itself.
    #    The whole app has been login-gated since Day 14, so the original trick
    #    of pulling real images from the live site fails with a bare request.
    #    Borrowing the ids from embeddings_seed.json.gz keeps what actually
    #    matters here — real CLIP vectors, so the ranking under test is real —
    #    and only the pixels are synthetic.
    seed = json.load(gzip.open(os.path.join(REPO, "backend", "embeddings_seed.json.gz")))
    seed_ids = [int(i) for i in sorted(seed["vectors"], key=int)[:NUM_IMAGES]]
    assert len(seed_ids) == NUM_IMAGES, f"seed file only has {len(seed_ids)} vectors"

    def fake_thumbnail(n):
        img = mod.Image.new("RGB", (120, 68), (20 + n * 25 % 200, 60, 140))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    # Two shared tags across the first few images so the 30% tag-overlap half
    # of the similarity score has something to bite on.
    tag_sets = [
        [("mood", "tense"), ("time_of_day", "night")],
        [("mood", "tense"), ("time_of_day", "night")],
        [("mood", "tense")],
    ]

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    for n, img_id in enumerate(seed_ids):
        c.execute(
            "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, caption, aspect_ratio)"
            " VALUES (?, 1, ?, ?, ?, ?, ?)",
            (img_id, f"test-{img_id}", f"img_{img_id}.jpg", fake_thumbnail(n),
             f"synthetic test image {n}", "16:9"),
        )
        for cat, val in (tag_sets[n] if n < len(tag_sets) else []):
            c.execute(
                "INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, ?, ?)",
                (img_id, cat, val),
            )
    live = [{"id": i} for i in seed_ids]
    # One extra image that has NO fingerprint, to test the 404 path.
    c.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob) VALUES (999999, 1, 'test-nofp', 'no_fingerprint.jpg', ?)",
        (b"\xff\xd8\xff",),
    )
    conn.commit()
    conn.close()
    print(f"Inserted {len(live)} images with real fingerprints (+1 without).")

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

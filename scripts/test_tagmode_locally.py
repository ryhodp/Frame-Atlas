"""
Frame Atlas — local test for the Day 10 bulk tagging backend.

Same trick as test_similar_locally.py: boots a patched copy of the server
against a throwaway database, seeds a small synthetic library with a designed
tag co-occurrence structure, then exercises the five tag-mode endpoints end
to end.

Originally this pulled real images from the live site; that stopped working
when Day 14 login-gated the app. Hand-built tags turned out to suit this test
better anyway — the expected suggestions are exact instead of depending on
whatever the library happens to hold.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_tagmode_locally.py
"""

import importlib.util
import io
import os
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
NUM_IMAGES = 10


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_tagmode_test_")
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

    # Synthetic library with a DESIGNED co-occurrence structure. The app has
    # been login-gated since Day 14, so the original unauthenticated fetch of
    # real images fails — and for this test hand-built tags are actually
    # better, because the expected suggestions become exact rather than
    # "whatever the library happens to contain today".
    #
    #   ids 1-10  the selection: all tense, 6 also night, 3 also interior
    #   ids 11-20 rest of library: tense + low-key + desaturated
    #
    # So "tense" sits on every selected image (must NOT be suggested), while
    # "low-key"/"desaturated" co-occur with it library-wide (should surface).
    def fake_thumbnail(n):
        img = mod.Image.new("RGB", (120, 68), (30, 40 + (n * 7) % 120, 90))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    for n in range(1, 21):
        c.execute(
            "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, caption, aspect_ratio)"
            " VALUES (?, 1, ?, ?, ?, ?, '16:9')",
            (n, f"test-{n}", f"img_{n}.jpg", fake_thumbnail(n), f"synthetic image {n}"),
        )
        tags = [("mood", "tense")]
        if n <= NUM_IMAGES:
            if n <= 6:
                tags.append(("time_of_day", "night"))
            if n <= 3:
                tags.append(("location", "interior"))
        else:
            tags += [("lighting_style", "low-key"), ("color_palette", "desaturated")]
        for cat, val in tags:
            c.execute(
                "INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, ?, ?)",
                (n, cat, val),
            )
    conn.commit()
    conn.close()
    ids = list(range(1, NUM_IMAGES + 1))
    print(f"Inserted 20 synthetic images; selection = {ids}")

    client = mod.app.test_client()
    setup_r = client.post('/api/setup', json={'email': 'test@test.com', 'password': 'testpass123'})
    assert setup_r.status_code == 200, setup_r.get_json()  # Day 14: log in as admin before hitting protected routes

    # 1. Category list
    r = client.get("/api/tag-categories")
    assert r.status_code == 200, r.get_json()
    cats = r.get_json()
    # Assert against the source of truth, not a number frozen on Day 10 —
    # this used to say 15 and silently went stale when my_work was added.
    assert len(cats) == len(mod.CAT_LABELS), \
        f"endpoint returned {len(cats)} categories, CAT_LABELS has {len(mod.CAT_LABELS)}"
    assert all({"key", "label", "color"} <= set(c) for c in cats)
    assert {c["key"] for c in cats} == set(mod.CAT_LABELS)
    print(f"/api/tag-categories OK — {len(cats)} categories.")

    # 2. Selection summary on the real tags
    r = client.post("/api/tags/selection-summary", json={"image_ids": ids})
    assert r.status_code == 200, r.get_json()
    summary = r.get_json()
    assert summary["total"] == len(ids)
    assert len(summary["tags"]) > 0, "expected some real tags in the selection"
    counts = [t["count"] for t in summary["tags"]]
    assert counts == sorted(counts, reverse=True), "not sorted by count desc"
    print(f"/api/tags/selection-summary OK — {len(summary['tags'])} distinct tags, "
          f"top: {summary['tags'][0]['value']} ({summary['tags'][0]['count']}/{summary['total']})")

    # 3. Suggestions based on real co-occurrence
    r = client.post("/api/tags/suggestions", json={"image_ids": ids})
    assert r.status_code == 200, r.get_json()
    suggestions = r.get_json()["suggestions"]
    assert len(suggestions) <= 12
    selected_values = {t["value"] for t in summary["tags"] if t["count"] >= summary["total"]}
    assert not any(s["value"] in selected_values for s in suggestions), \
        "a fully-applied tag leaked into suggestions"
    print(f"/api/tags/suggestions OK — {len(suggestions)} suggestions"
          + (f", top: {suggestions[0]['value']} ({suggestions[0]['count']})" if suggestions else ""))

    # 4. Bulk apply a brand-new custom tag to all of them
    r = client.post("/api/tags/bulk-apply", json={
        "image_ids": ids, "category": "mood", "value": "test-custom-tag-xyz"
    })
    assert r.status_code == 200, r.get_json()
    applied = r.get_json()
    assert applied["applied"] == len(ids), applied
    assert applied["already_had"] == 0
    print(f"/api/tags/bulk-apply (new tag) OK — applied to {applied['applied']}, "
          f"already_had {applied['already_had']}.")

    # 5. Applying it again should be a no-op (already_had, not duplicated)
    r = client.post("/api/tags/bulk-apply", json={
        "image_ids": ids, "category": "mood", "value": "test-custom-tag-xyz"
    })
    applied2 = r.get_json()
    assert applied2["applied"] == 0 and applied2["already_had"] == len(ids), applied2
    print("Re-applying the same tag is idempotent (0 new inserts) — OK.")

    # 6. Validation: bad category
    r = client.post("/api/tags/bulk-apply", json={
        "image_ids": ids, "category": "not-a-real-category", "value": "x"
    })
    assert r.status_code == 400, r.get_json()
    print("Invalid category correctly rejected with 400 — OK.")

    # 7. Validation: empty image_ids
    r = client.post("/api/tags/bulk-remove", json={"image_ids": [], "category": "mood", "value": "x"})
    assert r.status_code == 400, r.get_json()
    print("Empty image_ids correctly rejected with 400 — OK.")

    # 8. Invalid image id mixed with valid ones
    r = client.post("/api/tags/bulk-apply", json={
        "image_ids": ids + [999999], "category": "mood", "value": "another-tag"
    })
    applied3 = r.get_json()
    assert applied3["invalid_ids"] == [999999], applied3
    assert applied3["applied"] == len(ids)
    print("Nonexistent image id correctly reported in invalid_ids — OK.")

    # 9. Bulk remove the custom tag
    r = client.post("/api/tags/bulk-remove", json={
        "image_ids": ids, "category": "mood", "value": "test-custom-tag-xyz"
    })
    removed = r.get_json()
    assert removed["removed"] == len(ids), removed
    print(f"/api/tags/bulk-remove OK — removed {removed['removed']}.")

    # 10. Confirm removal took effect
    r = client.post("/api/tags/selection-summary", json={"image_ids": ids})
    summary2 = r.get_json()
    assert not any(t["value"] == "test-custom-tag-xyz" for t in summary2["tags"]), \
        "removed tag still present"
    print("Removal confirmed via selection-summary — OK.")

    shutil.rmtree(workdir)
    print("\nALL LOCAL TAG-MODE TESTS PASSED ✅")


if __name__ == "__main__":
    sys.exit(main())

"""
Frame Atlas — local test for the Day 10 bulk tagging backend.

Same trick as test_similar_locally.py: boots a patched copy of the server
against a throwaway database, loads a handful of REAL images + tags from
the live site, then exercises the five new tag-mode endpoints end to end.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_tagmode_locally.py
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

    data = requests.get(f"{SITE}/api/search?per={NUM_IMAGES}", timeout=120).json()
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
    conn.commit()
    conn.close()
    ids = [img["id"] for img in live]
    print(f"Inserted {len(ids)} real images with their real tags: {ids}")

    client = mod.app.test_client()

    # 1. Category list
    r = client.get("/api/tag-categories")
    assert r.status_code == 200, r.get_json()
    cats = r.get_json()
    assert len(cats) == 15, f"expected 15 categories, got {len(cats)}"
    assert all({"key", "label", "color"} <= set(c) for c in cats)
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

"""
Frame Atlas — local test for the Day 11 decks/scenes backend.

Same trick as test_similar_locally.py / test_tagmode_locally.py: boots a
patched copy of the server against a throwaway database, loads a handful
of REAL images from the live site, then exercises the full deck/scene
lifecycle including the move-vs-copy branching.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_decks_locally.py
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
NUM_IMAGES = 5


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_decks_test_")
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
    conn.commit()
    conn.close()
    ids = [img["id"] for img in live]
    print(f"Inserted {len(ids)} real images: {ids}")

    client = mod.app.test_client()
    setup_r = client.post('/api/setup', json={'email': 'test@test.com', 'password': 'testpass123'})
    assert setup_r.status_code == 200, setup_r.get_json()  # Day 14: log in as admin before hitting protected routes

    # 1. Empty deck list
    r = client.get("/api/decks")
    assert r.status_code == 200 and r.get_json() == [], r.get_json()
    print("Empty deck list OK.")

    # 2. Create a deck
    r = client.post("/api/decks", json={"name": "30 FAD Lookbook"})
    assert r.status_code == 200, r.get_json()
    deck = r.get_json()
    assert deck["name"] == "30 FAD Lookbook" and deck["image_count"] == 0
    deck_id = deck["id"]
    print(f"Created deck {deck_id}.")

    # 3. Rename it
    r = client.patch(f"/api/decks/{deck_id}", json={"name": "30 FAD Lookbook v2"})
    assert r.status_code == 200, r.get_json()
    print("Renamed deck OK.")

    # 4. Empty-name validation
    r = client.patch(f"/api/decks/{deck_id}", json={"name": "  "})
    assert r.status_code == 400, r.get_json()
    print("Empty rename correctly rejected — OK.")

    # 5. Add images to deck (lands in Unsorted)
    r = client.post(f"/api/decks/{deck_id}/images", json={"image_ids": ids + [999999]})
    assert r.status_code == 200, r.get_json()
    add_result = r.get_json()
    assert add_result["added"] == len(ids), add_result
    assert add_result["invalid_ids"] == [999999], add_result
    print(f"Added {add_result['added']} images, correctly flagged invalid id — OK.")

    # 6. Re-adding is deduped (already_in_deck)
    r = client.post(f"/api/decks/{deck_id}/images", json={"image_ids": ids})
    dup_result = r.get_json()
    assert dup_result["added"] == 0 and dup_result["already_in_deck"] == len(ids), dup_result
    print("Re-adding to Unsorted is idempotent — OK.")

    # 7. Deck list now shows the right count + previews
    r = client.get("/api/decks")
    decks = r.get_json()
    assert len(decks) == 1 and decks[0]["image_count"] == len(ids)
    assert len(decks[0]["preview_thumbnails"]) == min(4, len(ids))
    print(f"Deck list shows image_count={decks[0]['image_count']}, "
          f"{len(decks[0]['preview_thumbnails'])} previews — OK.")

    # 8. Deck detail: all images are Unsorted (scene_id null)
    r = client.get(f"/api/decks/{deck_id}")
    detail = r.get_json()
    assert detail["scenes"] == []
    assert len(detail["images"]) == len(ids)
    assert all(img["scene_id"] is None for img in detail["images"])
    deck_image_ids = {img["id"]: img["deck_image_id"] for img in detail["images"]}
    print("Deck detail: all images correctly in Unsorted — OK.")

    # 9. Create two scenes
    r = client.post("/api/scenes", json={"deck_id": deck_id, "name": "Opening"})
    scene_a = r.get_json()
    r = client.post("/api/scenes", json={"deck_id": deck_id, "name": "Climax"})
    scene_b = r.get_json()
    assert scene_a["sort_order"] == 0 and scene_b["sort_order"] == 1
    print(f"Created scenes '{scene_a['name']}' (id={scene_a['id']}) and '{scene_b['name']}' (id={scene_b['id']}).")

    # 10. Move image 0 from Unsorted into scene A (should be a MOVE, not a copy)
    di_id = deck_image_ids[ids[0]]
    r = client.post(f"/api/deck-images/{di_id}/move", json={"target_scene_id": scene_a["id"]})
    move_result = r.get_json()
    assert move_result["action"] == "moved", move_result
    print("Unsorted → Scene A: correctly reported as 'moved' — OK.")

    r = client.get(f"/api/decks/{deck_id}")
    detail2 = r.get_json()
    matching = [img for img in detail2["images"] if img["deck_image_id"] == di_id]
    assert len(matching) == 1 and matching[0]["scene_id"] == scene_a["id"]
    total_rows_for_image0 = len([img for img in detail2["images"] if img["id"] == ids[0]])
    assert total_rows_for_image0 == 1, "moving should not create a duplicate row"
    print("Confirmed: image now only in Scene A, no duplicate left in Unsorted — OK.")

    # 11. Now drag that same image from Scene A into Scene B (should be a COPY)
    r = client.post(f"/api/deck-images/{di_id}/move", json={"target_scene_id": scene_b["id"]})
    copy_result = r.get_json()
    assert copy_result["action"] == "copied", copy_result
    new_di_id = copy_result["new_deck_image_id"]
    print(f"Scene A → Scene B: correctly reported as 'copied' (new id={new_di_id}) — OK.")

    r = client.get(f"/api/decks/{deck_id}")
    detail3 = r.get_json()
    rows_for_image0 = [img for img in detail3["images"] if img["id"] == ids[0]]
    scene_ids_present = sorted(r["scene_id"] for r in rows_for_image0)
    assert scene_ids_present == sorted([scene_a["id"], scene_b["id"]]), rows_for_image0
    print(f"Confirmed: image now appears in BOTH Scene A and Scene B ({len(rows_for_image0)} rows) — OK.")

    # 12. Move the Scene B copy back to Unsorted (should be a move, not touch the Scene A row)
    r = client.post(f"/api/deck-images/{new_di_id}/move", json={"target_scene_id": None})
    back_result = r.get_json()
    assert back_result["action"] == "moved", back_result
    r = client.get(f"/api/decks/{deck_id}")
    detail4 = r.get_json()
    rows_for_image0 = [img for img in detail4["images"] if img["id"] == ids[0]]
    scene_ids_present = set(r["scene_id"] for r in rows_for_image0)
    assert scene_ids_present == {None, scene_a["id"]}, rows_for_image0
    print("Scene B → Unsorted: moved correctly, Scene A row untouched — OK.")

    # 12b. Dropping a photo back onto its own scene must NOT duplicate it
    r = client.post(f"/api/deck-images/{di_id}/move", json={"target_scene_id": scene_a["id"]})
    assert r.get_json()["action"] == "moved", r.get_json()
    r = client.get(f"/api/decks/{deck_id}")
    same_scene_rows = [img for img in r.get_json()["images"]
                       if img["id"] == ids[0] and img["scene_id"] == scene_a["id"]]
    assert len(same_scene_rows) == 1, same_scene_rows
    print("Same-scene drop is a no-op (no accidental duplicate) — OK.")

    # 13. Invalid target scene (belongs to no deck / doesn't exist)
    r = client.post(f"/api/deck-images/{deck_image_ids[ids[1]]}/move", json={"target_scene_id": 999999})
    assert r.status_code == 400, r.get_json()
    print("Invalid target scene correctly rejected with 400 — OK.")

    # 14. Delete one deck_image row directly (Unsorted image 1)
    r = client.delete(f"/api/deck-images/{deck_image_ids[ids[1]]}")
    assert r.status_code == 200, r.get_json()
    r = client.get(f"/api/decks/{deck_id}")
    assert not any(img["deck_image_id"] == deck_image_ids[ids[1]] for img in r.get_json()["images"])
    print("Single deck-image delete OK.")

    # 15. Delete Scene A — should also remove its deck_images row (image 0's Scene A copy),
    #     but leave the Unsorted copy (from step 12) alone.
    r = client.delete(f"/api/scenes/{scene_a['id']}")
    assert r.status_code == 200, r.get_json()
    r = client.get(f"/api/decks/{deck_id}")
    detail5 = r.get_json()
    assert scene_a["id"] not in [s["id"] for s in detail5["scenes"]]
    rows_for_image0 = [img for img in detail5["images"] if img["id"] == ids[0]]
    assert len(rows_for_image0) == 1 and rows_for_image0[0]["scene_id"] is None, rows_for_image0
    print("Scene delete removed its deck_images row, left the Unsorted copy intact — OK.")

    # 16. Delete the whole deck — cascades
    r = client.delete(f"/api/decks/{deck_id}")
    assert r.status_code == 200, r.get_json()
    r = client.get("/api/decks")
    assert r.get_json() == []
    print("Deck delete cascaded cleanly — OK.")

    shutil.rmtree(workdir)
    print("\nALL LOCAL DECK/SCENE TESTS PASSED ✅")


if __name__ == "__main__":
    sys.exit(main())

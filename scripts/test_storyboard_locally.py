"""
Frame Atlas — local test for the Day 12 storyboard/share backend.

Same trick as test_decks_locally.py: boots a patched copy of the server
against a throwaway database, loads a handful of REAL images from the live
site, then exercises storyboard reordering, notes, and share links.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_storyboard_locally.py
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
    workdir = tempfile.mkdtemp(prefix="frame_atlas_storyboard_test_")
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

    # ── Setup: deck with a scene, 3 images in the scene, 2 in Unsorted ────────
    deck_id = client.post("/api/decks", json={"name": "Storyboard Test"}).get_json()["id"]
    scene_id = client.post("/api/scenes", json={"deck_id": deck_id, "name": "Act One"}).get_json()["id"]
    r = client.post(f"/api/decks/{deck_id}/images", json={"image_ids": ids})
    assert r.get_json()["added"] == 5, r.get_json()

    deck = client.get(f"/api/decks/{deck_id}").get_json()
    di = {img["id"]: img["deck_image_id"] for img in deck["images"]}
    # Move the first 3 into the scene
    for image_id in ids[:3]:
        r = client.post(f"/api/deck-images/{di[image_id]}/move", json={"target_scene_id": scene_id})
        assert r.get_json()["action"] == "moved", r.get_json()
    print("Setup OK: deck + scene, 3 frames in scene, 2 in Unsorted.")

    deck = client.get(f"/api/decks/{deck_id}").get_json()
    scene_frames = [img["deck_image_id"] for img in deck["images"] if img["scene_id"] == scene_id]
    unsorted_frames = [img["deck_image_id"] for img in deck["images"] if img["scene_id"] is None]
    assert len(scene_frames) == 3 and len(unsorted_frames) == 2

    # 1. Deck GET includes share_token (null) and storyboard_note field
    assert deck["share_token"] is None
    assert all("storyboard_note" in img for img in deck["images"])
    print("1. Deck payload has share_token + storyboard_note fields.")

    # 2. Reorder the scene: reverse it
    reversed_order = list(reversed(scene_frames))
    r = client.post(f"/api/decks/{deck_id}/reorder",
                    json={"scene_id": scene_id, "deck_image_ids": reversed_order})
    assert r.status_code == 200 and r.get_json()["updated"] == 3, r.get_json()
    deck = client.get(f"/api/decks/{deck_id}").get_json()
    got = [img["deck_image_id"] for img in deck["images"] if img["scene_id"] == scene_id]
    assert got == reversed_order, (got, reversed_order)
    print("2. Scene reorder persists and deck GET returns frames in that order.")

    # 3. Reorder Unsorted (scene_id null)
    r = client.post(f"/api/decks/{deck_id}/reorder",
                    json={"scene_id": None, "deck_image_ids": list(reversed(unsorted_frames))})
    assert r.status_code == 200, r.get_json()
    deck = client.get(f"/api/decks/{deck_id}").get_json()
    got = [img["deck_image_id"] for img in deck["images"] if img["scene_id"] is None]
    assert got == list(reversed(unsorted_frames)), got
    print("3. Unsorted reorder works.")

    # 4. Reorder validation: incomplete list rejected
    r = client.post(f"/api/decks/{deck_id}/reorder",
                    json={"scene_id": scene_id, "deck_image_ids": reversed_order[:2]})
    assert r.status_code == 400, r.get_json()
    # ...ids from the wrong section rejected
    r = client.post(f"/api/decks/{deck_id}/reorder",
                    json={"scene_id": scene_id, "deck_image_ids": unsorted_frames + [reversed_order[0]]})
    assert r.status_code == 400, r.get_json()
    # ...bad payloads rejected
    assert client.post(f"/api/decks/{deck_id}/reorder", json={"scene_id": scene_id, "deck_image_ids": []}).status_code == 400
    assert client.post(f"/api/decks/{deck_id}/reorder", json={"scene_id": scene_id, "deck_image_ids": ["x"]}).status_code == 400
    assert client.post("/api/decks/99999/reorder", json={"scene_id": None, "deck_image_ids": [1]}).status_code == 404
    print("4. Reorder validation: partial lists, wrong-section ids, junk, missing deck all rejected.")

    # 5. Notes: set, read back, clear via empty string
    target = reversed_order[0]
    r = client.post(f"/api/deck-images/{target}/note", json={"note": "  Push in slowly here.  "})
    assert r.status_code == 200 and r.get_json()["note"] == "Push in slowly here.", r.get_json()
    deck = client.get(f"/api/decks/{deck_id}").get_json()
    note = next(img["storyboard_note"] for img in deck["images"] if img["deck_image_id"] == target)
    assert note == "Push in slowly here."
    r = client.post(f"/api/deck-images/{target}/note", json={"note": "   "})
    assert r.get_json()["note"] is None
    assert client.post("/api/deck-images/99999/note", json={"note": "x"}).status_code == 404
    assert client.post(f"/api/deck-images/{target}/note", json={"note": 5}).status_code == 400
    print("5. Notes: set (trimmed), read back, cleared by blank, 404/400 validation.")

    # 6. Scene-to-scene copy carries the note along
    r = client.post(f"/api/deck-images/{target}/note", json={"note": "carried note"})
    scene2_id = client.post("/api/scenes", json={"deck_id": deck_id, "name": "Act Two"}).get_json()["id"]
    r = client.post(f"/api/deck-images/{target}/move", json={"target_scene_id": scene2_id})
    assert r.get_json()["action"] == "copied"
    new_id = r.get_json()["new_deck_image_id"]
    deck = client.get(f"/api/decks/{deck_id}").get_json()
    copied_note = next(img["storyboard_note"] for img in deck["images"] if img["deck_image_id"] == new_id)
    assert copied_note == "carried note"
    print("6. Scene-to-scene copy carries the storyboard note to the new row.")

    # 7. Share link: create, idempotent create, public fetch
    r = client.post(f"/api/decks/{deck_id}/share")
    token = r.get_json()["share_token"]
    assert r.status_code == 200 and token and r.get_json()["share_path"] == f"/share/{token}"
    r2 = client.post(f"/api/decks/{deck_id}/share")
    assert r2.get_json()["share_token"] == token, "second POST should return the SAME token"
    r = client.get(f"/api/share/{token}")
    shared = r.get_json()
    assert r.status_code == 200 and shared["name"] == "Storyboard Test"
    assert len(shared["images"]) == len(deck["images"])
    assert any(img["storyboard_note"] == "carried note" for img in shared["images"])
    print("7. Share: token created, POST idempotent, public GET returns full deck + notes.")

    # 8. Bad token 404s; revoke kills the link; re-create mints a NEW token
    assert client.get("/api/share/not-a-real-token").status_code == 404
    r = client.delete(f"/api/decks/{deck_id}/share")
    assert r.status_code == 200 and r.get_json()["share_token"] is None
    assert client.get(f"/api/share/{token}").status_code == 404
    r = client.post(f"/api/decks/{deck_id}/share")
    assert r.get_json()["share_token"] != token, "revoked token must not be revived"
    assert client.post("/api/decks/99999/share").status_code == 404
    print("8. Share: bad token 404, revoke kills old link, re-share mints a fresh token.")

    print("\nAll storyboard/share checks passed. ✅")


if __name__ == "__main__":
    main()

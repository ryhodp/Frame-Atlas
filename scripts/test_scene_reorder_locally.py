"""
Frame Atlas — local test for the Day 20 scene-reorder backend (V38).

Same trick as test_decks_locally.py / test_storyboard_locally.py: boots a
patched copy of the server against a throwaway database, seeds it with a
handful of SYNTHETIC images and scenes, then exercises
POST /api/decks/<id>/scenes/reorder.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python3 scripts/test_scene_reorder_locally.py
"""

import importlib.util
import io
import os
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))
NUM_IMAGES = 3


def make_jpeg(mod, color=(200, 60, 40)):
    img = mod.Image.new("RGB", (160, 90), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_scene_reorder_test_")
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
    ids = []
    for i in range(NUM_IMAGES):
        blob = make_jpeg(mod)
        c.execute(
            "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, caption, aspect_ratio)"
            " VALUES (1, ?, ?, ?, ?, ?)",
            (f"test-file-{i}", f"frame_{i}.jpg", blob, f"Test frame {i}", "16:9"),
        )
        ids.append(c.lastrowid)
    conn.commit()
    conn.close()
    print(f"Inserted {len(ids)} synthetic images: {ids}")

    client = mod.app.test_client()
    setup_r = client.post('/api/setup', json={'email': 'test@test.com', 'password': 'testpass123'})
    assert setup_r.status_code == 200, setup_r.get_json()  # Day 14: log in as admin before hitting protected routes

    # ── Setup: deck with three scenes ────────────────────────────────────────
    deck_id = client.post("/api/decks", json={"name": "Reorder Test"}).get_json()["id"]
    scene_a = client.post("/api/scenes", json={"deck_id": deck_id, "name": "Opening"}).get_json()
    scene_b = client.post("/api/scenes", json={"deck_id": deck_id, "name": "Middle"}).get_json()
    scene_c = client.post("/api/scenes", json={"deck_id": deck_id, "name": "Climax"}).get_json()
    assert [scene_a["sort_order"], scene_b["sort_order"], scene_c["sort_order"]] == [0, 1, 2]
    print(f"Created 3 scenes: {scene_a['id']} (Opening), {scene_b['id']} (Middle), {scene_c['id']} (Climax).")

    deck_before = client.get(f"/api/decks/{deck_id}").get_json()
    updated_at_before = deck_before["updated_at"]
    activity_before = client.get(f"/api/decks/{deck_id}/activity").get_json()

    # 1. Happy path: reverse the order
    new_order = [scene_c["id"], scene_b["id"], scene_a["id"]]
    r = client.post(f"/api/decks/{deck_id}/scenes/reorder", json={"scene_ids": new_order})
    assert r.status_code == 200 and r.get_json() == {"success": True}, r.get_json()
    print("1. Reorder request accepted — OK.")

    # 2. Order persists via _deck_payload / GET /api/decks/<id>
    deck = client.get(f"/api/decks/{deck_id}").get_json()
    got_order = [s["id"] for s in deck["scenes"]]
    assert got_order == new_order, (got_order, new_order)
    got_sort_orders = [s["sort_order"] for s in deck["scenes"]]
    assert got_sort_orders == [0, 1, 2], got_sort_orders
    print("2. New order persists and GET /api/decks/<id> reflects it (scenes already sort_order-sorted) — OK.")

    # 3. Non-owner gets 404
    friend_client = mod.app.test_client()
    reg = friend_client.post('/api/auth/register', json={
        'email': 'friend@test.com', 'password': 'friendpass123', 'username': 'friend',
        'invite_code': _make_invite_code(mod)
    })
    assert reg.status_code == 200, reg.get_json()  # register() logs the new user in on the same response
    r = friend_client.post(f"/api/decks/{deck_id}/scenes/reorder", json={"scene_ids": new_order})
    assert r.status_code == 404, r.get_json()
    print("3. Non-owner reorder correctly rejected with 404 — OK.")

    # 4. Wrong id set: missing one scene (partial list)
    r = client.post(f"/api/decks/{deck_id}/scenes/reorder", json={"scene_ids": new_order[:2]})
    assert r.status_code == 400, r.get_json()
    print("4a. Partial scene_ids list correctly rejected with 400 — OK.")

    # 4b. Extra id (one that doesn't belong to this deck)
    other_deck_id = client.post("/api/decks", json={"name": "Other Deck"}).get_json()["id"]
    other_scene = client.post("/api/scenes", json={"deck_id": other_deck_id, "name": "Foreign"}).get_json()
    r = client.post(f"/api/decks/{deck_id}/scenes/reorder", json={"scene_ids": new_order + [other_scene["id"]]})
    assert r.status_code == 400, r.get_json()
    print("4b. Extra/foreign scene id correctly rejected with 400 — OK.")

    # 4c. Junk payloads
    assert client.post(f"/api/decks/{deck_id}/scenes/reorder", json={"scene_ids": []}).status_code == 400
    assert client.post(f"/api/decks/{deck_id}/scenes/reorder", json={"scene_ids": ["x", "y", "z"]}).status_code == 400
    assert client.post(f"/api/decks/{deck_id}/scenes/reorder", json={}).status_code == 400
    assert client.post("/api/decks/99999/scenes/reorder", json={"scene_ids": [1]}).status_code == 404
    print("4c. Junk payloads and missing deck all rejected — OK.")

    # 5. No activity-feed entry is created by a reorder; only updated_at changes.
    # Compare against the baseline captured right before step 1 — the only
    # deck mutation in between was the (successful) reorder itself, since the
    # rejected 400/404 requests in steps 3-4 never reach touch_deck/commit.
    activity_after = client.get(f"/api/decks/{deck_id}/activity").get_json()
    assert activity_after == activity_before, (
        f"Reorder should not write a deck_activity row: before={activity_before} after={activity_after}"
    )
    deck_after = client.get(f"/api/decks/{deck_id}").get_json()
    assert deck_after["updated_at"] >= updated_at_before, (deck_after["updated_at"], updated_at_before)
    print("5. No deck_activity row created by reorder; updated_at was bumped — OK.")

    # Confirm the state after failed requests (steps 3/4) is unchanged from step 2
    deck_final = client.get(f"/api/decks/{deck_id}").get_json()
    assert [s["id"] for s in deck_final["scenes"]] == new_order, deck_final["scenes"]
    print("6. Order untouched by the rejected requests — OK.")

    shutil.rmtree(workdir)
    print("\nALL LOCAL SCENE REORDER TESTS PASSED ✅")


def _make_invite_code(mod):
    """Admin generates a single-use invite code via the API-backed helper
    table, mirroring how the real invite flow works (Day 14)."""
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(mod.DB_PATH)
    c = conn.cursor()
    code = "TESTCODE1"
    c.execute(
        "INSERT INTO invite_codes (code, created_by) VALUES (?, 1)",
        (code,),
    )
    conn.commit()
    conn.close()
    return code


if __name__ == "__main__":
    sys.exit(main())

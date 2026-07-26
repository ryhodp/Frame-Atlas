"""
Frame Atlas — local test for the V23 public share-link + permission flow.

The V18 crew harness (test_crew_locally.py) covers invite-by-email and the
/invite-link flow. This one covers the *other* sharing path added in V23:
    POST   /api/decks/<id>/share?permission=viewer|editor
    POST   /api/decks/join/<token>

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_v23_share_link_locally.py
"""

import importlib.util
import io
import os
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")


def make_jpeg(mod, color=(80, 120, 200)):
    img = mod.Image.new("RGB", (160, 90), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_v23_test_")
    db_path = os.path.join(workdir, "library.db")

    src = open(os.path.join(REPO, "backend", "app.py")).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
    assert patched != src, "Could not find DB_PATH line to patch"
    open(os.path.join(workdir, "app.py"), "w").write(patched)

    spec = importlib.util.spec_from_file_location("fa_app", os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_app"] = mod
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True
    print("App imported OK.")

    # --- seed: admin owner + one friend --------------------------------
    owner = mod.app.test_client()
    owner.post("/api/setup", json={"email": "ryan@test.com", "password": "adminpass123"})
    code = owner.post("/api/admin/invite-codes").get_json()["code"]

    friend = mod.app.test_client()
    r = friend.post("/api/auth/register", json={
        "invite_code": code, "username": "alex",
        "email": "alex@test.com", "password": "friendpass1",
    })
    assert r.status_code in (200, 201), r.get_json()
    friend_id = friend.get("/api/auth/me").get_json()["user"]["id"]

    # one image + a deck owned by the admin
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob) VALUES (?,?,?,?)",
        (1, "fid_a", "a.jpg", mod.base64.b64encode(make_jpeg(mod)).decode()),
    )
    conn.commit()
    conn.close()

    deck_id = owner.post("/api/decks", json={"name": "Share Test"}).get_json()["id"]
    print(f"Owner built deck {deck_id}.")

    failures = []

    def check(label, cond, detail=""):
        if cond:
            print(f"{label} — OK")
        else:
            print(f"{label} — FAIL  {detail}")
            failures.append(label)

    # --- 1. share link creation ----------------------------------------
    r = owner.post(f"/api/decks/{deck_id}/share")
    body = r.get_json()
    token = body.get("share_token")
    check("Share link mints a token", r.status_code == 200 and bool(token), body)
    check("Default permission is viewer", body.get("permission") == "viewer", body)

    # idempotent
    r2 = owner.post(f"/api/decks/{deck_id}/share")
    check("Re-sharing returns the same token", r2.get_json().get("share_token") == token)

    # --- 2. share links are view-only, no editor escalation -------------
    # V23 accepted ?permission=editor and echoed it back while storing
    # 'viewer'. The flag is gone now; asking for it must not grant edit
    # rights by any route.
    editor_token = owner.post(
        f"/api/decks/{deck_id}/share?permission=editor"
    ).get_json().get("share_token")
    check("A stray ?permission= param can't escalate", editor_token == token)

    r = friend.post(f"/api/decks/join/{editor_token}")
    check("Friend can join via the link", r.status_code in (200, 201), r.get_json())

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT permission FROM deck_members WHERE deck_id = ? AND user_id = ?",
        (deck_id, friend_id),
    ).fetchone()
    conn.close()
    actual = row["permission"] if row else None
    check("Link joiner is stored as viewer", actual == "viewer", f"got {actual!r}")

    # Behaviourally: a link joiner must not be able to edit the deck.
    r = friend.patch(f"/api/decks/{deck_id}", json={"name": "Renamed By Joiner"})
    check(
        "Link joiner CANNOT rename the deck",
        r.status_code in (403, 404),
        f"got {r.status_code}: {r.get_json()}",
    )
    name_now = owner.get(f"/api/decks/{deck_id}").get_json()["name"]
    check("Deck name survived the attempt", name_now == "Share Test", name_now)

    # ...but they can still view it, which is the whole point of the link.
    r = friend.get(f"/api/decks/{deck_id}")
    check("Link joiner CAN view the deck", r.status_code == 200, r.get_json())

    # --- 2b. updated_at is exposed and maintained -----------------------
    before = owner.get(f"/api/decks/{deck_id}").get_json().get("updated_at")
    check("Deck payload exposes updated_at", bool(before), f"got {before!r}")

    import time as _t
    _t.sleep(1.1)   # sqlite CURRENT_TIMESTAMP is second-resolution
    owner.patch(f"/api/decks/{deck_id}", json={"name": "Share Test 2"})
    after = owner.get(f"/api/decks/{deck_id}").get_json().get("updated_at")
    check("Renaming bumps updated_at", after and after > before, f"{before!r} -> {after!r}")

    _t.sleep(1.1)
    scene_id = owner.post(
        "/api/scenes", json={"deck_id": deck_id, "name": "Opening"}
    ).get_json()["id"]
    after_scene = owner.get(f"/api/decks/{deck_id}").get_json().get("updated_at")
    check(
        "Adding a scene bumps updated_at",
        after_scene and after_scene > after,
        f"{after!r} -> {after_scene!r}",
    )

    # --- 3. revoke ------------------------------------------------------
    owner.delete(f"/api/decks/{deck_id}/share")
    stranger = mod.app.test_client()
    code2 = owner.post("/api/admin/invite-codes").get_json()["code"]
    stranger.post("/api/auth/register", json={
        "invite_code": code2, "username": "sam",
        "email": "sam@test.com", "password": "friendpass2",
    })
    r = stranger.post(f"/api/decks/join/{editor_token}")
    check("Revoked link can no longer be joined", r.status_code == 404, r.get_json())

    r = owner.post(f"/api/decks/{deck_id}/share")
    check("Re-share mints a NEW token", r.get_json().get("share_token") not in (editor_token, None))

    # --- 4. login requirement ------------------------------------------
    anon = mod.app.test_client()
    fresh_token = owner.post(f"/api/decks/{deck_id}/share").get_json()["share_token"]
    r = anon.post(f"/api/decks/join/{fresh_token}")
    check("Anonymous join is refused", r.status_code in (401, 403), f"got {r.status_code}")

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED ❌")
        for f in failures:
            print(f"   - {f}")
    else:
        print("ALL V23 SHARE-LINK TESTS PASSED ✅")

    shutil.rmtree(workdir, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

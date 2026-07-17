"""
Frame Atlas — local test for the Day 14 auth system: one-time admin setup,
login/logout, invite-only registration, admin-only gating, and per-user
scoping of images/decks/favorites/flags.

Same trick as the other test_*_locally.py scripts: boots a patched copy of
the server against a throwaway database, seeds it with SYNTHETIC images
(generated locally with Pillow — the whole app has been login-gated since
Day 14, so the old trick of pulling real images from the live site no
longer works without credentials), then drives the whole auth flow through
Flask's test client. Two SEPARATE test_client() instances stand in for two
different people's browsers (each keeps its own session cookie).

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_auth_locally.py
"""

import importlib.util
import io
import os
import sqlite3
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
NUM_IMAGES = 2


def make_jpeg(mod, color=(200, 60, 40)):
    img = mod.Image.new("RGB", (160, 90), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_auth_test_")
    db_path = os.path.join(workdir, "library.db")

    src = open(os.path.join(REPO, "backend", "app.py")).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
    assert patched != src, "Could not find DB_PATH line to patch"
    open(os.path.join(workdir, "app.py"), "w").write(patched)

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    spec = importlib.util.spec_from_file_location("test_app", os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True
    print("App imported OK.")

    # Seed a couple of synthetic images owned by user 1 (the admin's
    # pre-existing library).
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    image_ids = []
    for i in range(NUM_IMAGES):
        blob = make_jpeg(mod)
        c.execute(
            "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, caption, aspect_ratio)"
            " VALUES (1, ?, ?, ?, ?, ?)",
            (f"test-file-{i}", f"frame_{i}.jpg", blob, f"Test frame {i}", "16:9"),
        )
        image_ids.append(c.lastrowid)
    conn.commit()
    conn.close()
    admin_image_id = image_ids[0]
    print(f"Seeded {len(image_ids)} synthetic images owned by user 1 (admin).")

    anon = mod.app.test_client()

    # 1. Fresh install needs setup; protected routes are locked before login.
    assert anon.get("/api/setup/status").get_json() == {"needs_setup": True}
    assert anon.get("/api/decks").status_code == 401
    assert anon.get("/api/health").status_code == 200  # public route unaffected
    print("1. Fresh install needs setup; protected routes 401 when logged out; /api/health stays public.")

    # 2. Setup creates the admin, attaches the existing library, and logs in.
    admin = mod.app.test_client()
    r = admin.post("/api/setup", json={"email": "ryanhoang415@gmail.com", "password": "correct-horse-1"})
    assert r.status_code == 200, r.get_json()
    me = admin.get("/api/auth/me").get_json()
    assert me == {"logged_in": True, "user": {"id": 1, "username": "ryan", "email": "ryanhoang415@gmail.com", "role": "admin"}}, me
    assert admin.get("/api/setup/status").get_json() == {"needs_setup": False}
    r2 = admin.post("/api/setup", json={"email": "x@x.com", "password": "irrelevant1"})
    assert r2.status_code == 403, "setup must refuse to run twice"
    print("2. /api/setup bootstraps the admin, logs them in, then locks itself forever.")

    # 3. Admin sees their pre-existing library through the now-scoped /api/search.
    r = admin.get("/api/search")
    assert r.get_json()["total"] == NUM_IMAGES, r.get_json()
    print("3. Admin's existing images are attached to the new account (no data migration needed).")

    # 4. Admin-only routes: reachable by admin, not by a plain user (checked below).
    assert admin.get("/api/folders").status_code == 200
    print("4. Admin-only route reachable by the admin.")

    # 5. Invite codes: single-use, admin-only to generate/list/revoke.
    code_resp = admin.post("/api/admin/invite-codes").get_json()
    code = code_resp["code"]
    codes_list = admin.get("/api/admin/invite-codes").get_json()
    assert any(c["code"] == code and c["used_at"] is None for c in codes_list)
    print("5. Invite code generated and listed as unused.")

    # 6. Wrong/used-up invite codes are rejected; a good one creates a fresh,
    #    empty-library account and logs them in.
    friend = mod.app.test_client()
    bad = friend.post("/api/auth/register", json={"invite_code": "not-a-real-code", "username": "casey", "email": "casey@example.com", "password": "friendpass1"})
    assert bad.status_code == 400
    no_email = friend.post("/api/auth/register", json={"invite_code": code, "username": "casey", "password": "friendpass1"})
    assert no_email.status_code == 400, "email must be required"
    r = friend.post("/api/auth/register", json={"invite_code": code, "username": "casey", "email": "casey@example.com", "password": "friendpass1"})
    assert r.status_code == 200, r.get_json()
    friend_me = friend.get("/api/auth/me").get_json()
    assert friend_me["user"]["role"] == "user" and friend_me["user"]["username"] == "casey"
    print("6. Bad invite code rejected; good code registers a new non-admin account and logs them in.")

    # 7. The invite code is now burned — a second signup with it fails, even
    #    for a different username.
    dupe = mod.app.test_client()
    r = dupe.post("/api/auth/register", json={"invite_code": code, "username": "someone-else", "password": "otherpass1"})
    assert r.status_code == 400, "a used invite code must not work twice"
    print("7. Invite code cannot be reused by a second person.")

    # 8. Every user truly starts empty: the friend's own library is empty,
    #    even though the shared images table has the admin's 2 photos in it.
    r = friend.get("/api/search")
    assert r.get_json()["total"] == 0, r.get_json()
    print("8. New account starts with zero images (per-user library, not shared).")

    # 9. Non-admin cannot reach admin-only routes or another user's images.
    assert friend.get("/api/folders").status_code == 403
    assert friend.get("/api/admin/invite-codes").status_code == 403
    assert friend.get(f"/api/images/{admin_image_id}/full").status_code == 404
    assert friend.post(f"/api/images/{admin_image_id}/favorite").status_code == 404
    print("9. Non-admin blocked from admin routes and from touching the admin's images by id.")

    # 10. Favorites/flags are per-user, not a shared on/off switch.
    r = admin.post(f"/api/images/{admin_image_id}/favorite")
    assert r.get_json() == {"success": True, "is_favorite": True}
    assert admin.get("/api/views/favorites").get_json()["total"] == 1
    assert friend.get("/api/views/favorites").get_json()["total"] == 0
    r = admin.post(f"/api/images/{admin_image_id}/favorite")  # toggle back off
    assert r.get_json()["is_favorite"] is False
    print("10. Favoriting is scoped per-user; toggling twice returns to unfavorited.")

    # 11. Decks are private per account.
    admin_deck = admin.post("/api/decks", json={"name": "Admin Deck"}).get_json()
    friend_deck = friend.post("/api/decks", json={"name": "Friend Deck"}).get_json()
    admin_deck_ids = {d["id"] for d in admin.get("/api/decks").get_json()}
    friend_deck_ids = {d["id"] for d in friend.get("/api/decks").get_json()}
    assert admin_deck["id"] in admin_deck_ids and admin_deck["id"] not in friend_deck_ids
    assert friend_deck["id"] in friend_deck_ids and friend_deck["id"] not in admin_deck_ids
    # Cross-account access to a deck by id is a 404, not leaked data.
    assert friend.get(f"/api/decks/{admin_deck['id']}").status_code == 404
    assert friend.delete(f"/api/decks/{admin_deck['id']}").status_code == 404
    print("11. Decks are private per account; cross-account access 404s instead of leaking.")

    # 12. Revoking: an unused code is revocable, a used one is not.
    code2 = admin.post("/api/admin/invite-codes").get_json()
    del_ok = admin.delete(f"/api/admin/invite-codes/{code2['id']}")
    assert del_ok.status_code == 200
    used_code_id = next(c["id"] for c in admin.get("/api/admin/invite-codes").get_json() if c["code"] == code)
    assert admin.delete(f"/api/admin/invite-codes/{used_code_id}").status_code == 400
    print("12. Unused invite code revocable; used one cannot be revoked.")

    # 13. Logout actually ends the session.
    admin.post("/api/auth/logout")
    assert admin.get("/api/auth/me").get_json() == {"logged_in": False}
    assert admin.get("/api/decks").status_code == 401
    print("13. Logout clears the session; protected routes 401 again afterward.")

    # 14. Wrong password is rejected; right password logs back in.
    fresh = mod.app.test_client()
    bad_login = fresh.post("/api/auth/login", json={"username": "ryan", "password": "wrong-password"})
    assert bad_login.status_code == 401
    good_login = fresh.post("/api/auth/login", json={"username": "ryan", "password": "correct-horse-1"})
    assert good_login.status_code == 200 and good_login.get_json()["user"]["role"] == "admin"
    print("14. Wrong password rejected; correct password logs the admin back in.")

    # 15. Forgot/reset password: unknown email 404s; known email issues a
    #     one-time token that actually changes the password and then dies.
    anon2 = mod.app.test_client()
    unknown = anon2.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert unknown.status_code == 404
    got = anon2.post("/api/auth/forgot-password", json={"email": "casey@example.com"})
    assert got.status_code == 200, got.get_json()
    reset_path = got.get_json()["reset_path"]
    token = reset_path.split("token=")[1]
    bad_token = anon2.post("/api/auth/reset-password", json={"token": "not-a-real-token", "password": "whatever12"})
    assert bad_token.status_code == 400
    short_pw = anon2.post("/api/auth/reset-password", json={"token": token, "password": "short"})
    assert short_pw.status_code == 400
    ok = anon2.post("/api/auth/reset-password", json={"token": token, "password": "newfriendpass1"})
    assert ok.status_code == 200, ok.get_json()
    reused = anon2.post("/api/auth/reset-password", json={"token": token, "password": "anotherpass1"})
    assert reused.status_code == 400, "a used reset token must not work twice"
    old_login = anon2.post("/api/auth/login", json={"username": "casey", "password": "friendpass1"})
    assert old_login.status_code == 401, "old password must no longer work"
    new_login = anon2.post("/api/auth/login", json={"username": "casey", "password": "newfriendpass1"})
    assert new_login.status_code == 200, new_login.get_json()
    print("15. Forgot/reset password: unknown email 404s, token is single-use, and it actually changes the password.")

    print("\nAll auth checks passed.")


if __name__ == "__main__":
    main()

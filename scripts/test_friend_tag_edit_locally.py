"""
Frame Atlas — local test for V75: friends editing tags + filmography on their
OWN photos.

Before V75 every tag/filmography edit endpoint was @admin_required, so an
invited friend who tried to fix the tags on their own synced photo got a
silent 403 and nothing saved (the exact bug report that prompted this). V75
makes the single-image endpoints owner-or-admin and scopes the bulk endpoints
to the caller's own library. The library-wide tag-removal preview stays
admin-only.

Same harness as the other test_*_locally.py scripts: a throwaway DB pointed at
by FA_DB_PATH, admin created through the real /api/setup flow, a friend through
a real invite code, then everything exercised via Flask's test client.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_friend_tag_edit_locally.py
"""

import importlib.util
import io
import os
import sys
import sqlite3
import tempfile

from PIL import Image

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, "backend"))

failures = []


def check(label, cond, detail=""):
    mark = "OK" if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def _fake_jpeg(color=(120, 120, 120)):
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, format="JPEG")
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_friend_tag_test_")
    db_path = os.path.join(workdir, "library.db")
    os.environ["FA_DB_PATH"] = db_path
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    spec = importlib.util.spec_from_file_location("test_app_friend_tag", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("App imported OK.\n")

    admin = mod.app.test_client()
    r = admin.post("/api/setup", json={"email": "ryan@test.com", "password": "adminpass123"})
    assert r.status_code == 200, r.get_json()

    friend_code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    reg = friend.post("/api/auth/register", json={
        "invite_code": friend_code, "username": "casey",
        "email": "casey@test.com", "password": "friendpass1"})
    assert reg.status_code == 200, reg.get_json()

    # Find the friend's user_id, then seed one photo for each user directly.
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    friend_uid = c.execute("SELECT id FROM users WHERE username = 'casey'").fetchone()[0]
    assert friend_uid != 1, friend_uid

    def seed(user_id, fn):
        c.execute(
            "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio)"
            " VALUES (?, ?, ?, ?, '4:3')",
            (user_id, f"drive-{fn}", fn, _fake_jpeg()),
        )
        return c.lastrowid

    admin_img = seed(1, "admin_shot.jpg")
    friend_img = seed(friend_uid, "friend_shot.jpg")
    friend_img2 = seed(friend_uid, "friend_shot2.jpg")
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?, ?, 'mood', 'moody')",
              (friend_img, friend_uid))
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?, ?, 'mood', 'moody')",
              (friend_img2, friend_uid))
    conn.commit()
    conn.close()
    print(f"Seeded: admin_img={admin_img} (user 1), friend_img={friend_img}/{friend_img2} (user {friend_uid}).\n")

    # ── 1. Single-image tag editing: the original bug ──────────────────────────
    print("1. Single-image tag editing (the bug report):")
    r = friend.post(f"/api/images/{friend_img}/tags", json={"category": "lighting_quality", "value": "hard light"})
    body = r.get_json()
    check("Friend adds a tag to their OWN photo (was a silent 403 before V75)",
          r.status_code == 200 and any(t["value"] == "hard light" for t in body.get("tags", [])), body)

    r = friend.delete(f"/api/images/{friend_img}/tags", json={"category": "mood", "value": "moody"})
    body = r.get_json()
    check("Friend removes a tag from their own photo",
          r.status_code == 200 and not any(t["value"] == "moody" for t in body.get("tags", [])), body)

    r = friend.post(f"/api/images/{admin_img}/tags", json={"category": "mood", "value": "sneaky"})
    check("Friend is blocked (404) from tagging the ADMIN's photo", r.status_code == 404, r.get_json())

    # ── 2. Single-image filmography ───────────────────────────────────────────
    print("\n2. Single-image filmography:")
    r = friend.post(f"/api/images/{friend_img}/filmography",
                    json={"title": "My Short", "director": "Casey", "dp": "Casey", "year": "2026"})
    body = r.get_json()
    check("Friend sets filmography on their own photo",
          r.status_code == 200 and body.get("filmography", {}).get("title") == "My Short", body)

    r = friend.post(f"/api/images/{admin_img}/filmography", json={"title": "nope"})
    check("Friend is blocked (404) from filmography on the admin's photo", r.status_code == 404, r.get_json())

    # ── 3. Bulk tag endpoints scope to the caller's own library ───────────────
    print("\n3. Bulk tag endpoints (Select Mode):")
    mixed = [friend_img, friend_img2, admin_img]
    r = friend.post("/api/tags/bulk-apply",
                    json={"image_ids": mixed, "category": "genre_aesthetic", "value": "noir"})
    body = r.get_json()
    check("Friend bulk-apply hits only their own 2 photos, admin's id reported invalid",
          r.status_code == 200 and body.get("applied") == 2 and admin_img in body.get("invalid_ids", []), body)

    r = friend.post("/api/tags/bulk-remove",
                    json={"image_ids": mixed, "category": "genre_aesthetic", "value": "noir"})
    body = r.get_json()
    check("Friend bulk-remove clears only their own 2", r.status_code == 200 and body.get("removed") == 2, body)

    # admin's photo never received the tag
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM tags WHERE image_id = ? AND value = 'noir'", (admin_img,)).fetchone()[0]
    conn.close()
    check("The admin's photo was never touched by the friend's bulk calls", n == 0, n)

    r = friend.post("/api/tags/selection-summary", json={"image_ids": mixed})
    body = r.get_json()
    check("selection-summary works for a friend and counts only their own photos",
          r.status_code == 200 and body.get("total") == 2, body)

    r = friend.post("/api/tags/suggestions", json={"image_ids": [friend_img, friend_img2]})
    check("suggestions endpoint is reachable for a friend", r.status_code == 200, r.get_json())

    # ── 4. Bulk filmography scopes too ───────────────────────────────────────
    print("\n4. Bulk filmography:")
    r = friend.post("/api/filmography/bulk-set", json={"image_ids": mixed, "dp": "Casey"})
    body = r.get_json()
    check("Friend bulk-set filmography touches only their own 2, admin id invalid",
          r.status_code == 200 and body.get("updated") == 2 and admin_img in body.get("invalid_ids", []), body)

    r = friend.post("/api/filmography/bulk-clear", json={"image_ids": mixed})
    body = r.get_json()
    check("Friend bulk-clear filmography clears only their own 2",
          r.status_code == 200 and body.get("cleared") == 2, body)

    # ── 5. The library-wide removal preview stays admin-only ─────────────────
    print("\n5. Library-wide cleanup stays admin-only:")
    r = friend.get("/api/tags/removal-preview?value=moody")
    check("Friend is still refused the removal preview (403)", r.status_code == 403, r.get_json())
    r = admin.get("/api/tags/removal-preview?value=moody")
    check("Admin still gets the removal preview", r.status_code == 200, r.get_json())

    # ── 6. Admin path is unchanged — can still edit anyone's photo ───────────
    print("\n6. Admin still edits any photo:")
    r = admin.post(f"/api/images/{friend_img}/tags", json={"category": "misc", "value": "admin-added"})
    check("Admin adds a tag to the friend's photo", r.status_code == 200, r.get_json())

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED ❌")
        for f in failures:
            print(f"   - {f}")
        sys.exit(1)
    print("ALL FRIEND TAG/FILMOGRAPHY EDIT TESTS PASSED ✅")


if __name__ == "__main__":
    main()

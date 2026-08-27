"""
Frame Atlas — local test for Day 25 (V43): cacheable thumbnail URLs.

Before this, every thumbnail was base64 text buried inside a JSON response —
uncacheable, since there's no URL for the browser to remember it by. This
tests that:
  - /api/search, the decks owner view, and /api/images/<id>/similar now
    return a `/api/images/<id>/thumb?v=<checksum>` URL instead of a data URI
  - the public /api/share/<token> view is UNCHANGED — still embedded base64,
    since there's no login there to gate a URL behind (build_image_dict's
    public=True path)
  - GET /api/images/<id>/thumb actually serves the right bytes, with the
    right Cache-Control header, and the same owner-or-admin permission shape
    as the other single-image endpoints (crop, delete, notes)
  - changing an image's md5_checksum (what a crop does) changes the URL that
    comes back, so a stale cached copy naturally gets bypassed

Same trick as test_dp_notes_search_locally.py: boots the server against a throwaway database, seeds synthetic Pillow images, and
drives everything through Flask's test client.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python3 scripts/test_thumbnail_caching_locally.py
"""

import importlib.util
import io
import os
import re
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))

THUMB_URL_RE = re.compile(r'^/api/images/(\d+)/thumb\?v=(.+)$')


def make_jpeg(mod, color=(200, 60, 40)):
    img = mod.Image.new("RGB", (160, 90), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_invite_code(mod, code):
    conn = sqlite3.connect(mod.DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO invite_codes (code, created_by) VALUES (?, 1)", (code,))
    conn.commit()
    conn.close()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_thumb_test_")
    db_path = os.path.join(workdir, "library.db")

    os.environ["FA_DB_PATH"] = db_path

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")

    spec = importlib.util.spec_from_file_location("test_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("App imported OK.")

    conn = sqlite3.connect(mod.DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    blob_a = make_jpeg(mod, color=(200, 60, 40))
    c.execute(
        "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, caption, aspect_ratio, md5_checksum)"
        " VALUES (1, ?, ?, ?, ?, ?, ?)",
        ("test-file-a", "frame_a.jpg", blob_a, "Frame A", "16:9", "checksum-v1"),
    )
    img_a = c.lastrowid
    conn.commit()
    print(f"Inserted admin's image {img_a} with checksum 'checksum-v1'.")

    client = mod.app.test_client()
    setup_r = client.post('/api/setup', json={'email': 'admin@test.com', 'password': 'testpass123'})
    assert setup_r.status_code == 200, setup_r.get_json()

    # ── 1. /api/search returns a thumb URL, not base64 ──────────────────────
    r = client.get('/api/search')
    assert r.status_code == 200, r.get_json()
    images = r.get_json()['images']
    assert len(images) == 1, images
    thumb = images[0]['thumbnail']
    m = THUMB_URL_RE.match(thumb)
    assert m, f"expected a /api/images/<id>/thumb?v=... URL, got: {thumb}"
    assert int(m.group(1)) == img_a
    assert m.group(2) == 'checksum-v1', f"expected ?v= to be the image's md5_checksum, got: {m.group(2)}"
    print("1. /api/search returns a cacheable thumb URL versioned by md5_checksum, not base64 — OK.")

    # ── 2. GET /api/images/<id>/thumb serves the real bytes + cache headers ─
    r = client.get(f'/api/images/{img_a}/thumb?v=checksum-v1')
    assert r.status_code == 200, r.data
    assert r.mimetype == 'image/jpeg', r.mimetype
    assert r.data == blob_a, "served bytes must match the stored thumbnail_blob exactly"
    cc = r.headers.get('Cache-Control', '')
    assert 'immutable' in cc and 'max-age=31536000' in cc and 'private' in cc, cc
    print("2. /api/images/<id>/thumb serves the exact stored bytes with private+immutable Cache-Control — OK.")

    # ── 3. Unknown image id 404s ─────────────────────────────────────────────
    r = client.get('/api/images/999999/thumb')
    assert r.status_code == 404, r.get_json()
    print("3. Unknown image id 404s — OK.")

    # ── 4. Decks (owner view) also return thumb URLs, not base64 ────────────
    r = client.post('/api/decks', json={'name': 'Test Deck'})
    assert r.status_code == 200, r.get_json()
    deck_id = r.get_json()['id']
    r = client.post(f'/api/decks/{deck_id}/images', json={'image_ids': [img_a]})
    assert r.status_code == 200, r.get_json()

    r = client.get(f'/api/decks/{deck_id}')
    assert r.status_code == 200, r.get_json()
    deck_images = r.get_json()['images']
    assert len(deck_images) == 1
    assert THUMB_URL_RE.match(deck_images[0]['thumbnail']), \
        f"deck owner view should return a thumb URL, got: {deck_images[0]['thumbnail']}"
    print("4. Deck owner view (GET /api/decks/<id>) returns a thumb URL too — OK.")

    # ── 5. Public share view is UNCHANGED: still embedded base64 ────────────
    r = client.post(f'/api/decks/{deck_id}/share')
    assert r.status_code == 200, r.get_json()
    token = r.get_json()['share_token']

    r = client.get(f'/api/share/{token}')
    assert r.status_code == 200, r.get_json()
    shared_images = r.get_json()['images']
    assert len(shared_images) == 1
    shared_thumb = shared_images[0]['thumbnail']
    assert shared_thumb.startswith('data:image/jpeg;base64,'), \
        f"public share view must stay embedded base64 (no login to gate a URL behind), got: {shared_thumb[:60]}"
    print("5. Public /api/share/<token> view is unchanged — still embedded base64 — OK.")

    # ── 6. A different logged-in user cannot fetch admin's thumb URL ────────
    _make_invite_code(mod, "FRIENDCODE1")
    friend_client = mod.app.test_client()
    reg = friend_client.post('/api/auth/register', json={
        'email': 'friend@test.com', 'password': 'friendpass123', 'username': 'friend',
        'invite_code': 'FRIENDCODE1'
    })
    assert reg.status_code == 200, reg.get_json()

    r = friend_client.get(f'/api/images/{img_a}/thumb')
    assert r.status_code == 404, r.get_json()
    print("6. A different non-owner, non-admin user is rejected with 404 (same isolation as search) — OK.")

    # ── 7. Admin CAN fetch a friend's thumb (owner-or-admin, same as crop/notes) ─
    friend_uid = c.execute("SELECT id FROM users WHERE username = 'friend'").fetchone()[0]
    blob_f = make_jpeg(mod, color=(20, 60, 200))
    c.execute(
        "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, caption, aspect_ratio, md5_checksum)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (friend_uid, "friend-file-0", "friend_frame_0.jpg", blob_f, "Friend's frame", "16:9", "friend-checksum"),
    )
    friend_img_id = c.lastrowid
    conn.commit()

    r = client.get(f'/api/images/{friend_img_id}/thumb')
    assert r.status_code == 200, r.get_json()
    assert r.data == blob_f
    print("7. Admin can fetch ANY user's thumbnail (owner-or-admin) — OK.")

    # ── 8. Changing md5_checksum (what a crop does) changes the returned URL ─
    c.execute("UPDATE images SET md5_checksum = ? WHERE id = ?", ("checksum-v2-after-crop", img_a))
    conn.commit()
    r = client.get('/api/search')
    new_thumb = [i for i in r.get_json()['images'] if i['id'] == img_a][0]['thumbnail']
    m2 = THUMB_URL_RE.match(new_thumb)
    assert m2 and m2.group(2) == 'checksum-v2-after-crop', \
        f"URL should change when md5_checksum changes (crop simulation), got: {new_thumb}"
    assert new_thumb != thumb, "the URL must actually differ from before, or a cached copy would never refresh"
    print("8. A checksum change (crop) produces a different URL, forcing a fresh fetch — OK.")

    # ── 9. /api/images/<id>/similar also returns a thumb URL (shares build_image_dict) ─
    import array
    vec = array.array('f', [0.1] * 8).tobytes()
    c.execute("INSERT INTO embeddings (image_id, user_id, clip_vector) VALUES (?, 1, ?)", (img_a, vec))
    blob_b = make_jpeg(mod, color=(10, 200, 10))
    c.execute(
        "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, md5_checksum)"
        " VALUES (1, ?, ?, ?, ?, ?)",
        ("test-file-b", "frame_b.jpg", blob_b, "16:9", "checksum-b"),
    )
    img_b = c.lastrowid
    vec_b = array.array('f', [0.11] * 8).tobytes()
    c.execute("INSERT INTO embeddings (image_id, user_id, clip_vector) VALUES (?, 1, ?)", (img_b, vec_b))
    conn.commit()

    r = client.get(f'/api/images/{img_a}/similar')
    assert r.status_code == 200, r.get_json()
    sim_images = r.get_json()['images']
    assert len(sim_images) >= 1
    assert THUMB_URL_RE.match(sim_images[0]['thumbnail']), \
        f"similar-images endpoint should return a thumb URL too, got: {sim_images[0]['thumbnail']}"
    print("9. /api/images/<id>/similar returns a thumb URL too (shares build_image_dict) — OK.")

    conn.close()
    print("\nALL THUMBNAIL CACHING TESTS PASSED ✅")


if __name__ == "__main__":
    sys.exit(main())

"""
Frame Atlas — local test for Day 21 (V39): DP technical notes fields +
FTS5 full-text search over them.

Same trick as test_decks_locally.py / test_scene_reorder_locally.py: boots a
patched copy of the server against a throwaway database, seeds it with a
handful of SYNTHETIC images, then exercises:
  - POST /api/images/<id>/notes (owner-or-admin permission, field clearing)
  - the notes_fts virtual table + its three sync triggers
  - backfill_notes_fts() (the boot-time self-heal for pre-existing images)
  - /api/search?notes=[...] (AND-combines with tag chips, no-match handling)
  - /api/autocomplete's 'note' suggestion (present/absent, special chars)

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python3 scripts/test_dp_notes_search_locally.py
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
    workdir = tempfile.mkdtemp(prefix="frame_atlas_notes_search_test_")
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

    # ── 0. Migration + FTS5 table + triggers exist ──────────────────────────
    conn = sqlite3.connect(mod.DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    cols = {row["name"] for row in c.execute("PRAGMA table_info(images)").fetchall()}
    for col in ("camera_rig", "lens", "lens_filter", "stop", "onset_notes"):
        assert col in cols, f"images.{col} missing after migration"
    assert c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='notes_fts'"
    ).fetchone(), "notes_fts virtual table missing"
    trigger_sql = {
        row["name"]: row["sql"]
        for row in c.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall()
    }
    assert "notes_fts_ai" in trigger_sql and "notes_fts_ad" in trigger_sql and "notes_fts_au" in trigger_sql
    # The scoping clause is what stops every unrelated images write (crop,
    # favorite toggle, view log) from rebuilding the FTS row for nothing.
    assert "UPDATE OF camera_rig, lens, lens_filter, stop, onset_notes" in trigger_sql["notes_fts_au"], \
        trigger_sql["notes_fts_au"]
    print("0. Migration columns, notes_fts table, and all 3 triggers exist (UPDATE trigger correctly scoped) — OK.")

    # ── Seed images directly via SQL (mirrors how sync/upload/clip insert) ──
    ids = []
    for i in range(4):
        blob = make_jpeg(mod)
        c.execute(
            "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, caption, aspect_ratio)"
            " VALUES (1, ?, ?, ?, ?, ?)",
            (f"test-file-{i}", f"frame_{i}.jpg", blob, f"Test frame {i}", "16:9"),
        )
        ids.append(c.lastrowid)
    conn.commit()
    print(f"Inserted {len(ids)} synthetic images (admin/user 1): {ids}")

    # The AFTER INSERT trigger should have seeded a notes_fts row for each,
    # even with everything NULL — later UPDATEs need an existing rowid to hit.
    fts_rowids = {r[0] for r in c.execute("SELECT rowid FROM notes_fts").fetchall()}
    for img_id in ids:
        assert img_id in fts_rowids, f"AFTER INSERT trigger did not seed notes_fts for image {img_id}"
    print("1. AFTER INSERT trigger seeds a notes_fts row for every new image — OK.")

    client = mod.app.test_client()
    setup_r = client.post('/api/setup', json={'email': 'admin@test.com', 'password': 'testpass123'})
    assert setup_r.status_code == 200, setup_r.get_json()

    # ── 2. Owner (admin owns image 1 too) can save all 5 fields ─────────────
    payload = {
        "camera_rig": "Alexa Mini LF", "lens": "Probe Lens",
        "lens_filter": "Black Pro-Mist 1/4", "stop": "T2.8",
        "onset_notes": "Rain machine, lights on a colored chase.",
    }
    r = client.post(f"/api/images/{ids[0]}/notes", json=payload)
    assert r.status_code == 200, r.get_json()
    got = r.get_json()
    assert got == {"success": True, "notes": payload}, got
    print("2. Owner save round-trips all 5 fields exactly — OK.")

    # ── 3. Empty strings clear fields to NULL ────────────────────────────────
    r = client.post(f"/api/images/{ids[0]}/notes", json={
        "camera_rig": "", "lens": "", "lens_filter": "", "stop": "", "onset_notes": ""
    })
    assert r.status_code == 200
    cleared = r.get_json()["notes"]
    assert all(v is None for v in cleared.values()), cleared
    print("3. Empty-string fields clear to NULL — OK.")
    # Put real content back for the search tests below.
    r = client.post(f"/api/images/{ids[0]}/notes", json=payload)
    assert r.status_code == 200

    # ── 4. A second friend registers; friend can edit THEIR OWN image ───────
    _make_invite_code(mod, "FRIENDCODE1")
    friend_client = mod.app.test_client()
    reg = friend_client.post('/api/auth/register', json={
        'email': 'friend@test.com', 'password': 'friendpass123', 'username': 'friend',
        'invite_code': 'FRIENDCODE1'
    })
    assert reg.status_code == 200, reg.get_json()
    friend_uid = reg.get_json()["user"]["id"] if "user" in reg.get_json() else None

    # Give the friend their own image (owner-or-admin needs a real friend-owned row).
    friend_uid = c.execute("SELECT id FROM users WHERE username = 'friend'").fetchone()[0]
    blob = make_jpeg(mod, color=(20, 60, 200))
    c.execute(
        "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, caption, aspect_ratio)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (friend_uid, "friend-file-0", "friend_frame_0.jpg", blob, "Friend's frame", "16:9"),
    )
    friend_img_id = c.lastrowid
    conn.commit()

    r = friend_client.post(f"/api/images/{friend_img_id}/notes", json={
        "camera_rig": "Sony FX6", "lens": "35mm", "lens_filter": "", "stop": "T4", "onset_notes": "Handheld."
    })
    assert r.status_code == 200, r.get_json()
    print("4. Friend can edit fields on their OWN photo (owner-or-admin, deliberate departure from admin-only) — OK.")

    # ── 5. Admin can edit the friend's photo too ─────────────────────────────
    r = client.post(f"/api/images/{friend_img_id}/notes", json={
        "camera_rig": "Admin Override Cam", "lens": "", "lens_filter": "", "stop": "", "onset_notes": ""
    })
    assert r.status_code == 200, r.get_json()
    print("5. Admin can edit ANY photo's notes fields — OK.")

    # ── 6. A different non-owner, non-admin user is rejected with 404 ───────
    _make_invite_code(mod, "FRIENDCODE2")
    other_client = mod.app.test_client()
    reg2 = other_client.post('/api/auth/register', json={
        'email': 'other@test.com', 'password': 'otherpass123', 'username': 'other',
        'invite_code': 'FRIENDCODE2'
    })
    assert reg2.status_code == 200, reg2.get_json()
    r = other_client.post(f"/api/images/{friend_img_id}/notes", json={"camera_rig": "Should not land"})
    assert r.status_code == 404, r.get_json()
    print("6. A different non-owner, non-admin user is correctly rejected with 404 — OK.")

    # ── 7. FTS5 sync: after the owner save in step 2, notes_fts reflects it ──
    row = c.execute("SELECT camera_rig, onset_notes FROM notes_fts WHERE rowid = ?", (ids[0],)).fetchone()
    assert row["camera_rig"] == "Alexa Mini LF" and "colored chase" in row["onset_notes"], dict(row)
    print("7. notes_fts row reflects the saved fields (AFTER UPDATE OF ... trigger fired) — OK.")

    # ── 8. Editing something UNRELATED does not touch notes_fts content ─────
    # (The trigger's OF-clause scoping was already verified statically in
    # step 0; this confirms it holds at runtime too — toggling favorite must
    # not disturb the notes_fts row for a completely different reason to be
    # wrong, e.g. a stray full-row UPDATE elsewhere clobbering it with NULLs.)
    client.post(f"/api/images/{ids[0]}/favorite")
    row_after = c.execute("SELECT camera_rig, onset_notes FROM notes_fts WHERE rowid = ?", (ids[0],)).fetchone()
    assert dict(row_after) == dict(row), (dict(row), dict(row_after))
    print("8. Unrelated column edit (favorite toggle) leaves notes_fts untouched — OK.")

    # ── 9. AFTER DELETE trigger removes the notes_fts row ────────────────────
    # Deleted via raw SQL, not the /api/images/<id> DELETE endpoint — that
    # endpoint calls the real Google Drive API for an admin delete, which has
    # no credentials in this test environment and would fail for a reason
    # that has nothing to do with the trigger under test.
    del_target = ids[3]
    assert c.execute("SELECT 1 FROM notes_fts WHERE rowid = ?", (del_target,)).fetchone()
    c.execute("DELETE FROM images WHERE id = ?", (del_target,))
    conn.commit()
    assert not c.execute("SELECT 1 FROM notes_fts WHERE rowid = ?", (del_target,)).fetchone()
    print("9. Deleting an image row removes its notes_fts row (AFTER DELETE trigger) — OK.")

    # ── 10. Backfill: simulate a pre-V39 image (no notes_fts row) ───────────
    c.execute("DELETE FROM notes_fts WHERE rowid = ?", (ids[1],))
    conn.commit()
    assert not c.execute("SELECT 1 FROM notes_fts WHERE rowid = ?", (ids[1],)).fetchone()
    mod.backfill_notes_fts()
    assert c.execute("SELECT 1 FROM notes_fts WHERE rowid = ?", (ids[1],)).fetchone()
    # Second call is a no-op (nothing left to seed) — the whole point of the
    # self-disabling design is that every later boot does zero work here.
    mod.backfill_notes_fts()
    print("10. backfill_notes_fts() seeds a missing row and is a no-op on the next call — OK.")

    # ── 11. Search: notes param matches, AND-combines with a tag chip ───────
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, 'camera_format', 'digital')",
              (ids[0],))
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, 'camera_format', 'digital')",
              (ids[2],))
    conn.commit()

    import json as _json
    r = client.get('/api/search', query_string={'notes': _json.dumps(['colored chase'])})
    data = r.get_json()
    assert [im["id"] for im in data["images"]] == [ids[0]], data
    print("11a. /api/search?notes=[...] matches only the photo whose on-set notes mention the phrase — OK.")

    r = client.get('/api/search', query_string={
        'notes': _json.dumps(['colored chase']), 'chips': 'digital'
    })
    data = r.get_json()
    assert [im["id"] for im in data["images"]] == [ids[0]], data
    print("11b. Notes filter AND-combines correctly with an active tag chip — OK.")

    r = client.get('/api/search', query_string={'notes': _json.dumps(['nonexistent gibberish xyz'])})
    assert r.status_code == 200 and r.get_json()["images"] == []
    print("11c. A non-matching phrase returns zero results without erroring — OK.")

    # A phrase with FTS5-special characters must not 500 the search endpoint.
    r = client.get('/api/search', query_string={'notes': _json.dumps(['T2.8 "weird" -query*'])})
    assert r.status_code == 200, r.get_json()
    print("11d. A phrase with FTS5 special characters (\", -, *, .) does not error — OK.")

    # Every image dict returned by search carries a `notes` object.
    r = client.get('/api/search')
    for im in r.get_json()["images"]:
        assert "notes" in im and isinstance(im["notes"], dict), im
        for key in ("camera_rig", "lens", "lens_filter", "stop", "onset_notes"):
            assert key in im["notes"], im["notes"]
    print("11e. Every image dict from /api/search carries a `notes` object with all 5 keys — OK.")

    # ── 12. Autocomplete: 'note' suggestion present/absent, special chars ───
    r = client.get('/api/autocomplete', query_string={'q': 'colored ch'})
    types = [o["type"] for o in r.get_json()]
    assert "note" in types, r.get_json()
    note_entry = next(o for o in r.get_json() if o["type"] == "note")
    assert note_entry["value"] == "colored ch" and note_entry["count"] >= 1, note_entry
    print("12a. Autocomplete offers a live 'note' suggestion for a prefix that matches on-set notes — OK.")

    r = client.get('/api/autocomplete', query_string={'q': 'zzz_nonexistent_zzz'})
    types = [o["type"] for o in r.get_json()]
    assert "note" not in types, r.get_json()
    print("12b. No 'note' suggestion when nothing matches — OK.")

    r = client.get('/api/autocomplete', query_string={'q': 'weird"-*query'})
    assert r.status_code == 200, r.status_code
    print("12c. A query with FTS5 special characters does not 500 the autocomplete endpoint — OK.")

    conn.close()
    shutil.rmtree(workdir)
    print("\nALL LOCAL DP NOTES + SEARCH TESTS PASSED ✅")


if __name__ == "__main__":
    main()

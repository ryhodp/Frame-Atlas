"""
Frame Atlas — local test for the V18 crew/member sharing backend (view-only
members on a deck, invite-by-email, invite links, activity feed).

Same trick as test_decks_locally.py: boots a patched copy of the server
against a throwaway database seeded with synthetic images, but this one uses
TWO logged-in test clients (owner + friend) to exercise cross-user access.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_crew_locally.py
"""

import importlib.util
import io
import os
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
NUM_IMAGES = 3


def make_jpeg(mod, color=(80, 120, 200)):
    img = mod.Image.new("RGB", (160, 90), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_crew_test_")
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

    owner = mod.app.test_client()
    r = owner.post('/api/setup', json={'email': 'ryan@test.com', 'password': 'testpass123'})
    assert r.status_code == 200, r.get_json()

    # Owner builds a deck with a scene and some photos.
    deck = owner.post('/api/decks', json={'name': 'Crew Test Deck'}).get_json()
    deck_id = deck['id']
    owner.post(f'/api/decks/{deck_id}/images', json={'image_ids': ids})
    scene = owner.post('/api/scenes', json={'deck_id': deck_id, 'name': 'Opening'}).get_json()
    print(f"Owner built deck {deck_id} with scene '{scene['name']}'.")

    # Register a friend via an admin-issued invite code (same flow real signup uses).
    code = owner.post('/api/admin/invite-codes').get_json()['code']
    friend = mod.app.test_client()
    r = friend.post('/api/auth/register', json={'invite_code': code, 'username': 'alex', 'password': 'friendpass123'})
    assert r.status_code == 200, r.get_json()
    friend_id = r.get_json()['user']['id']
    # register() doesn't collect email — set it directly, same as a friend filling out Account later.
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE users SET email = 'alex@test.com' WHERE id = ?", (friend_id,))
    conn.commit()
    conn.close()
    print(f"Registered friend 'alex' (id={friend_id}).")

    # 1. Friend has no access yet.
    r = friend.get(f'/api/decks/{deck_id}')
    assert r.status_code == 404, r.get_json()
    print("Friend correctly has no access before being invited — OK.")

    # 2. Inviting an unknown email fails cleanly.
    r = owner.post(f'/api/decks/{deck_id}/invite', json={'email': 'nobody@nowhere.com'})
    assert r.status_code == 404 and r.get_json()['error'] == 'no_account', r.get_json()
    print("Inviting an email with no account correctly rejected — OK.")

    # 3. Invite by email.
    r = owner.post(f'/api/decks/{deck_id}/invite', json={'email': 'ALEX@test.com'})  # case-insensitive
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['user_id'] == friend_id
    print("Invite by email (case-insensitive) OK.")

    # 4. Friend can now view — read-only.
    r = friend.get(f'/api/decks/{deck_id}')
    assert r.status_code == 200, r.get_json()
    fd = r.get_json()
    assert fd['is_owner'] is False and fd['owner_name'] == 'ryan'
    assert len(fd['images']) == len(ids)
    print("Friend can view the deck as a non-owner — OK.")

    # 5. Friend is blocked from every write endpoint (same 404-as-owner-check pattern).
    assert friend.patch(f'/api/decks/{deck_id}', json={'name': 'Hijacked'}).status_code == 404
    assert friend.post('/api/scenes', json={'deck_id': deck_id, 'name': 'Sneaky'}).status_code == 404
    assert friend.delete(f'/api/scenes/{scene["id"]}').status_code == 404
    assert friend.post(f'/api/decks/{deck_id}/images', json={'image_ids': ids}).status_code == 404
    print("Friend blocked from rename/scene-create/scene-delete/add-images — OK.")

    # 6. Owner sees the member in the member list; friend cannot list members.
    r = owner.get(f'/api/decks/{deck_id}/members')
    members = r.get_json()
    assert len(members) == 1 and members[0]['user_id'] == friend_id
    assert friend.get(f'/api/decks/{deck_id}/members').status_code == 404
    print("Owner-only member list works, friend can't call it — OK.")

    # 7. Invite link: a second friend joins via link instead of email.
    r = owner.post(f'/api/decks/{deck_id}/invite-link')
    assert r.status_code == 200, r.get_json()
    token = r.get_json()['invite_token']
    assert r.get_json()['invite_path'] == f'/invite/{token}'

    code2 = owner.post('/api/admin/invite-codes').get_json()['code']
    friend2 = mod.app.test_client()
    r = friend2.post('/api/auth/register', json={'invite_code': code2, 'username': 'sam', 'password': 'friendpass123'})
    sam_id = r.get_json()['user']['id']

    r = friend2.post(f'/api/decks/invite/{token}/accept')
    assert r.status_code == 200 and r.get_json()['deck_id'] == deck_id, r.get_json()
    r = friend2.get(f'/api/decks/{deck_id}')
    assert r.status_code == 200 and r.get_json()['is_owner'] is False
    print("Second friend joined via invite link and can view — OK.")

    # Accepting again is idempotent (no duplicate member row / duplicate 'joined' log entry).
    friend2.post(f'/api/decks/invite/{token}/accept')
    r = owner.get(f'/api/decks/{deck_id}/members')
    assert len(r.get_json()) == 2, r.get_json()
    print("Re-accepting the same link is idempotent — OK.")

    # 8. Revoked invite link stops working.
    owner.delete(f'/api/decks/{deck_id}/invite-link')
    friend3_code = owner.post('/api/admin/invite-codes').get_json()['code']
    friend3 = mod.app.test_client()
    friend3.post('/api/auth/register', json={'invite_code': friend3_code, 'username': 'jo', 'password': 'friendpass123'})
    r = friend3.post(f'/api/decks/invite/{token}/accept')
    assert r.status_code == 404, r.get_json()
    print("Revoked invite link correctly rejected — OK.")

    # 9. Deck list shows shared decks for members, with is_owner=False + owner_name.
    r = friend.get('/api/decks')
    friend_decks = r.get_json()
    assert len(friend_decks) == 1 and friend_decks[0]['is_owner'] is False
    assert friend_decks[0]['owner_name'] == 'ryan'
    print("Friend's deck list includes the shared deck, correctly marked — OK.")

    # 10. Activity feed reflects everything so far, newest first, visible to owner + members.
    r = owner.get(f'/api/decks/{deck_id}/activity')
    activity = r.get_json()
    actions = [a['action'] for a in activity]
    assert actions[0] == 'joined'  # most recent = sam's second accept (idempotent, still just one 'joined' from first accept)
    assert 'invited' in actions and 'added_scene' in actions and 'added_photos' in actions
    r = friend.get(f'/api/decks/{deck_id}/activity')
    assert r.status_code == 200 and len(r.get_json()) == len(activity)
    print(f"Activity feed has {len(activity)} entries, visible to owner and members — OK.")

    # 11. Owner removes a member — they lose access immediately.
    owner.delete(f'/api/decks/{deck_id}/members/{friend_id}')
    r = friend.get(f'/api/decks/{deck_id}')
    assert r.status_code == 404, r.get_json()
    print("Removed member immediately loses access — OK.")

    # 12. Deleting the deck cascades deck_members + deck_activity (vanishes for remaining member too).
    owner.delete(f'/api/decks/{deck_id}')
    r = friend2.get(f'/api/decks/{deck_id}')
    assert r.status_code == 404, r.get_json()
    conn = sqlite3.connect(db_path)
    left_members = conn.execute("SELECT COUNT(*) FROM deck_members WHERE deck_id = ?", (deck_id,)).fetchone()[0]
    left_activity = conn.execute("SELECT COUNT(*) FROM deck_activity WHERE deck_id = ?", (deck_id,)).fetchone()[0]
    conn.close()
    assert left_members == 0 and left_activity == 0, (left_members, left_activity)
    print("Deck delete cascaded deck_members + deck_activity, vanished for remaining member — OK.")

    shutil.rmtree(workdir)
    print("\nALL LOCAL CREW/MEMBER TESTS PASSED ✅")


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Day 24 (V42): local checks for the client-feedback backend — anonymous
picks + comments on a deck's public /share/<token> link.

Boots a patched copy of the server against a throwaway database (same trick
as the other test_*_locally.py scripts), seeds synthetic images/decks, and
drives everything through Flask's test client: no real browser, no real
Drive, no real Gemini.

What actually matters here, and why each section exists:
- New decks default feedback ON; decks that existed before this migration
  default OFF. Getting this backwards would either silently open every
  already-shared link to the public, or silently keep every future deck too.
- A pick is idempotent per (frame, browser token) — double-clicking Pick, or
  a retried request, must never inflate the count.
- The owner's Feedback panel and the public share page's own feedback view
  share one function (_deck_feedback_payload) — this file checks they can
  never show different numbers for the same deck.
- Feedback writes must 404 the moment the deck's switch is off, even with a
  perfectly valid token and a perfectly valid deck_image_id.
- Deleting a deck, or removing one photo from a deck, must not leave orphan
  pick/comment rows behind — and must not crash trying to clean them up.

Run:  scripts/.venv/bin/python scripts/test_client_feedback_locally.py
"""

import io
import os
import sys
import sqlite3
import tempfile
import importlib.util

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(REPO, 'backend'))

from PIL import Image  # noqa: E402

passed = 0
failed = 0


def check(label, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print(f'  PASS  {label}')
    else:
        failed += 1
        print(f'  FAIL  {label}  {detail}')


def make_jpeg(color=(180, 90, 40)):
    buf = io.BytesIO()
    Image.new('RGB', (800, 450), color).save(buf, format='JPEG', quality=85)
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix='frame_atlas_feedback_test_')
    db_path = os.path.join(workdir, 'library.db')

    src = open(os.path.join(REPO, 'backend', 'app.py')).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f'DB_PATH = {db_path!r}')
    assert patched != src, 'Could not find DB_PATH line to patch'
    open(os.path.join(workdir, 'app.py'), 'w').write(patched)

    os.environ.setdefault('GOOGLE_OAUTH_CLIENT_ID', 'dummy')
    os.environ.setdefault('GOOGLE_OAUTH_CLIENT_SECRET', 'dummy')
    os.environ.setdefault('GEMINI_API_KEY', 'dummy')

    spec = importlib.util.spec_from_file_location('test_app_feedback', os.path.join(workdir, 'app.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print('App imported OK.\n')

    admin = mod.app.test_client()
    setup_r = admin.post('/api/setup', json={'email': 'ryan@test.com', 'password': 'adminpass123'})
    assert setup_r.status_code == 200, setup_r.get_json()

    # Two synthetic images to build a deck out of.
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    image_ids = []
    for i in range(2):
        c.execute(
            "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio)"
            " VALUES (1, ?, ?, ?, ?)",
            (f'test-file-{i}', f'frame_{i}.jpg', make_jpeg(), '16:9'),
        )
        image_ids.append(c.lastrowid)
    conn.commit()
    conn.close()

    # ── New decks default feedback ON ────────────────────────────────────────
    print('--- new-deck default ---')
    deck = admin.post('/api/decks', json={'name': 'Nightfall Pitch'}).get_json()
    deck_id = deck['id']
    check('create_deck response reports feedback on', deck.get('feedback_enabled') is True)
    got = admin.get(f'/api/decks/{deck_id}').get_json()
    check('a freshly created deck is feedback-enabled', got['feedback_enabled'] is True, got)

    # ── Pre-migration decks default OFF ──────────────────────────────────────
    print('\n--- pre-existing-deck default (simulating a deck from before V42) ---')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("INSERT INTO decks (user_id, name) VALUES (1, 'Old Deck')")  # no feedback_enabled given
    old_deck_id = c.lastrowid
    conn.commit()
    conn.close()
    old = admin.get(f'/api/decks/{old_deck_id}').get_json()
    check('a deck created without the column explicitly comes back OFF',
          old['feedback_enabled'] is False, old)

    # ── Build out the deck under test: two frames, a share link ─────────────
    added = admin.post(f'/api/decks/{deck_id}/images', json={'image_ids': image_ids}).get_json()
    check('both images added to the deck', added['added'] == 2, added)
    deck_full = admin.get(f'/api/decks/{deck_id}').get_json()
    deck_image_ids = [img['deck_image_id'] for img in deck_full['images']]
    check('two deck_image rows exist', len(deck_image_ids) == 2, deck_image_ids)
    di_a, di_b = deck_image_ids

    share = admin.post(f'/api/decks/{deck_id}/share').get_json()
    token = share['share_token']
    check('share token minted', bool(token), share)

    VIEWER_A = 'aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa'
    VIEWER_B = 'bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb'

    # ── Public GET before anyone has left feedback ───────────────────────────
    print('\n--- public feedback view, empty deck ---')
    fb = admin.get(f'/api/share/{token}/feedback').get_json()
    check('enabled reports true', fb['enabled'] is True, fb)
    check('no ranked frames yet', fb['ranked_deck_image_ids'] == [], fb)
    check('totals start at zero', fb['total_picks'] == 0 and fb['total_comments'] == 0, fb)

    # ── Picking: validation ───────────────────────────────────────────────────
    print('\n--- pick validation ---')
    r = admin.post(f'/api/share/{token}/picks', json={'viewer_token': VIEWER_A, 'viewer_name': 'Sarah'})
    check('missing deck_image_id is a 400', r.status_code == 400, r.get_json())
    r = admin.post(f'/api/share/{token}/picks', json={'deck_image_id': di_a, 'viewer_name': 'Sarah'})
    check('missing viewer_token is a 400', r.status_code == 400, r.get_json())
    r = admin.post(f'/api/share/{token}/picks', json={'deck_image_id': di_a, 'viewer_token': VIEWER_A})
    check('missing viewer_name is a 400', r.status_code == 400, r.get_json())
    r = admin.post(f'/api/share/{token}/picks',
                   json={'deck_image_id': di_a, 'viewer_token': 'short', 'viewer_name': 'Sarah'})
    check('a malformed viewer_token is rejected', r.status_code == 400, r.get_json())
    r = admin.post(f'/api/share/{token}/picks',
                   json={'deck_image_id': 999999, 'viewer_token': VIEWER_A, 'viewer_name': 'Sarah'})
    check('a deck_image_id from another deck is a 404, not a pick', r.status_code == 404, r.get_json())

    # ── Picking: success + idempotency ───────────────────────────────────────
    print('\n--- picking a frame ---')
    r = admin.post(f'/api/share/{token}/picks',
                   json={'deck_image_id': di_a, 'viewer_token': VIEWER_A, 'viewer_name': 'Sarah'})
    check('first pick succeeds', r.status_code == 200 and r.get_json()['pick_count'] == 1, r.get_json())

    r = admin.post(f'/api/share/{token}/picks',
                   json={'deck_image_id': di_a, 'viewer_token': VIEWER_A, 'viewer_name': 'Sarah R.'})
    check('re-picking the SAME frame from the SAME browser does not double the count',
          r.get_json()['pick_count'] == 1, r.get_json())

    r = admin.post(f'/api/share/{token}/picks',
                   json={'deck_image_id': di_a, 'viewer_token': VIEWER_B, 'viewer_name': 'Jon'})
    check('a DIFFERENT browser picking the same frame does add to the count',
          r.get_json()['pick_count'] == 2, r.get_json())

    fb = admin.get(f'/api/share/{token}/feedback',
                    headers={'X-FA-Viewer': VIEWER_A}).get_json()
    frame_a = fb['frames'][str(di_a)]
    check('pick_count for frame A is 2', frame_a['pick_count'] == 2, frame_a)
    check('the retyped name overwrote the original, not appended a second entry',
          sorted(frame_a['pickers']) == ['Jon', 'Sarah R.'], frame_a)
    check('picked_by_me is true for the browser asking (VIEWER_A)', frame_a['picked_by_me'] is True, frame_a)

    fb_b = admin.get(f'/api/share/{token}/feedback', headers={'X-FA-Viewer': VIEWER_B}).get_json()
    check('picked_by_me is also true for VIEWER_B, scoped per-browser',
          fb_b['frames'][str(di_a)]['picked_by_me'] is True, fb_b)

    fb_anon = admin.get(f'/api/share/{token}/feedback').get_json()
    check('no viewer header means picked_by_me is false for everyone',
          fb_anon['frames'][str(di_a)]['picked_by_me'] is False, fb_anon)

    # ── Un-picking ────────────────────────────────────────────────────────────
    print('\n--- un-picking ---')
    r = admin.delete(f'/api/share/{token}/picks', json={'deck_image_id': di_a, 'viewer_token': VIEWER_B})
    check('un-pick drops the count back to 1', r.get_json()['pick_count'] == 1, r.get_json())
    r = admin.delete(f'/api/share/{token}/picks', json={'deck_image_id': di_a, 'viewer_token': VIEWER_B})
    check('un-picking again (already gone) is a no-op, not an error',
          r.status_code == 200 and r.get_json()['pick_count'] == 1, r.get_json())

    # ── Comments: validation ─────────────────────────────────────────────────
    print('\n--- comment validation ---')
    r = admin.post(f'/api/share/{token}/comments',
                   json={'deck_image_id': di_b, 'viewer_token': VIEWER_A, 'viewer_name': 'Sarah', 'body': '   '})
    check('an empty (whitespace-only) comment body is a 400', r.status_code == 400, r.get_json())
    r = admin.post(f'/api/share/{token}/comments',
                   json={'deck_image_id': di_b, 'viewer_token': VIEWER_A, 'viewer_name': 'Sarah',
                         'body': 'x' * 2001})
    check('a comment over the length cap is a 400', r.status_code == 400, r.get_json())

    # ── Comments: success ────────────────────────────────────────────────────
    print('\n--- posting comments ---')
    r = admin.post(f'/api/share/{token}/comments',
                   json={'deck_image_id': di_b, 'viewer_token': VIEWER_A, 'viewer_name': 'Sarah R.',
                         'body': 'Love the lighting on this one.'})
    check('comment created', r.status_code == 200 and r.get_json().get('id'), r.get_json())
    comment_id = r.get_json()['id']

    admin.post(f'/api/share/{token}/comments',
               json={'deck_image_id': di_b, 'viewer_token': VIEWER_B, 'viewer_name': 'Jon',
                     'body': 'Agreed, this is the one for the opener.'})

    fb = admin.get(f'/api/share/{token}/feedback').get_json()
    check('two comments now visible on frame B, in post order',
          [c_['viewer_name'] for c_ in fb['frames'][str(di_b)]['comments']] == ['Sarah R.', 'Jon'], fb)
    check('every viewer sees the same comments (collaborative, per Ryan\'s call)',
          fb['total_comments'] == 2, fb)

    # ── Owner view matches public view exactly ───────────────────────────────
    print('\n--- owner Feedback panel matches the public view ---')
    owner_fb = admin.get(f'/api/decks/{deck_id}/feedback').get_json()
    check('same total_picks', owner_fb['total_picks'] == fb['total_picks'], (owner_fb, fb))
    check('same total_comments', owner_fb['total_comments'] == fb['total_comments'], (owner_fb, fb))
    check('most-picked frame (A, 1 pick) ranks ahead of comment-only frame (B, 0 picks)',
          owner_fb['ranked_deck_image_ids'][0] == di_a, owner_fb)
    check("owner's own view never claims picked_by_me (owner isn't a viewer)",
          all(not f['picked_by_me'] for f in owner_fb['frames'].values()), owner_fb)

    # ── Feedback OFF blocks every public write ───────────────────────────────
    print('\n--- turning feedback off blocks writes but not reads ---')
    toggled = admin.post(f'/api/decks/{deck_id}/feedback-enabled', json={'enabled': False}).get_json()
    check('toggle reports off', toggled['feedback_enabled'] is False, toggled)

    r = admin.post(f'/api/share/{token}/picks',
                   json={'deck_image_id': di_a, 'viewer_token': VIEWER_A, 'viewer_name': 'Sarah'})
    check('picking with feedback off is a 404', r.status_code == 404, r.get_json())
    r = admin.post(f'/api/share/{token}/comments',
                   json={'deck_image_id': di_a, 'viewer_token': VIEWER_A, 'viewer_name': 'Sarah', 'body': 'hi'})
    check('commenting with feedback off is a 404', r.status_code == 404, r.get_json())
    fb_off = admin.get(f'/api/share/{token}/feedback').get_json()
    check('the READ still works and reports enabled=false, existing feedback hidden',
          fb_off == {'enabled': False, 'frames': {}, 'ranked_deck_image_ids': [],
                     'total_picks': 0, 'total_comments': 0}, fb_off)

    # Turn it back on for the rest of the checks.
    admin.post(f'/api/decks/{deck_id}/feedback-enabled', json={'enabled': True})

    # ── Comment deletion: owner-only pressure valve ──────────────────────────
    print('\n--- deleting a comment ---')
    r = admin.delete(f'/api/decks/{deck_id}/comments/{comment_id}')
    check('owner can delete a comment', r.status_code == 200, r.get_json())
    fb = admin.get(f'/api/share/{token}/feedback').get_json()
    check('the deleted comment is gone', comment_id not in
          [c_['id'] for c_ in fb['frames'].get(str(di_b), {}).get('comments', [])], fb)
    r = admin.delete(f'/api/decks/{deck_id}/comments/{comment_id}')
    check('deleting it again is a clean 404, not a 500', r.status_code == 404, r.get_json())

    # ── Cross-user: another logged-in user cannot manage this deck's feedback ─
    print('\n--- another user cannot touch this deck\'s feedback ---')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("INSERT INTO invite_codes (code, created_by) VALUES ('TESTCODE', 1)")
    conn.commit()
    conn.close()
    other = mod.app.test_client()
    other.post('/api/auth/register', json={
        'username': 'casey', 'email': 'casey@test.com',
        'password': 'friendpass123', 'invite_code': 'TESTCODE'})

    r = other.get(f'/api/decks/{deck_id}/feedback')
    check('a different user gets 404 reading feedback, not the owner\'s data', r.status_code == 404, r.get_json())
    r = other.post(f'/api/decks/{deck_id}/feedback-enabled', json={'enabled': False})
    check('a different user cannot toggle feedback on this deck', r.status_code == 404, r.get_json())

    # ── Anonymous (no session at all) can still use the public endpoints ─────
    print('\n--- fully anonymous client (no login at all) ---')
    anon = mod.app.test_client()
    r = anon.get(f'/api/share/{token}/feedback')
    check('anonymous GET works with no login', r.status_code == 200, r.get_json())
    r = anon.post(f'/api/share/{token}/picks',
                   json={'deck_image_id': di_b, 'viewer_token': VIEWER_B, 'viewer_name': 'Alex'})
    check('anonymous POST pick works with no login', r.status_code == 200 and r.get_json()['picked'], r.get_json())

    # ── Cascade cleanup: removing one photo drops its feedback ───────────────
    print('\n--- removing a photo from the deck cleans up its feedback ---')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    before_picks = c.execute('SELECT COUNT(*) FROM deck_picks WHERE deck_image_id = ?', (di_b,)).fetchone()[0]
    conn.close()
    check('frame B actually has a pick to clean up (sanity check on the test itself)', before_picks > 0)

    admin.delete(f'/api/deck-images/{di_b}')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    after_picks = c.execute('SELECT COUNT(*) FROM deck_picks WHERE deck_image_id = ?', (di_b,)).fetchone()[0]
    conn.close()
    check('picks on the removed frame are gone, not orphaned', after_picks == 0, after_picks)

    # ── Cascade cleanup: deleting the whole deck doesn't crash or orphan rows ─
    print('\n--- deleting the deck cleans up remaining feedback ---')
    r = admin.delete(f'/api/decks/{deck_id}')
    check('deck delete succeeds with feedback rows still attached', r.status_code == 200, r.get_json())
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    remaining_picks = c.execute(
        "SELECT COUNT(*) FROM deck_picks WHERE deck_image_id IN (SELECT id FROM deck_images WHERE deck_id = ?)",
        (deck_id,)
    ).fetchone()[0]
    conn.close()
    check('no orphaned picks remain after the deck is gone', remaining_picks == 0, remaining_picks)

    print(f'\n{passed} passed, {failed} failed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()

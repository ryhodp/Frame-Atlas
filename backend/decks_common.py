"""decks_common.py — deck helpers shared by routes_decks.py AND routes_share.py (Day 41 / Phase 3).

Who may open a deck (_deck_access), the activity log (touch_deck /
log_deck_activity), how a person is named in the UI (_display_name — also
used by app.py's /api/analytics/users), the full deck payload (_deck_payload,
used by the owner's view AND the public share link), and the picks +
comments tally (_deck_feedback_payload, likewise both sides).

Also run_self_test(): the boot-time check that calls _deck_access() and
touch_deck() against a canary row in the REAL database. It lives HERE, next
to the helpers it exercises, so it always calls the same module-level names
a test patches (test_self_test_locally.py patches decks_common._deck_access).
app.py passes it to init_db(run_self_test=...), unchanged.

Every function is character-for-character what it was in app.py.
"""
from flask import session

import images_common


def run_self_test(conn):
    """V50 (Day 48 cont'd): exercises the ACTUAL queries a real request
    would run, against a disposable "canary" row in the REAL database —
    not a fresh one built for a test, and not just a check that the right
    columns exist.

    Why this exists on top of check_schema(): that function would have
    caught decks.updated_at going missing INSTANTLY. It would NOT catch a
    different-shaped bug in the same feature — a column that exists but a
    query built on it is wrong (a backwards WHERE, the wrong table, a typo
    that still parses). check_schema() asks "does the shape exist?"; this
    asks "does calling the real function actually work?" — by calling
    _deck_access() and touch_deck() directly, the same functions every real
    request calls, not a hand-copied imitation of them that could itself
    drift out of sync.

    The canary is ALWAYS removed in a finally block, even if a check raises
    partway through, so a run of this can never leave debris in Ryan's real
    deck list. Nothing it does is visible to any real user at any point —
    insert, probe, delete, all within this one function call.

    Skipped (not failed, and not logged as a failure) when:
      - a required column is already known missing — that's check_schema's
        finding to report; running these queries against a schema already
        known broken would just reproduce the same failure with less clarity
      - there are no users yet (decks.user_id is NOT NULL; a fresh,
        pre-setup install has nothing to attach a canary deck to)

    Non-fatal by design, matching check_schema(): one broken feature must
    not take the rest of the app down with it."""
    c = conn.cursor()
    results = []  # (check name, ok, detail-or-None)

    user_row = c.execute('SELECT id FROM users LIMIT 1').fetchone()
    if not user_row:
        print('[selftest] Skipped — no users yet (fresh install).')
        return results
    user_id = user_row[0]

    CANARY_NAME = '__frame_atlas_selftest_canary__'
    deck_id = None
    try:
        c.execute('INSERT INTO decks (user_id, name) VALUES (?, ?)', (user_id, CANARY_NAME))
        conn.commit()
        deck_id = c.lastrowid

        # 1. The exact read every "open a deck" request makes.
        try:
            deck_row, is_owner = _deck_access(c, deck_id, user_id)
            ok = deck_row is not None and is_owner
            results.append(('deck open (_deck_access)', ok, None if ok else 'row not returned as owner'))
        except Exception as e:
            results.append(('deck open (_deck_access)', False, str(e)))

        # 2. The exact write every deck mutation makes (rename, add photo,
        #    reorder, ...) to bump the "last changed" stamp.
        try:
            touch_deck(c, deck_id)
            conn.commit()
            results.append(('deck touch (touch_deck)', True, None))
        except Exception as e:
            results.append(('deck touch (touch_deck)', False, str(e)))

        # 3. The public /api/share/<token> lookup — same query shape,
        #    exercised the same way get_shared_deck() actually calls it.
        try:
            probe_token = 'selftest-canary-token'
            c.execute('UPDATE decks SET share_token = ? WHERE id = ?', (probe_token, deck_id))
            conn.commit()
            shared_row = c.execute(
                'SELECT id, name, created_at, updated_at, share_token, user_id, feedback_enabled '
                'FROM decks WHERE share_token = ?', (probe_token,)
            ).fetchone()
            ok = shared_row is not None
            results.append(('public share lookup', ok, None if ok else 'row not found by token'))
        except Exception as e:
            results.append(('public share lookup', False, str(e)))

    finally:
        if deck_id is not None:
            try:
                c.execute('DELETE FROM decks WHERE id = ?', (deck_id,))
                conn.commit()
            except Exception as e:
                print(f'[selftest] WARNING: could not remove canary deck {deck_id}: {e}')

    failures = [(name, detail) for name, ok, detail in results if not ok]
    if failures:
        print('[selftest] ' + '=' * 62)
        print(f'[selftest] CRITICAL: {len(failures)} live check(s) FAILED against the real database.')
        for name, detail in failures:
            print(f'[selftest]     {name}: {detail}')
        print('[selftest] The real feature behind each of these will fail for real requests too.')
        print('[selftest] ' + '=' * 62)
    else:
        print(f'[selftest] OK — {len(results)} live check(s) passed against the real database.')
    return results


def _display_name(row):
    """Best-effort human label for a user row: username, falling back to
    email (or a generic id label) if somehow both are blank."""
    if not row:
        return 'Unknown'
    return row['username'] or row['email'] or f"user {row['id']}"

def _deck_access(c, deck_id, user_id):
    """Returns (deck_row, is_owner) if this user can VIEW the deck — either
    because they own it or because they're an invited view-only member.
    Returns (None, False) if neither. Callers that only allow edits (rename,
    add/remove photos, etc.) should keep using the stricter
    `user_id = session['user_id']` owner-only check instead of this."""
    deck_row = c.execute(
        'SELECT id, name, created_at, updated_at, share_token, invite_token, user_id, feedback_enabled '
        'FROM decks WHERE id = ?', (deck_id,)
    ).fetchone()
    if not deck_row:
        return None, False
    if deck_row['user_id'] == user_id:
        return deck_row, True
    is_member = c.execute(
        'SELECT 1 FROM deck_members WHERE deck_id = ? AND user_id = ?', (deck_id, user_id)
    ).fetchone()
    if is_member:
        return deck_row, False
    return None, False

def touch_deck(c, deck_id):
    """Bumps a deck's last-modified stamp. The offline cache diffs this to
    decide whether to show the "New changes" banner, so anything that alters
    what a deck LOOKS like has to call it."""
    c.execute('UPDATE decks SET updated_at = CURRENT_TIMESTAMP WHERE id = ?', (deck_id,))

def log_deck_activity(c, deck_id, action, detail=None):
    """Appends one row to the deck's activity feed, attributed to whoever's
    logged in right now. Only the deck owner can call the write endpoints
    this is hooked into, so `action` almost always describes an owner edit —
    the exception is 'invited'/'joined', which fire for the two sides of a
    member joining.

    Also bumps the deck's updated_at: every mutating endpoint already logs
    activity, so hooking the timestamp here keeps the two from drifting apart
    the way they would if each endpoint had to remember both calls."""
    c.execute(
        'INSERT INTO deck_activity (deck_id, user_id, action, detail) VALUES (?, ?, ?, ?)',
        (deck_id, session['user_id'], action, detail)
    )
    touch_deck(c, deck_id)

def _deck_payload(c, deck_row, public=False):
    """Full deck JSON: deck info + ordered scenes + flat image list. Shared by
    the owner view (GET /api/decks/<id>) and the public share view
    (GET /api/share/<token>) so the two can never drift apart. Images come back
    in storyboard order (unordered rows last, then by row id) — the frontend
    preserves this order when it groups images into scene sections.

    `public=True` (only ever passed by the share-token route) makes every
    image's thumbnail an embedded base64 blob instead of a login-gated URL —
    see build_image_dict()'s docstring (V43/Day 25)."""
    deck_id = deck_row['id']
    scenes = [
        {'id': s['id'], 'name': s['name'], 'sort_order': s['sort_order']}
        for s in c.execute(
            'SELECT id, name, sort_order FROM scenes WHERE deck_id = ? ORDER BY sort_order ASC', (deck_id,)
        ).fetchall()
    ]

    di_rows = c.execute('''
        SELECT id, scene_id, image_id, storyboard_order, storyboard_note
        FROM deck_images WHERE deck_id = ?
        ORDER BY CASE WHEN storyboard_order IS NULL THEN 1 ELSE 0 END,
                 storyboard_order ASC, id ASC
    ''', (deck_id,)).fetchall()

    images_out = []
    for di in di_rows:
        img_dict = images_common._fetch_image_dict(c, di['image_id'], deck_row['user_id'], public=public)
        if img_dict is None:
            continue
        img_dict['deck_image_id'] = di['id']
        img_dict['scene_id'] = di['scene_id']
        img_dict['storyboard_order'] = di['storyboard_order']
        img_dict['storyboard_note'] = di['storyboard_note']
        images_out.append(img_dict)

    return {
        'id': deck_row['id'],
        'name': deck_row['name'],
        'created_at': deck_row['created_at'],
        # The offline cache compares this against its saved copy to decide
        # whether to offer a refresh — leaving it out of the payload made the
        # frontend's "New changes" banner permanently dead.
        'updated_at': deck_row['updated_at'],
        'share_token': deck_row['share_token'],
        # V42: whether the share link accepts picks/comments. Every caller of
        # this function selects the column now (see _deck_access and
        # get_shared_deck) so it's always present on deck_row.
        'feedback_enabled': bool(deck_row['feedback_enabled']),
        'scenes': scenes,
        'images': images_out
    }

def _deck_feedback_payload(c, deck_id, viewer_token=None):
    """Picks + comments for every frame in a deck that has either, grouped by
    deck_image_id and ranked most-picked first. Shared by the owner's
    Feedback panel and the public share page's own view of the same data, so
    the two can never drift apart — same reasoning as _deck_payload().

    `viewer_token`, when given, marks which picks belong to THIS browser
    (`picked_by_me`) — left as None for the owner's own view, since the
    owner isn't a "viewer" with a token of their own.

    Thumbnails/filenames are deliberately NOT included here — both callers
    already have deck.images in hand (from _deck_payload) and can cross-
    reference by deck_image_id, so this stays a small, fast query."""
    pick_rows = c.execute('''
        SELECT dp.deck_image_id, dp.viewer_name, dp.viewer_token
        FROM deck_picks dp
        JOIN deck_images di ON di.id = dp.deck_image_id
        WHERE di.deck_id = ?
        ORDER BY dp.created_at ASC
    ''', (deck_id,)).fetchall()

    comment_rows = c.execute('''
        SELECT dc.id, dc.deck_image_id, dc.viewer_name, dc.body, dc.created_at
        FROM deck_comments dc
        JOIN deck_images di ON di.id = dc.deck_image_id
        WHERE di.deck_id = ?
        ORDER BY dc.created_at ASC
    ''', (deck_id,)).fetchall()

    frames = {}
    def bucket(deck_image_id):
        return frames.setdefault(str(deck_image_id), {
            'pick_count': 0, 'pickers': [], 'picked_by_me': False, 'comments': []
        })

    for r in pick_rows:
        b = bucket(r['deck_image_id'])
        b['pick_count'] += 1
        b['pickers'].append(r['viewer_name'])
        if viewer_token and r['viewer_token'] == viewer_token:
            b['picked_by_me'] = True

    for r in comment_rows:
        b = bucket(r['deck_image_id'])
        b['comments'].append({
            'id': r['id'], 'viewer_name': r['viewer_name'],
            'body': r['body'], 'created_at': r['created_at']
        })

    # Most-picked first (Ryan's call): the frame that won the room is the
    # first thing the owner sees, not whichever happens to sort first in the
    # deck. Ties keep insertion order, which is already earliest-pick-first
    # since pick_rows came back ASC by created_at and dict insertion order —
    # and therefore Python's stable sort — preserves that.
    ranked_ids = [
        int(k) for k, v in sorted(frames.items(), key=lambda kv: -kv[1]['pick_count'])
        if v['pick_count'] > 0 or v['comments']
    ]

    return {
        'frames': frames,
        'ranked_deck_image_ids': ranked_ids,
        'total_picks': len(pick_rows),
        'total_comments': len(comment_rows),
    }

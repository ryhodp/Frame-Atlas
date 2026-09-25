"""routes_decks.py — logged-in deck routes as a Flask Blueprint (Day 41 / Phase 3).

Decks, scenes, members + invites, the photos in a deck (add / move / copy /
remove / storyboard note / reorder), scene reorder, the activity feed, the
owner's controls for a share link and its client feedback, and the PDF
lookbook export. Everything here needs a login; the public side of a share
link is routes_share.py, and the helpers both use are decks_common.py.

Every route body is character-for-character what it was in app.py — only
@app.route became @bp.route, and every URL path is byte-identical.
"""
import base64
import secrets

from flask import Blueprint, jsonify, request, send_file, session

from core import get_db
from pdf_export import build_deck_pdf, pdf_download_name, LAYOUTS as PDF_LAYOUTS
from decks_common import (
    _display_name, _deck_access, touch_deck, log_deck_activity,
    _deck_payload, _deck_feedback_payload,
)

bp = Blueprint('decks', __name__)


@bp.route('/api/decks', methods=['GET'])
def list_decks():
    conn = get_db()
    c = conn.cursor()
    deck_rows = c.execute('''
        SELECT d.id, d.name, d.created_at, d.user_id, (d.user_id = ?) AS is_owner
        FROM decks d
        WHERE d.user_id = ? OR d.id IN (SELECT deck_id FROM deck_members WHERE user_id = ?)
        ORDER BY d.created_at DESC
    ''', (session['user_id'], session['user_id'], session['user_id'])).fetchall()

    decks_out = []
    for d in deck_rows:
        image_count = c.execute(
            'SELECT COUNT(DISTINCT image_id) FROM deck_images WHERE deck_id = ?', (d['id'],)
        ).fetchone()[0]

        # Most-recently-added distinct images: walk deck_images newest-first
        # and keep the first (most recent) row we see per image_id.
        preview_thumbnails = []
        seen_image_ids = set()
        for di in c.execute(
            'SELECT image_id FROM deck_images WHERE deck_id = ? ORDER BY id DESC', (d['id'],)
        ).fetchall():
            if di['image_id'] in seen_image_ids:
                continue
            seen_image_ids.add(di['image_id'])
            img_row = c.execute('SELECT thumbnail_blob FROM images WHERE id = ?', (di['image_id'],)).fetchone()
            if img_row:
                thumb_b64 = base64.b64encode(img_row['thumbnail_blob']).decode('utf-8')
                preview_thumbnails.append(f'data:image/jpeg;base64,{thumb_b64}')
            if len(preview_thumbnails) >= 4:
                break

        owner_name = None
        if not d['is_owner']:
            owner_row = c.execute('SELECT id, username, email FROM users WHERE id = ?', (d['user_id'],)).fetchone()
            owner_name = _display_name(owner_row)

        decks_out.append({
            'id': d['id'],
            'name': d['name'],
            'created_at': d['created_at'],
            'image_count': image_count,
            'preview_thumbnails': preview_thumbnails,
            'is_owner': bool(d['is_owner']),
            'owner_name': owner_name,
        })

    conn.close()
    return jsonify(decks_out)

@bp.route('/api/decks', methods=['POST'])
def create_deck():
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400

    conn = get_db()
    c = conn.cursor()
    # feedback_enabled = 1 explicitly, overriding the column's own DEFAULT 0
    # (that default exists so decks that predate V42 come back OFF — see the
    # migration above). Every deck created from here on starts with feedback on.
    c.execute('INSERT INTO decks (user_id, name, feedback_enabled) VALUES (?, ?, 1)', (session['user_id'], name))
    deck_id = c.lastrowid
    created_at = c.execute('SELECT created_at FROM decks WHERE id = ?', (deck_id,)).fetchone()['created_at']
    conn.commit()
    conn.close()

    return jsonify({
        'id': deck_id,
        'name': name,
        'created_at': created_at,
        'image_count': 0,
        'preview_thumbnails': [],
        'feedback_enabled': True
    })

@bp.route('/api/decks/<int:deck_id>', methods=['PATCH'])
def update_deck(deck_id):
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400

    conn = get_db()
    c = conn.cursor()
    if not c.execute('SELECT 1 FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    c.execute('UPDATE decks SET name = ? WHERE id = ?', (name, deck_id))
    log_deck_activity(c, deck_id, 'renamed', name)
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@bp.route('/api/decks/<int:deck_id>', methods=['DELETE'])
def delete_deck(deck_id):
    conn = get_db()
    c = conn.cursor()
    if not c.execute('SELECT 1 FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    # V42: picks/comments key off deck_image_id, so they have to go BEFORE
    # deck_images itself — after that delete there's nothing left to join on.
    c.execute('DELETE FROM deck_picks WHERE deck_image_id IN (SELECT id FROM deck_images WHERE deck_id = ?)', (deck_id,))
    c.execute('DELETE FROM deck_comments WHERE deck_image_id IN (SELECT id FROM deck_images WHERE deck_id = ?)', (deck_id,))
    c.execute('DELETE FROM deck_images WHERE deck_id = ?', (deck_id,))
    c.execute('DELETE FROM scenes WHERE deck_id = ?', (deck_id,))
    c.execute('DELETE FROM deck_members WHERE deck_id = ?', (deck_id,))
    c.execute('DELETE FROM deck_activity WHERE deck_id = ?', (deck_id,))
    c.execute('DELETE FROM decks WHERE id = ?', (deck_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@bp.route('/api/decks/<int:deck_id>', methods=['GET'])
def get_deck(deck_id):
    conn = get_db()
    c = conn.cursor()
    deck_row, is_owner = _deck_access(c, deck_id, session['user_id'])
    if not deck_row:
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    payload = _deck_payload(c, deck_row)
    payload['is_owner'] = is_owner
    owner_row = c.execute('SELECT id, username, email FROM users WHERE id = ?', (deck_row['user_id'],)).fetchone()
    payload['owner_name'] = _display_name(owner_row)
    conn.close()
    return jsonify(payload)

@bp.route('/api/decks/<int:deck_id>/members', methods=['GET'])
def list_deck_members(deck_id):
    conn = get_db()
    c = conn.cursor()
    if not c.execute('SELECT 1 FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    rows = c.execute('''
        SELECT u.id, u.username, u.email, dm.added_at, COALESCE(dm.permission, 'viewer') as permission
        FROM deck_members dm JOIN users u ON u.id = dm.user_id
        WHERE dm.deck_id = ? ORDER BY dm.added_at ASC
    ''', (deck_id,)).fetchall()
    conn.close()
    return jsonify([
        {'user_id': r['id'], 'name': _display_name(r), 'email': r['email'], 'permission': r['permission'], 'added_at': r['added_at']}
        for r in rows
    ])

@bp.route('/api/decks/<int:deck_id>/invite', methods=['POST'])
def invite_to_deck(deck_id):
    """Adds an existing Frame Atlas user as a view-only member by email —
    there's no outgoing email sent, this just looks up an account that
    already exists (same as any other admin-lookup pattern in this app)."""
    data = request.get_json(force=True) or {}
    email = (data.get('email') or '').strip().lower()
    if not email:
        return jsonify({'error': 'email is required'}), 400

    conn = get_db()
    c = conn.cursor()
    if not c.execute('SELECT 1 FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    target = c.execute('SELECT id, username, email FROM users WHERE LOWER(email) = ?', (email,)).fetchone()
    if not target:
        conn.close()
        return jsonify({
            'error': 'no_account',
            'message': "No Frame Atlas account uses that email — send them the invite link instead."
        }), 404
    if target['id'] == session['user_id']:
        conn.close()
        return jsonify({'error': 'That is your own account.'}), 400

    c.execute('INSERT OR IGNORE INTO deck_members (deck_id, user_id) VALUES (?, ?)', (deck_id, target['id']))
    log_deck_activity(c, deck_id, 'invited', _display_name(target))
    conn.commit()
    conn.close()
    return jsonify({'user_id': target['id'], 'name': _display_name(target), 'email': target['email']})

@bp.route('/api/decks/<int:deck_id>/members/<int:user_id>', methods=['DELETE'])
def remove_deck_member(deck_id, user_id):
    conn = get_db()
    c = conn.cursor()
    if not c.execute('SELECT 1 FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    c.execute('DELETE FROM deck_members WHERE deck_id = ? AND user_id = ?', (deck_id, user_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@bp.route('/api/decks/<int:deck_id>/invite-link', methods=['POST', 'DELETE'])
def deck_invite_link(deck_id):
    """A reusable "join as a viewer" link — separate from the anonymous,
    loginless /share/<token> link. Opening this one requires being logged in
    and turns into a permanent deck_members row (visible to the owner,
    revocable one at a time), rather than just viewing without an account."""
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        'SELECT invite_token FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    if request.method == 'DELETE':
        c.execute('UPDATE decks SET invite_token = NULL WHERE id = ?', (deck_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'invite_token': None})

    token = row['invite_token']
    if not token:
        token = secrets.token_urlsafe(16)
        c.execute('UPDATE decks SET invite_token = ? WHERE id = ?', (token, deck_id))
        conn.commit()
    conn.close()
    return jsonify({'invite_token': token, 'invite_path': f'/invite/{token}'})

@bp.route('/api/decks/invite/<token>/accept', methods=['POST'])
def accept_deck_invite(token):
    conn = get_db()
    c = conn.cursor()
    deck_row = c.execute('SELECT id, name, user_id FROM decks WHERE invite_token = ?', (token,)).fetchone()
    if not deck_row:
        conn.close()
        return jsonify({'error': 'Invite link not found or revoked'}), 404

    if deck_row['user_id'] == session['user_id']:
        conn.close()
        return jsonify({'deck_id': deck_row['id'], 'name': deck_row['name']})

    already = c.execute(
        'SELECT 1 FROM deck_members WHERE deck_id = ? AND user_id = ?', (deck_row['id'], session['user_id'])
    ).fetchone()
    c.execute('INSERT OR IGNORE INTO deck_members (deck_id, user_id) VALUES (?, ?)', (deck_row['id'], session['user_id']))
    if not already:
        me = c.execute('SELECT id, username, email FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        log_deck_activity(c, deck_row['id'], 'joined', _display_name(me))
    conn.commit()
    conn.close()
    return jsonify({'deck_id': deck_row['id'], 'name': deck_row['name']})

@bp.route('/api/decks/<int:deck_id>/activity', methods=['GET'])
def deck_activity(deck_id):
    conn = get_db()
    c = conn.cursor()
    deck_row, _is_owner = _deck_access(c, deck_id, session['user_id'])
    if not deck_row:
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    rows = c.execute('''
        SELECT da.action, da.detail, da.created_at, u.username, u.email
        FROM deck_activity da JOIN users u ON u.id = da.user_id
        WHERE da.deck_id = ? ORDER BY da.id DESC LIMIT 50
    ''', (deck_id,)).fetchall()
    conn.close()
    return jsonify([
        {'action': r['action'], 'detail': r['detail'], 'created_at': r['created_at'], 'actor': _display_name(r)}
        for r in rows
    ])

@bp.route('/api/scenes', methods=['POST'])
def create_scene():
    data = request.get_json(force=True) or {}
    deck_id = data.get('deck_id')
    name = (data.get('name') or '').strip()

    conn = get_db()
    c = conn.cursor()
    if not isinstance(deck_id, int) or not c.execute(
        'SELECT 1 FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])
    ).fetchone():
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404
    if not name:
        conn.close()
        return jsonify({'error': 'name is required'}), 400

    next_order = c.execute(
        'SELECT COALESCE(MAX(sort_order), -1) + 1 FROM scenes WHERE deck_id = ?', (deck_id,)
    ).fetchone()[0]
    c.execute('INSERT INTO scenes (deck_id, name, sort_order) VALUES (?, ?, ?)', (deck_id, name, next_order))
    scene_id = c.lastrowid
    log_deck_activity(c, deck_id, 'added_scene', name)
    conn.commit()
    conn.close()

    return jsonify({'id': scene_id, 'name': name, 'sort_order': next_order, 'deck_id': deck_id})

@bp.route('/api/scenes/<int:scene_id>', methods=['PATCH'])
def update_scene(scene_id):
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()

    conn = get_db()
    c = conn.cursor()
    scene_row = c.execute(
        'SELECT s.deck_id FROM scenes s JOIN decks d ON d.id = s.deck_id WHERE s.id = ? AND d.user_id = ?',
        (scene_id, session['user_id'])
    ).fetchone()
    if not scene_row:
        conn.close()
        return jsonify({'error': 'Scene not found'}), 404
    if not name:
        conn.close()
        return jsonify({'error': 'name is required'}), 400

    c.execute('UPDATE scenes SET name = ? WHERE id = ?', (name, scene_id))
    log_deck_activity(c, scene_row['deck_id'], 'renamed_scene', name)
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@bp.route('/api/scenes/<int:scene_id>', methods=['DELETE'])
def delete_scene(scene_id):
    conn = get_db()
    c = conn.cursor()
    scene_row = c.execute(
        'SELECT s.deck_id, s.name FROM scenes s JOIN decks d ON d.id = s.deck_id WHERE s.id = ? AND d.user_id = ?',
        (scene_id, session['user_id'])
    ).fetchone()
    if not scene_row:
        conn.close()
        return jsonify({'error': 'Scene not found'}), 404

    c.execute('DELETE FROM deck_images WHERE scene_id = ?', (scene_id,))
    c.execute('DELETE FROM scenes WHERE id = ?', (scene_id,))
    log_deck_activity(c, scene_row['deck_id'], 'deleted_scene', scene_row['name'])
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@bp.route('/api/decks/<int:deck_id>/scenes/reorder', methods=['POST'])
def reorder_scenes(deck_id):
    """Persists a new scene order within a deck. Expects the COMPLETE ordered
    list of the deck's scene ids — position in the list becomes sort_order.
    Mirrors reorder_deck_images below: same validation shape, and the same
    touch_deck()-not-log_deck_activity() call, since reordering is the one
    mutation with no activity-feed entry."""
    data = request.get_json(force=True) or {}
    scene_ids = data.get('scene_ids')

    if not isinstance(scene_ids, list) or not scene_ids or not all(isinstance(i, int) for i in scene_ids):
        return jsonify({'error': 'scene_ids must be a non-empty list of ints'}), 400

    conn = get_db()
    c = conn.cursor()
    if not c.execute('SELECT 1 FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    rows = c.execute('SELECT id FROM scenes WHERE deck_id = ?', (deck_id,)).fetchall()
    current_ids = {r['id'] for r in rows}

    if set(scene_ids) != current_ids or len(scene_ids) != len(current_ids):
        conn.close()
        return jsonify({'error': 'scene_ids must be exactly the scenes in this deck'}), 400

    for position, scene_id in enumerate(scene_ids):
        c.execute('UPDATE scenes SET sort_order = ? WHERE id = ?', (position, scene_id))
    touch_deck(c, deck_id)
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@bp.route('/api/decks/<int:deck_id>/images', methods=['POST'])
def add_images_to_deck(deck_id):
    data = request.get_json(force=True) or {}
    image_ids = data.get('image_ids')

    conn = get_db()
    c = conn.cursor()
    if not c.execute('SELECT 1 FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404
    if not isinstance(image_ids, list) or not image_ids or not all(isinstance(i, int) for i in image_ids):
        conn.close()
        return jsonify({'error': 'image_ids must be a non-empty list of ints'}), 400

    next_order = c.execute(
        'SELECT COALESCE(MAX(storyboard_order), -1) + 1 FROM deck_images WHERE deck_id = ? AND scene_id IS NULL',
        (deck_id,)
    ).fetchone()[0]

    added = 0
    already_in_deck = 0
    invalid_ids = []

    for image_id in image_ids:
        if not c.execute(
            'SELECT 1 FROM images WHERE id = ? AND user_id = ?', (image_id, session['user_id'])
        ).fetchone():
            invalid_ids.append(image_id)
            continue

        exists = c.execute(
            'SELECT 1 FROM deck_images WHERE deck_id = ? AND image_id = ? AND scene_id IS NULL',
            (deck_id, image_id)
        ).fetchone()
        if exists:
            already_in_deck += 1
            continue

        c.execute('''
            INSERT INTO deck_images (deck_id, scene_id, image_id, storyboard_order, storyboard_note)
            VALUES (?, NULL, ?, ?, NULL)
        ''', (deck_id, image_id, next_order))
        next_order += 1
        added += 1

    if added:
        log_deck_activity(c, deck_id, 'added_photos', f"{added} photo{'s' if added != 1 else ''}")
    conn.commit()
    conn.close()
    return jsonify({'added': added, 'already_in_deck': already_in_deck, 'invalid_ids': invalid_ids})

@bp.route('/api/deck-images/<int:deck_image_id>/move', methods=['POST'])
def move_deck_image(deck_image_id):
    data = request.get_json(force=True) or {}
    target_scene_id = data.get('target_scene_id')

    conn = get_db()
    c = conn.cursor()
    row = c.execute('''
        SELECT di.id, di.deck_id, di.scene_id, di.image_id, di.storyboard_note
        FROM deck_images di JOIN decks d ON d.id = di.deck_id
        WHERE di.id = ? AND d.user_id = ?
    ''', (deck_image_id, session['user_id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'deck image not found'}), 404

    if target_scene_id is not None:
        valid_target = c.execute(
            'SELECT 1 FROM scenes WHERE id = ? AND deck_id = ?', (target_scene_id, row['deck_id'])
        ).fetchone()
        if not valid_target:
            conn.close()
            return jsonify({'error': 'scene not found in this deck'}), 400

    current_scene_id = row['scene_id']

    if target_scene_id == current_scene_id:
        # Dropped back where it started (e.g. an accidental tiny drag) —
        # do nothing, and especially don't fall through to the copy branch,
        # which would duplicate the photo inside its own scene.
        conn.close()
        return jsonify({'action': 'moved'})

    if target_scene_id is None:
        # Dropping into Unsorted: simple move.
        c.execute('UPDATE deck_images SET scene_id = NULL WHERE id = ?', (deck_image_id,))
        log_deck_activity(c, row['deck_id'], 'moved_photo', 'Unsorted')
        conn.commit()
        conn.close()
        return jsonify({'action': 'moved'})

    target_name = c.execute('SELECT name FROM scenes WHERE id = ?', (target_scene_id,)).fetchone()['name']

    if current_scene_id is None:
        # Moving out of Unsorted into a named scene: simple move.
        c.execute('UPDATE deck_images SET scene_id = ? WHERE id = ?', (target_scene_id, deck_image_id))
        log_deck_activity(c, row['deck_id'], 'moved_photo', target_name)
        conn.commit()
        conn.close()
        return jsonify({'action': 'moved'})

    # Scene-to-scene: copy. Leave the original row untouched, insert a new
    # row in the target scene (the image now sits in both scenes).
    next_order = c.execute(
        'SELECT COALESCE(MAX(storyboard_order), -1) + 1 FROM deck_images WHERE deck_id = ? AND scene_id = ?',
        (row['deck_id'], target_scene_id)
    ).fetchone()[0]
    c.execute('''
        INSERT INTO deck_images (deck_id, scene_id, image_id, storyboard_order, storyboard_note)
        VALUES (?, ?, ?, ?, ?)
    ''', (row['deck_id'], target_scene_id, row['image_id'], next_order, row['storyboard_note']))
    new_deck_image_id = c.lastrowid
    log_deck_activity(c, row['deck_id'], 'copied_photo', target_name)
    conn.commit()
    conn.close()
    return jsonify({'action': 'copied', 'new_deck_image_id': new_deck_image_id})

@bp.route('/api/deck-images/<int:deck_image_id>', methods=['DELETE'])
def delete_deck_image(deck_image_id):
    conn = get_db()
    c = conn.cursor()
    row = c.execute('''
        SELECT di.deck_id FROM deck_images di JOIN decks d ON d.id = di.deck_id
        WHERE di.id = ? AND d.user_id = ?
    ''', (deck_image_id, session['user_id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'deck image not found'}), 404

    # V42: drop any picks/comments left on this frame — otherwise re-adding
    # the same underlying image to the deck later would land on a fresh
    # deck_images row with orphaned feedback pointing at the old one.
    c.execute('DELETE FROM deck_picks WHERE deck_image_id = ?', (deck_image_id,))
    c.execute('DELETE FROM deck_comments WHERE deck_image_id = ?', (deck_image_id,))
    c.execute('DELETE FROM deck_images WHERE id = ?', (deck_image_id,))
    log_deck_activity(c, row['deck_id'], 'removed_photo')
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ============================================================================
# STORYBOARD + SHARE LINKS
# ============================================================================

@bp.route('/api/deck-images/<int:deck_image_id>/note', methods=['POST'])
def set_deck_image_note(deck_image_id):
    data = request.get_json(force=True) or {}
    note = data.get('note')
    if note is not None and not isinstance(note, str):
        return jsonify({'error': 'note must be a string or null'}), 400
    if isinstance(note, str):
        note = note.strip() or None  # empty string clears the note

    conn = get_db()
    c = conn.cursor()
    row = c.execute('''
        SELECT di.deck_id FROM deck_images di JOIN decks d ON d.id = di.deck_id
        WHERE di.id = ? AND d.user_id = ?
    ''', (deck_image_id, session['user_id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'deck image not found'}), 404

    c.execute('UPDATE deck_images SET storyboard_note = ? WHERE id = ?', (note, deck_image_id))
    log_deck_activity(c, row['deck_id'], 'edited_note')
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'note': note})

@bp.route('/api/decks/<int:deck_id>/reorder', methods=['POST'])
def reorder_deck_images(deck_id):
    """Persists a new storyboard order for one section (a scene, or Unsorted
    when scene_id is null). Expects the COMPLETE ordered list of that section's
    deck_image_ids — position in the list becomes storyboard_order."""
    data = request.get_json(force=True) or {}
    scene_id = data.get('scene_id')  # null = Unsorted
    ordered_ids = data.get('deck_image_ids')

    if not isinstance(ordered_ids, list) or not ordered_ids or not all(isinstance(i, int) for i in ordered_ids):
        return jsonify({'error': 'deck_image_ids must be a non-empty list of ints'}), 400

    conn = get_db()
    c = conn.cursor()
    if not c.execute('SELECT 1 FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    if scene_id is None:
        rows = c.execute(
            'SELECT id FROM deck_images WHERE deck_id = ? AND scene_id IS NULL', (deck_id,)
        ).fetchall()
    else:
        rows = c.execute(
            'SELECT id FROM deck_images WHERE deck_id = ? AND scene_id = ?', (deck_id, scene_id)
        ).fetchall()
    current_ids = {r['id'] for r in rows}

    if set(ordered_ids) != current_ids or len(ordered_ids) != len(current_ids):
        conn.close()
        return jsonify({'error': 'deck_image_ids must be exactly the ids in this section'}), 400

    for position, di_id in enumerate(ordered_ids):
        c.execute('UPDATE deck_images SET storyboard_order = ? WHERE id = ?', (position, di_id))
    # Reordering is the one mutation with no activity-feed entry, so it has to
    # bump the timestamp itself.
    touch_deck(c, deck_id)
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'updated': len(ordered_ids)})

@bp.route('/api/decks/<int:deck_id>/share', methods=['POST', 'DELETE'])
def deck_share_token(deck_id):
    """POST creates (or returns the existing) share token for a deck.
    DELETE revokes it — the old link stops working immediately, and a later
    POST mints a brand new token rather than reviving the old one.

    Share links are view-only, deliberately. V23 accepted a ?permission=editor
    flag and echoed it back, but never stored it — everyone who joined landed
    on 'viewer' regardless, so the flag was pure decoration. Rather than make
    it real, it's gone: a share link is a URL, and anyone who ends up holding
    it should not be able to rewrite the deck. Granting edit rights is what
    the named-invite flow (/api/decks/<id>/invite) is for."""
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        'SELECT share_token, id FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    if request.method == 'DELETE':
        c.execute('UPDATE decks SET share_token = NULL WHERE id = ?', (deck_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'share_token': None})

    token = row['share_token']
    if not token:
        token = secrets.token_urlsafe(16)
        c.execute('UPDATE decks SET share_token = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (token, deck_id))
        conn.commit()
    conn.close()
    return jsonify({'share_token': token, 'share_path': f'/share/{token}', 'permission': 'viewer'})

@bp.route('/api/decks/join/<token>', methods=['POST'])
def join_deck_via_link(token):
    """Join a deck via its public share link. Must be logged in.
    Always joins as a viewer — see deck_share_token() for why links don't
    grant edit rights."""
    conn = get_db()
    c = conn.cursor()

    deck_row = c.execute(
        'SELECT id, share_token FROM decks WHERE share_token = ?', (token,)
    ).fetchone()
    if not deck_row:
        conn.close()
        return jsonify({'error': 'Share link not found or revoked'}), 404

    user_id = session.get('user_id')
    if not user_id:
        conn.close()
        return jsonify({'error': 'Must be logged in'}), 401

    # Check if already a member
    existing = c.execute(
        'SELECT permission FROM deck_members WHERE deck_id = ? AND user_id = ?',
        (deck_row['id'], user_id)
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({'message': 'Already a member', 'permission': existing['permission']}), 200

    c.execute(
        'INSERT INTO deck_members (deck_id, user_id, permission) VALUES (?, ?, ?)',
        (deck_row['id'], user_id, 'viewer')
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'deck_id': deck_row['id']}), 201

@bp.route('/api/decks/<int:deck_id>/feedback', methods=['GET'])
def get_deck_feedback(deck_id):
    """Owner-only feedback summary. Reuses _deck_feedback_payload so this can
    never show the owner something different from what viewers themselves see."""
    conn = get_db()
    c = conn.cursor()
    if not c.execute('SELECT 1 FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404
    payload = _deck_feedback_payload(c, deck_id)
    conn.close()
    return jsonify(payload)

@bp.route('/api/decks/<int:deck_id>/feedback-enabled', methods=['POST'])
def set_deck_feedback_enabled(deck_id):
    """Owner-only on/off switch — lives in the Share panel on the frontend
    (feedback only matters once a link exists, so the switch lives with the
    thing that creates the link). Deliberately does NOT call touch_deck() or
    log_deck_activity(): this isn't a content change crew members or the
    offline "New changes" banner need to know about — same reasoning as the
    V40 PDF export being read-only."""
    data = request.get_json(force=True) or {}
    enabled = bool(data.get('enabled'))
    conn = get_db()
    c = conn.cursor()
    if not c.execute('SELECT 1 FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404
    c.execute('UPDATE decks SET feedback_enabled = ? WHERE id = ?', (1 if enabled else 0, deck_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'feedback_enabled': enabled})

@bp.route('/api/decks/<int:deck_id>/comments/<int:comment_id>', methods=['DELETE'])
def delete_deck_comment(deck_id, comment_id):
    """Owner-only. The share token is unguessable, but anyone holding it can
    post — this delete is the pressure valve the product plan calls for."""
    conn = get_db()
    c = conn.cursor()
    if not c.execute('SELECT 1 FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404
    row = c.execute('''
        SELECT dc.id FROM deck_comments dc JOIN deck_images di ON di.id = dc.deck_image_id
        WHERE dc.id = ? AND di.deck_id = ?
    ''', (comment_id, deck_id)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Comment not found'}), 404
    c.execute('DELETE FROM deck_comments WHERE id = ?', (comment_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@bp.route('/api/decks/<int:deck_id>/export.pdf')
def export_deck_pdf(deck_id):
    """Day 22 (V40): render a deck as a PDF lookbook.

    ?layout=full   one photo per page, scene title cards — the client pitch doc
    ?layout=grid   contact sheet, 6 frames a page — the crew handout
    ?include_unsorted=1|0   whether the Unsorted bucket ships as a final section

    Owner-only (the same 404-not-403 idiom the rest of the deck routes use).
    All layout lives in backend/pdf_export.py; this only reads rows. It writes
    nothing — in particular NOT log_deck_activity(), which would bump
    decks.updated_at and light up the frontend's "New changes" banner for an
    export that changed nothing.

    Deliberately does not reuse _deck_payload(): that base64-encodes every
    thumbnail into JSON (pure waste when the bytes go straight into a PDF) and
    its single global ORDER BY doesn't give correct per-scene ordering.
    """
    layout = (request.args.get('layout') or 'full').strip().lower()
    if layout not in PDF_LAYOUTS:
        return jsonify({'error': f"layout must be one of {', '.join(PDF_LAYOUTS)}"}), 400
    include_unsorted = (request.args.get('include_unsorted') or '1').strip().lower() not in ('0', 'false', 'no')

    conn = get_db()
    c = conn.cursor()
    deck_row = c.execute(
        'SELECT id, name FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])
    ).fetchone()
    if not deck_row:
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    scene_rows = c.execute(
        'SELECT id, name FROM scenes WHERE deck_id = ? ORDER BY sort_order ASC, id ASC', (deck_id,)
    ).fetchall()
    # The JOIN quietly drops any deck_images row whose image is gone.
    photo_rows = c.execute('''
        SELECT di.id AS deck_image_id, di.scene_id, di.storyboard_note,
               i.filename, i.thumbnail_blob
        FROM deck_images di
        JOIN images i ON i.id = di.image_id
        WHERE di.deck_id = ?
        ORDER BY CASE WHEN di.storyboard_order IS NULL THEN 1 ELSE 0 END,
                 di.storyboard_order ASC, di.id ASC
    ''', (deck_id,)).fetchall()
    deck = {'id': deck_row['id'], 'name': deck_row['name']}
    conn.close()

    buckets = {}
    for row in photo_rows:
        buckets.setdefault(row['scene_id'], []).append(dict(row))

    sections = [{'name': s['name'], 'images': buckets.get(s['id'], [])} for s in scene_rows]
    if include_unsorted and buckets.get(None):
        sections.append({'name': None, 'images': buckets[None]})

    fh = build_deck_pdf(deck, sections, layout=layout)
    return send_file(fh, mimetype='application/pdf', as_attachment=True,
                     download_name=pdf_download_name(deck['name']))

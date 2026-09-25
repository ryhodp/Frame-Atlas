"""routes_share.py — the PUBLIC share-link routes as a Flask Blueprint (Day 41 / Phase 3).

Every route in this file is reachable WITHOUT logging in by anyone holding a
deck's share token (core.PUBLIC_API_ROUTES / the login gate let /api/share/*
through). Keeping them in one small file is deliberate: this is the app's
whole public surface, so it's the file to read first in any security review.

  GET    /api/share/<token>              the deck itself (base64 thumbnails —
                                         there's no login to gate a thumb URL)
  GET    /api/share/<token>/feedback     picks + comments (works even when
                                         feedback is off: returns enabled=false)
  POST   /api/share/<token>/picks        \
  DELETE /api/share/<token>/picks         > all gated by _feedback_deck_for_token
  POST   /api/share/<token>/comments     /  (token AND feedback_enabled)

The owner's side of the same feature (feedback panel, on/off toggle, comment
delete, creating/revoking the link) is in routes_decks.py.

Every route body is character-for-character what it was in app.py.
"""
import re

from flask import Blueprint, jsonify, request

from core import get_db
from decks_common import _deck_payload, _deck_feedback_payload

bp = Blueprint('share', __name__)


@bp.route('/api/share/<token>')
def get_shared_deck(token):
    """Public read-only deck view — no login, the token IS the access grant.
    Viewers get thumbnails only (they're embedded in the payload as data URIs —
    public=True, since there's no login here to gate a cacheable URL behind,
    see build_image_dict()'s docstring); none of the full-res, edit, or
    delete endpoints check tokens, so a shared link exposes nothing beyond
    what this one endpoint returns."""
    conn = get_db()
    c = conn.cursor()
    deck_row = c.execute(
        'SELECT id, name, created_at, updated_at, share_token, user_id, feedback_enabled '
        'FROM decks WHERE share_token = ?', (token,)
    ).fetchone()
    if not deck_row:
        conn.close()
        return jsonify({'error': 'Share link not found or revoked'}), 404

    payload = _deck_payload(c, deck_row, public=True)
    conn.close()
    return jsonify(payload)

# ============================================================================
# DAY 24 (V42): CLIENT FEEDBACK — anonymous picks + comments on a share link
# ============================================================================

COMMENT_MAX_LEN = 2000

VIEWER_NAME_MAX_LEN = 60

# The viewer's browser generates this (crypto.randomUUID()) and echoes it on
# every feedback write — never a login, just a way to recognize "the same
# browser came back" so a pick can be toggled and can't be inflated by a
# double-click or a retried request. Loose enough to accept a UUID or any
# other reasonable random string; tight enough that it can't be used to
# smuggle SQL-shaped or oversized junk into a TEXT column with no other
# validation.
VIEWER_TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{8,128}$')

def _valid_viewer_token(token):
    return isinstance(token, str) and bool(VIEWER_TOKEN_RE.match(token))

def _clean_viewer_name(raw):
    name = (raw or '').strip()
    if not name or len(name) > VIEWER_NAME_MAX_LEN:
        return None
    return name

def _feedback_deck_for_token(c, token):
    """The deck row for a share token, but ONLY if feedback is turned on —
    every public write endpoint below gates through this one function, so a
    future third condition (e.g. a moderation pause) only has to change here."""
    return c.execute(
        'SELECT id FROM decks WHERE share_token = ? AND feedback_enabled = 1', (token,)
    ).fetchone()

@bp.route('/api/share/<token>/feedback', methods=['GET'])
def get_share_feedback(token):
    """Public. Everyone holding the link sees the same picks and comments —
    Ryan's call, collaborative, one conversation for the whole agency side —
    except `picked_by_me`, which is scoped to whichever browser is asking."""
    viewer_token = request.headers.get('X-FA-Viewer') or request.args.get('viewer_token')
    if not _valid_viewer_token(viewer_token):
        viewer_token = None
    conn = get_db()
    c = conn.cursor()
    deck_row = c.execute('SELECT id, feedback_enabled FROM decks WHERE share_token = ?', (token,)).fetchone()
    if not deck_row:
        conn.close()
        return jsonify({'error': 'Share link not found or revoked'}), 404
    if not deck_row['feedback_enabled']:
        conn.close()
        return jsonify({'enabled': False, 'frames': {}, 'ranked_deck_image_ids': [],
                        'total_picks': 0, 'total_comments': 0})
    payload = _deck_feedback_payload(c, deck_row['id'], viewer_token=viewer_token)
    payload['enabled'] = True
    conn.close()
    return jsonify(payload)

@bp.route('/api/share/<token>/picks', methods=['POST'])
def add_share_pick(token):
    """Public, idempotent: picking a frame you already picked (same browser)
    just refreshes the display name in case it was retyped — it does not
    create a second pick or error."""
    data = request.get_json(force=True) or {}
    deck_image_id = data.get('deck_image_id')
    viewer_token = data.get('viewer_token')
    viewer_name = _clean_viewer_name(data.get('viewer_name'))
    if not isinstance(deck_image_id, int):
        return jsonify({'error': 'deck_image_id is required'}), 400
    if not _valid_viewer_token(viewer_token):
        return jsonify({'error': 'viewer_token is required'}), 400
    if not viewer_name:
        return jsonify({'error': 'A name is required'}), 400

    conn = get_db()
    c = conn.cursor()
    deck_row = _feedback_deck_for_token(c, token)
    if not deck_row:
        conn.close()
        return jsonify({'error': 'Feedback is not open on this lookbook'}), 404
    if not c.execute('SELECT 1 FROM deck_images WHERE id = ? AND deck_id = ?',
                     (deck_image_id, deck_row['id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Frame not found in this deck'}), 404

    c.execute('''
        INSERT INTO deck_picks (deck_image_id, viewer_token, viewer_name)
        VALUES (?, ?, ?)
        ON CONFLICT(deck_image_id, viewer_token) DO UPDATE SET viewer_name = excluded.viewer_name
    ''', (deck_image_id, viewer_token, viewer_name))
    conn.commit()
    count = c.execute('SELECT COUNT(*) FROM deck_picks WHERE deck_image_id = ?', (deck_image_id,)).fetchone()[0]
    conn.close()
    return jsonify({'picked': True, 'pick_count': count})

@bp.route('/api/share/<token>/picks', methods=['DELETE'])
def remove_share_pick(token):
    """Public, idempotent: un-picking a frame that was never picked (or was
    already un-picked) is a no-op, not an error."""
    data = request.get_json(force=True) or {}
    deck_image_id = data.get('deck_image_id')
    viewer_token = data.get('viewer_token')
    if not isinstance(deck_image_id, int) or not _valid_viewer_token(viewer_token):
        return jsonify({'error': 'deck_image_id and viewer_token are required'}), 400

    conn = get_db()
    c = conn.cursor()
    deck_row = _feedback_deck_for_token(c, token)
    if not deck_row:
        conn.close()
        return jsonify({'error': 'Feedback is not open on this lookbook'}), 404

    c.execute('''
        DELETE FROM deck_picks WHERE deck_image_id = ? AND viewer_token = ?
        AND deck_image_id IN (SELECT id FROM deck_images WHERE deck_id = ?)
    ''', (deck_image_id, viewer_token, deck_row['id']))
    conn.commit()
    count = c.execute('SELECT COUNT(*) FROM deck_picks WHERE deck_image_id = ?', (deck_image_id,)).fetchone()[0]
    conn.close()
    return jsonify({'picked': False, 'pick_count': count})

@bp.route('/api/share/<token>/comments', methods=['POST'])
def add_share_comment(token):
    """Public. Every submission is its own row — comments are never
    deduped or toggled the way picks are."""
    data = request.get_json(force=True) or {}
    deck_image_id = data.get('deck_image_id')
    viewer_token = data.get('viewer_token')
    viewer_name = _clean_viewer_name(data.get('viewer_name'))
    body = (data.get('body') or '').strip()
    if not isinstance(deck_image_id, int):
        return jsonify({'error': 'deck_image_id is required'}), 400
    if not _valid_viewer_token(viewer_token):
        return jsonify({'error': 'viewer_token is required'}), 400
    if not viewer_name:
        return jsonify({'error': 'A name is required'}), 400
    if not body:
        return jsonify({'error': 'Comment cannot be empty'}), 400
    if len(body) > COMMENT_MAX_LEN:
        return jsonify({'error': f'Comment is too long (max {COMMENT_MAX_LEN} characters)'}), 400

    conn = get_db()
    c = conn.cursor()
    deck_row = _feedback_deck_for_token(c, token)
    if not deck_row:
        conn.close()
        return jsonify({'error': 'Feedback is not open on this lookbook'}), 404
    if not c.execute('SELECT 1 FROM deck_images WHERE id = ? AND deck_id = ?',
                     (deck_image_id, deck_row['id'])).fetchone():
        conn.close()
        return jsonify({'error': 'Frame not found in this deck'}), 404

    c.execute('''
        INSERT INTO deck_comments (deck_image_id, viewer_token, viewer_name, body)
        VALUES (?, ?, ?, ?)
    ''', (deck_image_id, viewer_token, viewer_name, body))
    comment_id = c.lastrowid
    created_at = c.execute('SELECT created_at FROM deck_comments WHERE id = ?', (comment_id,)).fetchone()['created_at']
    conn.commit()
    conn.close()
    return jsonify({'id': comment_id, 'viewer_name': viewer_name, 'body': body, 'created_at': created_at})

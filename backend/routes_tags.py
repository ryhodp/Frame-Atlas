"""routes_tags.py — Frame Atlas tag routes as a Flask Blueprint (Day 38 / Phase 3).

Two families of "tag" routes, one file (Ryan's call):

  * Editing tags — one photo's tags (/api/images/<id>/tags), Select Mode's
    bulk panel (/api/tags/bulk-apply, bulk-remove, selection-summary,
    suggestions), the library-wide cleanup preview (/api/tags/removal-preview,
    still @admin_required), and the category list (/api/tag-categories).
  * Running the Gemini auto-tagger — /api/tag/start, /api/tag/retry-failed,
    /api/tag/mine, and the /api/tag-progress snapshot, live SSE stream, and
    per-user view. These are thin controls over tagging.py's worker and read
    its state qualified (tagging._tag_progress etc.).

Every route body is character-for-character what it was in app.py — only
@app.route became @bp.route, and every URL path is byte-identical, so the
frontend needed zero changes.

_scope_ids_to_user() (the "only your own photos" trim every bulk endpoint runs
through) moved to core.py, not here: Day 39's bulk filmography routes need it
too, and no blueprint imports another. The removal preview's filter builder
comes from search_filters.py (Day 37) for the same reason.
"""
import base64
import json
import queue as queue_module

from flask import Blueprint, Response, jsonify, request, session, stream_with_context

from core import (
    CAT_COLORS, CAT_LABELS, SQL_PARAM_CHUNK, chunked,
    get_db, admin_required, current_user_id, normalize_tag_value,
    _scope_ids_to_user,
)
from imaging import ar_float_from_str
import gemini
import search_filters
import tagging

bp = Blueprint('tags', __name__)


# ── Running the Gemini auto-tagger ──────────────────────────────────────────

@bp.route('/api/tag/retry-failed', methods=['POST'])
@admin_required
def retry_failed():
    """Reset only failed images to pending and trigger retag. Cheaper than force=true."""
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE images SET tagging_status = 'pending' WHERE tagging_status = 'failed'")
    affected = c.rowcount
    conn.commit()
    conn.close()
    if affected > 0:
        tagging.trigger_tagging()
    return jsonify({'success': True, 'reset': affected, 'message': f'Reset {affected} failed images, tagging started'})

@bp.route('/api/tag-progress/stream')
@admin_required
def tag_progress_stream():
    def generate():
        q = queue_module.Queue(maxsize=50)
        with tagging._sse_lock:
            tagging._sse_queues.append(q)
        try:
            with tagging._tag_progress_lock:
                data = dict(tagging._tag_progress)
            pct = int(data['done'] / data['total'] * 100) if data['total'] > 0 else 0
            yield f"data: {json.dumps({**data, 'pct': pct})}\n\n"

            while True:
                try:
                    payload = q.get(timeout=30)
                    yield f"data: {payload}\n\n"
                    parsed = json.loads(payload)
                    if parsed.get('status') in ('complete', 'error'):
                        break
                except queue_module.Empty:
                    yield ": keepalive\n\n"
        finally:
            with tagging._sse_lock:
                if q in tagging._sse_queues:
                    tagging._sse_queues.remove(q)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )

@bp.route('/api/tag-progress')
@admin_required
def tag_progress_snapshot():
    with tagging._tag_progress_lock:
        data = dict(tagging._tag_progress)
    pct = int(data['done'] / data['total'] * 100) if data['total'] > 0 else 0

    conn = get_db()
    c = conn.cursor()
    counts = {}
    for row in c.execute("SELECT tagging_status, COUNT(*) as n FROM images GROUP BY tagging_status").fetchall():
        counts[row['tagging_status']] = row['n']
    tag_rows = c.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    conn.close()

    return jsonify({**data, 'pct': pct, 'status_counts': counts, 'total_tag_rows': tag_rows})

@bp.route('/api/tag/start', methods=['POST'])
@admin_required
def tag_start():
    force = request.args.get('force') == 'true'
    with tagging._tag_progress_lock:
        if tagging._tag_progress['running']:
            return jsonify({'error': 'Tagging already in progress'}), 400

    if force:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE images SET tagging_status = 'pending'")
        conn.commit()
        conn.close()

    tagging.trigger_tagging()
    return jsonify({'success': True, 'message': 'Tagging started', 'force': force})

@bp.route('/api/tag/mine', methods=['POST'])
def tag_mine():
    """A friend's own 'Tag my photos' trigger — scoped to just their library,
    always using their own saved key (never the admin's)."""
    uid = current_user_id()
    if uid == 1:
        return jsonify({'error': 'Admin tagging runs automatically after sync.'}), 400

    if not gemini.get_user_gemini_key(uid):
        return jsonify({'error': 'Add your Gemini API key in Account settings first.'}), 400

    with tagging._tag_progress_lock:
        if tagging._tag_progress['running']:
            return jsonify({'error': 'Tagging already in progress'}), 400

    tagging.trigger_tagging(user_id=uid)
    return jsonify({'success': True, 'message': 'Tagging started'})

@bp.route('/api/tag-progress/mine')
def tag_progress_mine():
    """Same shape as the admin-only /api/tag-progress, but scoped so a friend
    can poll their own 'Tag my photos' run without the admin_required gate."""
    uid = current_user_id()
    with tagging._tag_progress_lock:
        data = dict(tagging._tag_progress)
    pct = int(data['done'] / data['total'] * 100) if data['total'] > 0 else 0

    conn = get_db()
    c = conn.cursor()
    counts = {}
    for row in c.execute(
        "SELECT tagging_status, COUNT(*) as n FROM images WHERE user_id = ? GROUP BY tagging_status", (uid,)
    ).fetchall():
        counts[row['tagging_status']] = row['n']
    conn.close()

    return jsonify({**data, 'pct': pct, 'status_counts': counts})


# ── Editing tags ────────────────────────────────────────────────────────────

@bp.route('/api/tag-categories')
def tag_categories():
    """Full fixed list of tag categories (not just ones currently in use),
    so the frontend can always show a complete category picker."""
    return jsonify([{
        'key': key,
        'label': CAT_LABELS[key],
        'color': CAT_COLORS.get(key, '#9c988d')
    } for key in CAT_LABELS])

@bp.route('/api/images/<int:image_id>/tags', methods=['POST', 'DELETE'])
def edit_tags(image_id):
    data = request.get_json(force=True) or {}
    # No category picked -> misc. Kept out of CAT_LABELS/CAT_COLORS on
    # purpose so it never shows up as a pickable option in the category
    # dropdown, but renders fine everywhere via the existing .get(x, x)
    # fallbacks (label becomes literally "misc", color a neutral gray).
    category = (data.get('category') or '').strip() or 'misc'
    value = normalize_tag_value(data.get('value'))
    if not value:
        return jsonify({'error': 'value is required'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT user_id FROM images WHERE id = ?', (image_id,))
    row = c.fetchone()
    # V75: tag editing is owner-or-admin, not admin-only — a friend can fix the
    # tags on their OWN photos (same rule as the On-Set Notes editor, V39). A
    # non-owner gets a plain 404, never a 403, so the endpoint doesn't confirm
    # the image exists to someone who can't touch it.
    if not row or (row['user_id'] != session['user_id'] and session.get('role') != 'admin'):
        conn.close()
        return jsonify({'error': 'Image not found'}), 404

    if request.method == 'POST':
        c.execute('''
            SELECT 1 FROM tags WHERE image_id = ? AND category = ? AND value = ?
        ''', (image_id, category, value))
        if not c.fetchone():
            c.execute('''
                INSERT INTO tags (image_id, user_id, category, value)
                VALUES (?, ?, ?, ?)
            ''', (image_id, row['user_id'], category, value))
    else:
        c.execute('''
            DELETE FROM tags WHERE image_id = ? AND category = ? AND value = ?
        ''', (image_id, category, value))

    conn.commit()
    c.execute('SELECT category, value FROM tags WHERE image_id = ? ORDER BY category, value', (image_id,))
    tags = [{'category': t[0], 'value': t[1]} for t in c.fetchall()]
    conn.close()
    return jsonify({'success': True, 'tags': tags})

def count_tags_for_images(c, image_ids):
    """{(category, value): how many of these images carry it}, highest first.

    Shared by the selection summary and the suggestions endpoint — they were
    running the identical query. Chunked (V32) because select-all can now put
    a whole library's worth of ids in one selection, past what SQLite will
    accept as placeholders in a single statement. Chunks are disjoint sets of
    image ids, so adding the per-chunk counts gives the same answer one big
    query would."""
    counts = {}
    for batch in chunked(image_ids):
        placeholders = ','.join('?' * len(batch))
        for row in c.execute(f'''
            SELECT category, value, COUNT(DISTINCT image_id) as cnt
            FROM tags WHERE image_id IN ({placeholders})
            GROUP BY category, value
        ''', batch).fetchall():
            key = (row['category'], row['value'])
            counts[key] = counts.get(key, 0) + row['cnt']
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))

def _parse_bulk_tag_request(data):
    """Shared validation for the bulk-apply/bulk-remove endpoints. Returns
    (image_ids, category, value, error_response). error_response is None
    if validation passed."""
    image_ids = data.get('image_ids')
    # Blank category -> misc, same as the single-image tag editor.
    category = (data.get('category') or '').strip() or 'misc'
    value = normalize_tag_value(data.get('value'))

    if not isinstance(image_ids, list) or not image_ids or \
            not all(isinstance(i, int) for i in image_ids):
        return None, None, None, (jsonify({'error': 'image_ids must be a non-empty list of ints'}), 400)
    if category != 'misc' and category not in CAT_LABELS:
        return None, None, None, (jsonify({'error': 'invalid category'}), 400)
    if not value:
        return None, None, None, (jsonify({'error': 'value is required'}), 400)

    return image_ids, category, value, None

@bp.route('/api/tags/bulk-apply', methods=['POST'])
def bulk_apply_tags():
    data = request.get_json(force=True) or {}
    image_ids, category, value, error = _parse_bulk_tag_request(data)
    if error:
        return error

    conn = get_db()
    c = conn.cursor()
    # V75: friends may bulk-tag in Select Mode, but only their own photos. An
    # admin's list is returned untouched; a friend's is trimmed to what they
    # own, and anything dropped is reported back in invalid_ids.
    requested_ids = image_ids
    image_ids = _scope_ids_to_user(c, image_ids)
    scoped = set(image_ids)
    invalid_ids = [i for i in requested_ids if i not in scoped]

    applied = 0
    already_had = 0

    for image_id in image_ids:
        c.execute('SELECT user_id FROM images WHERE id = ?', (image_id,))
        row = c.fetchone()
        if not row:
            invalid_ids.append(image_id)
            continue

        c.execute('''
            SELECT 1 FROM tags WHERE image_id = ? AND category = ? AND value = ?
        ''', (image_id, category, value))
        if c.fetchone():
            already_had += 1
        else:
            c.execute('''
                INSERT INTO tags (image_id, user_id, category, value)
                VALUES (?, ?, ?, ?)
            ''', (image_id, row['user_id'], category, value))
            applied += 1

    conn.commit()
    conn.close()
    return jsonify({'applied': applied, 'already_had': already_had, 'invalid_ids': invalid_ids})

@bp.route('/api/tags/bulk-remove', methods=['POST'])
def bulk_remove_tags():
    data = request.get_json(force=True) or {}
    image_ids, category, value, error = _parse_bulk_tag_request(data)
    if error:
        return error

    conn = get_db()
    c = conn.cursor()
    # V75: friends may bulk-remove a shared tag from their OWN selection in
    # Select Mode. The library-wide "remove this tag everywhere" cleanup (V32)
    # stays admin-only — that path is gated at /api/tags/removal-preview, which
    # keeps its @admin_required.
    image_ids = _scope_ids_to_user(c, image_ids)
    # Chunked because V32's "remove this tag from every result" can hand this
    # the whole filtered library at once, and SQLite caps how many `?`
    # placeholders one statement may carry. The delete is scoped to the exact
    # category+value pair either way — it can never touch another tag, and it
    # never touches the images themselves.
    removed = 0
    for i in range(0, len(image_ids), SQL_PARAM_CHUNK):
        batch = image_ids[i:i + SQL_PARAM_CHUNK]
        placeholders = ','.join('?' * len(batch))
        c.execute(f'''
            DELETE FROM tags WHERE image_id IN ({placeholders}) AND category = ? AND value = ?
        ''', batch + [category, value])
        removed += c.rowcount
    conn.commit()
    conn.close()
    return jsonify({'removed': removed})

# How many thumbnails the removal preview sends back per category. The COUNT
# and the id list are always complete — this only caps the pictures, because
# 600px base64 thumbnails are ~40KB each and a 2,000-photo preview would be
# an 80MB response for a strip nobody scrolls to the end of.
TAG_REMOVAL_PREVIEW_SAMPLES = 60

@bp.route('/api/tags/removal-preview')
@admin_required
def tag_removal_preview():
    """Show which photos would lose a tag BEFORE removing it across a whole
    filtered search (V32) — Ryan's explicit choice over a bare are-you-sure
    box or an undo window: he wants to look at the photos first.

    Results are grouped by tag category, never merged. 'car (Location)' and
    'car (Objects)' are two different true facts about a photo (CLAUDE.md,
    V30), so the person removing gets to pick which one they actually meant
    instead of wiping both from one button.

    Filtering goes through build_search_filters(), the same code /api/search
    uses, so "110 photos would lose this" counts the same photos the grid is
    showing. Read-only — this endpoint never writes anything."""
    uid = session['user_id']
    value = normalize_tag_value(request.args.get('value'))
    if not value:
        return jsonify({'error': 'value is required'}), 400

    conn = get_db()
    c = conn.cursor()
    conditions, params, _ = search_filters.build_search_filters(c, uid, request.args)
    where = 'WHERE ' + ' AND '.join(conditions)

    # The filter clause goes in as a subquery so it stays byte-for-byte the
    # clause /api/search runs, with no rewriting for the join.
    rows = c.execute(f'''
        SELECT t.category AS category, i.id AS id, i.filename AS filename,
               i.thumbnail_blob AS thumbnail_blob, i.aspect_ratio AS aspect_ratio
        FROM images i JOIN tags t ON t.image_id = i.id
        WHERE t.value = ? AND i.id IN (SELECT id FROM images {where})
        ORDER BY i.date_added DESC
    ''', [value] + params).fetchall()
    conn.close()

    groups = {}
    for row in rows:
        g = groups.setdefault(row['category'], {'image_ids': [], 'samples': []})
        g['image_ids'].append(row['id'])
        if len(g['samples']) < TAG_REMOVAL_PREVIEW_SAMPLES:
            ar_float = ar_float_from_str(row['aspect_ratio'] or '16:9')
            g['samples'].append({
                'id': row['id'],
                'filename': row['filename'],
                'thumbnail': 'data:image/jpeg;base64,' + base64.b64encode(row['thumbnail_blob']).decode('utf-8'),
                'ar_float': round(ar_float, 4)
            })

    return jsonify({
        'value': value,
        'groups': [{
            'category': cat,
            'catLabel': CAT_LABELS.get(cat, cat),
            'color': CAT_COLORS.get(cat, '#9c988d'),
            'count': len(g['image_ids']),
            'image_ids': g['image_ids'],
            'samples': g['samples'],
            'sample_limit': TAG_REMOVAL_PREVIEW_SAMPLES
        } for cat, g in sorted(groups.items(), key=lambda kv: -len(kv[1]['image_ids']))]
    })

@bp.route('/api/tags/selection-summary', methods=['POST'])
def tags_selection_summary():
    data = request.get_json(force=True) or {}
    image_ids = data.get('image_ids')
    if not isinstance(image_ids, list) or not image_ids or \
            not all(isinstance(i, int) for i in image_ids):
        return jsonify({'error': 'image_ids must be a non-empty list of ints'}), 400

    conn = get_db()
    c = conn.cursor()
    # V75: the shared-tags panel is available to friends now, scoped to their
    # own photos (an admin's list passes through untouched).
    image_ids = _scope_ids_to_user(c, image_ids)
    if not image_ids:
        conn.close()
        return jsonify({'total': 0, 'tags': [],
                        'common_filmography': {f: None for f in ('title', 'director', 'dp', 'year', 'painter', 'photographer')}})
    tag_counts = count_tags_for_images(c, image_ids)

    # Filmography consensus: a field only counts as "common" when EVERY
    # selected image already agrees on the same non-empty value — missing
    # data on even one image breaks the consensus (so the bulk form doesn't
    # falsely imply a field's been verified across the whole selection).
    film_rows = []
    for batch in chunked(image_ids):
        placeholders = ','.join('?' * len(batch))
        film_rows += c.execute(f'''
            SELECT image_id, title, director, dp, year, painter, photographer FROM filmography
            WHERE image_id IN ({placeholders})
        ''', batch).fetchall()
    conn.close()

    film_by_image = {r['image_id']: r for r in film_rows}
    common_filmography = {}
    for field in ('title', 'director', 'dp', 'year', 'painter', 'photographer'):
        values = {(film_by_image[iid][field] if iid in film_by_image else None) for iid in image_ids}
        only_value = next(iter(values)) if len(values) == 1 else None
        common_filmography[field] = only_value or None

    total = len(image_ids)
    # "Shared tags" means every selected image carries it, not just some of
    # them — a tag on 4 of 12 selected photos isn't something a bulk-remove
    # click should be able to touch. cnt == total is the actual intersection.
    return jsonify({
        'total': total,
        'tags': [{
            'category': cat,
            'value': val,
            'catLabel': CAT_LABELS.get(cat, cat),
            'color': CAT_COLORS.get(cat, '#9c988d'),
            'count': cnt
        } for (cat, val), cnt in tag_counts.items() if cnt == total],
        'common_filmography': common_filmography
    })

@bp.route('/api/tags/suggestions', methods=['POST'])
def tags_suggestions():
    data = request.get_json(force=True) or {}
    image_ids = data.get('image_ids')
    if not isinstance(image_ids, list) or not image_ids or \
            not all(isinstance(i, int) for i in image_ids):
        return jsonify({'error': 'image_ids must be a non-empty list of ints'}), 400

    conn = get_db()
    c = conn.cursor()
    # V75: available to friends, scoped to their own photos.
    image_ids = _scope_ids_to_user(c, image_ids)
    if not image_ids:
        conn.close()
        return jsonify({'suggestions': []})
    total = len(image_ids)
    selection_tags = count_tags_for_images(c, image_ids)

    if not selection_tags:
        conn.close()
        return jsonify({'suggestions': []})

    # Top 5 seed tags by how many selected images carry them.
    seed_pairs = sorted(selection_tags.items(), key=lambda kv: kv[1], reverse=True)[:5]
    seed_values = [pair[0][1] for pair in seed_pairs]

    # V86: both halves scoped to the caller's OWN library (admin included —
    # Ryan's call). Before this the candidate query read every user's tags,
    # so a friend was offered tag words and counts that existed only on the
    # admin's photos, and vice versa. tags.user_id is always the image owner
    # (every INSERT writes the owner's id), so filtering on it is exact.
    uid = session['user_id']
    seed_placeholders = ','.join('?' * len(seed_values))
    candidate_rows = c.execute(f'''
        SELECT t2.category, t2.value, COUNT(DISTINCT t2.image_id) as cnt
        FROM tags t2
        WHERE t2.user_id = ? AND t2.image_id IN (
            SELECT DISTINCT image_id FROM tags WHERE user_id = ? AND value IN ({seed_placeholders})
        )
        AND t2.value NOT IN ({seed_placeholders})
        GROUP BY t2.category, t2.value
        ORDER BY cnt DESC
        LIMIT 30
    ''', [uid, uid] + seed_values + seed_values).fetchall()
    conn.close()

    suggestions = []
    for row in candidate_rows:
        key = (row['category'], row['value'])
        if selection_tags.get(key, 0) >= total:
            continue
        suggestions.append({
            'category': row['category'],
            'value': row['value'],
            'catLabel': CAT_LABELS.get(row['category'], row['category']),
            'color': CAT_COLORS.get(row['category'], '#9c988d'),
            'count': row['cnt']
        })
        if len(suggestions) >= 12:
            break

    return jsonify({'suggestions': suggestions})

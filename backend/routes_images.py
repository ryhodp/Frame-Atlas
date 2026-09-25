"""routes_images.py — Frame Atlas photo routes as a Flask Blueprint (Day 40 / Phase 3).

Everything you do TO a photo that's already in a library: list and view it
(/api/images, /full, /thumb), favourite it, edit its film credit (single and
Select Mode bulk, plus the as-you-type suggestions), edit its on-set notes,
download it, delete it (single and bulk), and crop it (queue a job, poll and
reset progress — the worker itself is crop.py).

Every route body is character-for-character what it was in app.py — only
@app.route became @bp.route, and every URL path is byte-identical, so the
frontend needed zero changes.

MediaIoBaseDownload is imported here (full-res view + download) as well as in
app.py-era homes drive.py / sync.py — anything faking Drive downloads for
these routes must patch routes_images.MediaIoBaseDownload (the Day 34/35 trap).

Not here: adding NEW photos (/api/upload, /api/clip — Day 42's routes_sync.py)
and the admin library tools (routes_maintenance.py).
"""
import base64
import concurrent.futures
import io
import threading
import time

from flask import Blueprint, jsonify, request, send_file, session
from googleapiclient.http import MediaIoBaseDownload

from core import chunked, get_db, favorite_col, _scope_ids_to_user
from perspective import parse_perspective_corners, perspective_is_whole_image
import crop
import drive
from drive import BULK_DELETE_WORKERS

bp = Blueprint('images', __name__)


@bp.route('/api/images', methods=['GET'])
def get_images():
    user_id = session['user_id']
    conn = get_db()
    c = conn.cursor()
    c.execute(f'''
        SELECT id, filename, thumbnail_blob, aspect_ratio, date_added, {favorite_col(user_id)}
        FROM images WHERE user_id = ? ORDER BY date_added DESC
    ''', (user_id,))
    images = []
    for row in c.fetchall():
        thumb_b64 = base64.b64encode(row[2]).decode('utf-8')
        images.append({
            'id': row[0], 'filename': row[1],
            'thumbnail': f'data:image/jpeg;base64,{thumb_b64}',
            'aspect_ratio': row[3], 'date_added': row[4],
            'is_favorite': row[5]
        })
    conn.close()
    return jsonify({'images': images})

@bp.route('/api/images/<int:image_id>/full')
def get_full_image(image_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT drive_file_id FROM images WHERE id = ? AND user_id = ?', (image_id, session['user_id']))
    row = c.fetchone()
    conn.close()

    if not row:
        return jsonify({'error': 'Image not found'}), 404

    file_id = row['drive_file_id']
    try:
        service = drive.get_drive_service()
        req = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        return send_file(fh, mimetype='image/jpeg', as_attachment=False)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/images/<int:image_id>/thumb')
def get_image_thumb(image_id):
    """A photo's thumbnail as its own cacheable URL (V43/Day 25), instead of
    base64 buried inside a JSON response — a base64 blob inside JSON can
    never be cached by the browser, since there's no URL to remember it by.
    Measured at real library size: ~6.5MB re-transferred per page of 60,
    every single visit.

    The `?v=` query param (the image's own md5_checksum, set by
    build_image_dict()) is never read here — it exists purely so the URL
    itself changes when a crop rewrites the image, forcing a fresh fetch,
    while an unrelated re-tag leaves the checksum and the URL untouched.

    Same owner-or-admin check as the other single-image endpoints (crop,
    delete, notes) — deliberately NOT a signed/public URL, since search
    already never returns another user's images, and this keeps that
    guarantee true for the thumbnail too."""
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        'SELECT thumbnail_blob, user_id FROM images WHERE id = ?', (image_id,)
    ).fetchone()
    conn.close()

    if not row or (row['user_id'] != session['user_id'] and session.get('role') != 'admin'):
        return jsonify({'error': 'Image not found'}), 404

    resp = send_file(io.BytesIO(row['thumbnail_blob']), mimetype='image/jpeg')
    # private: this is login-gated, per-user content — a shared/CDN cache
    # must not serve one user's thumbnail to another. immutable + a year:
    # safe because the URL itself changes (see ?v= above) whenever the
    # actual bytes do.
    resp.headers['Cache-Control'] = 'private, max-age=31536000, immutable'
    return resp

def _toggle_membership(table, user_id, image_id):
    """Shared on/off toggle for a user's membership table (currently just
    user_favorites — a 'flag' feature using this on user_flags was removed
    in V55): insert if absent, delete if present. Returns the new state
    (True = now in the table)."""
    conn = get_db()
    c = conn.cursor()
    if not c.execute('SELECT 1 FROM images WHERE id = ? AND user_id = ?', (image_id, user_id)).fetchone():
        conn.close()
        return None
    existing = c.execute(
        f'SELECT 1 FROM {table} WHERE user_id = ? AND image_id = ?', (user_id, image_id)
    ).fetchone()
    if existing:
        c.execute(f'DELETE FROM {table} WHERE user_id = ? AND image_id = ?', (user_id, image_id))
        new_state = False
    else:
        c.execute(f'INSERT INTO {table} (user_id, image_id) VALUES (?, ?)', (user_id, image_id))
        new_state = True
    conn.commit()
    conn.close()
    return new_state

@bp.route('/api/images/<int:image_id>/favorite', methods=['POST'])
def toggle_favorite(image_id):
    result = _toggle_membership('user_favorites', session['user_id'], image_id)
    if result is None:
        return jsonify({'error': 'Image not found'}), 404
    return jsonify({'success': True, 'is_favorite': result})

@bp.route('/api/filmography/autocomplete')
def filmography_autocomplete():
    """Suggest filmography values (title, director, DP) based on what the logged-in
    user has already entered on other photos.

    Matches anywhere in the value, not just the start — "Korra" finds "The
    Legend of Korra", "Deakins" finds "Roger Deakins" — since film titles and
    full names routinely put the useful search word after the first one.
    A value that STARTS with what was typed still ranks above one that merely
    contains it, so an exact-prefix match like "Yi" -> "Yi Yi" doesn't get
    buried under unrelated contains-matches; frequency breaks ties within
    each of those two groups.

    Title suggestions carry their other credits along for the ride: if
    "Tokyo Story" already has director=Yasujiro Ozu / year=1953 recorded
    somewhere, picking that suggestion elsewhere should offer to fill those
    in too, not just complete the word. Director/DP/painter/photographer
    suggestions don't get this treatment the other way — a director made
    many films, so there's no single title/year to sensibly offer back.
    MAX() picks one consistent combination per title if photos ever
    disagree (e.g. a typo fixed on one but not another yet); it doesn't
    matter which survives since the point is a starting point to edit from,
    not a promise of authority."""
    field = request.args.get('field', '').strip()  # 'title', 'director', 'dp', 'painter', or 'photographer'
    q = request.args.get('q', '').strip().lower()

    if not field or field not in ('title', 'director', 'dp', 'painter', 'photographer') or not q:
        return jsonify([])

    uid = session['user_id']
    conn = get_db()
    c = conn.cursor()

    if field == 'title':
        rows = c.execute('''
            SELECT title as value, COUNT(DISTINCT f.image_id) as cnt,
                   CASE WHEN LOWER(title) LIKE ? THEN 0 ELSE 1 END as prefix_rank,
                   MAX(director) as director, MAX(dp) as dp, MAX(year) as year,
                   MAX(painter) as painter, MAX(photographer) as photographer
            FROM filmography f JOIN images i ON i.id = f.image_id
            WHERE i.user_id = ? AND f.title IS NOT NULL AND LOWER(f.title) LIKE ?
            GROUP BY f.title
            ORDER BY prefix_rank ASC, cnt DESC
            LIMIT 20
        ''', (f'{q}%', uid, f'%{q}%')).fetchall()
        conn.close()
        return jsonify([{
            'value': r['value'], 'count': r['cnt'],
            'director': r['director'], 'dp': r['dp'], 'year': r['year'],
            'painter': r['painter'], 'photographer': r['photographer'],
        } for r in rows])

    rows = c.execute(f'''
        SELECT {field} as value, COUNT(DISTINCT f.image_id) as cnt,
               CASE WHEN LOWER({field}) LIKE ? THEN 0 ELSE 1 END as prefix_rank
        FROM filmography f JOIN images i ON i.id = f.image_id
        WHERE i.user_id = ? AND f.{field} IS NOT NULL AND LOWER(f.{field}) LIKE ?
        GROUP BY f.{field}
        ORDER BY prefix_rank ASC, cnt DESC
        LIMIT 20
    ''', (f'{q}%', uid, f'%{q}%')).fetchall()

    conn.close()
    return jsonify([{'value': r['value'], 'count': r['cnt']} for r in rows])


@bp.route('/api/images/<int:image_id>/filmography', methods=['POST'])
def update_filmography(image_id):
    """Set or clear the creator info for this image. Sending all empty fields
    clears it entirely.

    V81: Director/DP (film stills), Painter (paintings) and Photographer
    (photographs) all live on this same table — the frontend shows whichever
    pair applies based on a media-type switch, but the backend doesn't care
    which one was filled in; it just stores whatever came in the request.

    V75: owner-or-admin, not admin-only — a friend knows what they shot and can
    fix the credit on their own photo, same rule as tag editing and the
    On-Set Notes editor (V39)."""
    data = request.get_json(force=True) or {}
    title = (data.get('title') or '').strip()
    director = (data.get('director') or '').strip()
    dp = (data.get('dp') or '').strip()
    year = str(data.get('year') or '').strip()
    painter = (data.get('painter') or '').strip()
    photographer = (data.get('photographer') or '').strip()

    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT user_id FROM images WHERE id = ?', (image_id,)).fetchone()
    if not row or (row['user_id'] != session['user_id'] and session.get('role') != 'admin'):
        conn.close()
        return jsonify({'error': 'Image not found'}), 404

    c.execute('DELETE FROM filmography WHERE image_id = ?', (image_id,))
    filmography = None
    if any([title, director, dp, year, painter, photographer]):
        c.execute(
            'INSERT INTO filmography (image_id, title, director, dp, year, painter, photographer) '
            'VALUES (?,?,?,?,?,?,?)',
            (image_id, title or None, director or None, dp or None, year or None,
             painter or None, photographer or None)
        )
        filmography = {'title': title or None, 'director': director or None,
                       'dp': dp or None, 'year': year or None,
                       'painter': painter or None, 'photographer': photographer or None}
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'filmography': filmography})

@bp.route('/api/images/<int:image_id>/notes', methods=['POST'])
def update_notes(image_id):
    """Set or clear a photo's DP technical fields (camera/rig, lens, lens
    filter, stop) and freeform on-set notes. V39: deliberately owner-or-admin
    rather than @admin_required — the first metadata field in this app a
    friend can edit on their OWN photo. Every other edit endpoint (tags,
    filmography) is admin-only; this is Ryan's explicit call, since these are
    facts about a shoot a friend would know, not an AI guess to curate.
    notes_fts stays in sync via the AFTER UPDATE OF ... trigger (see
    init_db()) — nothing here touches it directly."""
    data = request.get_json(force=True) or {}
    camera_rig = (data.get('camera_rig') or '').strip()
    lens = (data.get('lens') or '').strip()
    lens_filter = (data.get('lens_filter') or '').strip()
    stop = (data.get('stop') or '').strip()
    onset_notes = (data.get('onset_notes') or '').strip()

    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT user_id FROM images WHERE id = ?', (image_id,)).fetchone()
    if not row or (row['user_id'] != session['user_id'] and session.get('role') != 'admin'):
        conn.close()
        return jsonify({'error': 'Image not found'}), 404

    c.execute(
        'UPDATE images SET camera_rig = ?, lens = ?, lens_filter = ?, stop = ?, onset_notes = ? WHERE id = ?',
        (camera_rig or None, lens or None, lens_filter or None, stop or None, onset_notes or None, image_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'notes': {
        'camera_rig': camera_rig or None,
        'lens': lens or None,
        'lens_filter': lens_filter or None,
        'stop': stop or None,
        'onset_notes': onset_notes or None,
    }})

def _parse_bulk_image_ids(data):
    """Shared image_ids validation for the bulk filmography endpoints."""
    image_ids = data.get('image_ids')
    if not isinstance(image_ids, list) or not image_ids or \
            not all(isinstance(i, int) for i in image_ids):
        return None, (jsonify({'error': 'image_ids must be a non-empty list of ints'}), 400)
    return image_ids, None

@bp.route('/api/filmography/bulk-set', methods=['POST'])
def bulk_set_filmography():
    """Applies only the fields you actually typed to every selected image —
    a blank field means "leave this field alone" per image, not "clear it."
    So fixing just the DP across 10 stills that already have the right
    title/director doesn't blank those out; each image keeps whatever it
    already had in any field you didn't touch."""
    data = request.get_json(force=True) or {}
    image_ids, error = _parse_bulk_image_ids(data)
    if error:
        return error

    touched = {
        'title': (data.get('title') or '').strip(),
        'director': (data.get('director') or '').strip(),
        'dp': (data.get('dp') or '').strip(),
        'year': str(data.get('year') or '').strip(),
        'painter': (data.get('painter') or '').strip(),
        'photographer': (data.get('photographer') or '').strip(),
    }
    touched = {k: v for k, v in touched.items() if v}
    if not touched:
        return jsonify({'error': 'At least one of title/director/dp/year/painter/photographer is required'}), 400

    conn = get_db()
    c = conn.cursor()
    # V75: friends may bulk-set filmography on their OWN selection only.
    requested_ids = image_ids
    image_ids = _scope_ids_to_user(c, image_ids)
    valid_ids = [r[0] for r in c.execute(
        f"SELECT id FROM images WHERE id IN ({','.join('?' * len(image_ids))})", image_ids
    ).fetchall()] if image_ids else []
    invalid_ids = [i for i in requested_ids if i not in valid_ids]

    for image_id in valid_ids:
        existing = c.execute(
            'SELECT title, director, dp, year, painter, photographer FROM filmography WHERE image_id = ?', (image_id,)
        ).fetchone()
        merged = {
            field: touched.get(field, existing[field] if existing else None)
            for field in ('title', 'director', 'dp', 'year', 'painter', 'photographer')
        }
        c.execute('DELETE FROM filmography WHERE image_id = ?', (image_id,))
        if any(merged.values()):
            c.execute(
                'INSERT INTO filmography (image_id, title, director, dp, year, painter, photographer) '
                'VALUES (?,?,?,?,?,?,?)',
                (image_id, merged['title'], merged['director'], merged['dp'], merged['year'],
                 merged['painter'], merged['photographer'])
            )
    conn.commit()
    conn.close()

    return jsonify({
        'updated': len(valid_ids),
        'invalid_ids': invalid_ids,
        'fields_applied': touched,
    })

@bp.route('/api/filmography/bulk-clear', methods=['POST'])
def bulk_clear_filmography():
    """Wipes filmography from every selected image — for stills Gemini
    guessed a film on that isn't one at all. V75: owner-scoped for friends."""
    data = request.get_json(force=True) or {}
    image_ids, error = _parse_bulk_image_ids(data)
    if error:
        return error

    conn = get_db()
    c = conn.cursor()
    requested_ids = image_ids
    image_ids = _scope_ids_to_user(c, image_ids)
    valid_ids = [r[0] for r in c.execute(
        f"SELECT id FROM images WHERE id IN ({','.join('?' * len(image_ids))})", image_ids
    ).fetchall()] if image_ids else []
    invalid_ids = [i for i in requested_ids if i not in valid_ids]

    for image_id in valid_ids:
        c.execute('DELETE FROM filmography WHERE image_id = ?', (image_id,))
    conn.commit()
    conn.close()

    return jsonify({'cleared': len(valid_ids), 'invalid_ids': invalid_ids})

@bp.route('/api/images/<int:image_id>/download')
def download_image(image_id):
    import mimetypes
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT drive_file_id, filename FROM images WHERE id = ? AND user_id = ?', (image_id, session['user_id']))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Image not found'}), 404
    try:
        service = drive.get_drive_service()
        req = service.files().get_media(fileId=row['drive_file_id'])
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        mime = mimetypes.guess_type(row['filename'])[0] or 'application/octet-stream'
        return send_file(fh, mimetype=mime, as_attachment=True, download_name=row['filename'])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/images/<int:image_id>', methods=['DELETE'])
def delete_image(image_id):
    """Admin: moves the Drive file into _Removed (recoverable), then removes
    the image and its metadata from the library. Friends (V17): removes the
    image from THEIR library only — their Drive file is never touched, since
    they typically share read-only and own the file anyway."""
    user_id = session['user_id']
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT drive_file_id, filename, user_id FROM images WHERE id = ?', (image_id,))
    row = c.fetchone()
    conn.close()
    if not row or (user_id != 1 and row['user_id'] != user_id):
        return jsonify({'error': 'Image not found'}), 404

    if user_id == 1:
        try:
            service = drive.get_drive_service()
            file_id = row['drive_file_id']
            f = service.files().get(fileId=file_id, fields='parents').execute()
            prev_parents = ','.join(f.get('parents', []))
            removed_id = drive.get_or_create_removed_folder(service, drive.get_root_folder_id(1))
            service.files().update(
                fileId=file_id,
                addParents=removed_id,
                removeParents=prev_parents,
                fields='id'
            ).execute()
        except Exception as e:
            msg = str(e)
            if 'insufficient' in msg.lower() or 'permission' in msg.lower() or '403' in msg:
                return jsonify({
                    'error': ("Drive blocked the move — the service account only has Viewer "
                              "access. In Drive: right-click the folder → Share → change the "
                              "service account's role to Editor, then try again.")
                }), 403
            return jsonify({'error': f'Could not move file in Drive: {msg}'}), 500

    conn = get_db()
    c = conn.cursor()
    for table in ('tags', 'colors', 'embeddings', 'deck_images', 'filmography', 'user_favorites', 'user_flags', 'image_views'):
        c.execute(f'DELETE FROM {table} WHERE image_id = ?', (image_id,))
    c.execute('DELETE FROM images WHERE id = ?', (image_id,))
    if user_id != 1:
        # The file is still sitting in their Drive folder (we can't move it),
        # so remember it — otherwise the next sync would re-import it.
        c.execute('INSERT OR IGNORE INTO sync_exclusions (user_id, drive_file_id) VALUES (?, ?)',
                  (user_id, row['drive_file_id']))
    conn.commit()
    conn.close()
    return jsonify({'success': True,
                    'moved_to': drive.REMOVED_FOLDER_NAME if user_id == 1 else None,
                    'filename': row['filename']})

# BULK_DELETE_WORKERS (how many Drive moves run at once) lives in drive.py
# since Day 40 — /api/upload (still in app.py) reuses it for parallel uploads.

# Google's machine-readable reasons for "you're calling too fast" (as
# opposed to a real permissions/quota problem, which shouldn't be retried).
DRIVE_RATE_LIMIT_REASONS = {'userRateLimitExceeded', 'rateLimitExceeded'}

@bp.route('/api/images/bulk-delete', methods=['POST'])
def bulk_delete_images():
    """Same rules as DELETE /api/images/<id> (owner-or-admin, admin's own
    images move to Drive's _Removed), just batched. A failure on one photo
    (e.g. a Drive permission hiccup) is skipped and reported — it does not
    roll back or block the rest of the batch.

    The Drive move for each admin photo runs across a small thread pool
    (BULK_DELETE_WORKERS at a time) instead of one at a time. The _Removed
    folder is looked up once up front, before any worker starts, so they
    never race to create it. A photo that hits Drive's rate limit gets a
    couple of short retries before it's actually counted as failed — one
    busy moment during a big batch shouldn't turn a working delete into an
    error."""
    user_id = session['user_id']
    data = request.get_json(force=True) or {}
    image_ids = data.get('image_ids')
    if not isinstance(image_ids, list) or not image_ids or \
            not all(isinstance(i, int) for i in image_ids):
        return jsonify({'error': 'image_ids must be a non-empty list of ints'}), 400

    conn = get_db()
    c = conn.cursor()
    rows = []
    for batch in chunked(image_ids):
        placeholders = ','.join('?' * len(batch))
        rows += c.execute(
            f'SELECT id, drive_file_id, filename, user_id FROM images WHERE id IN ({placeholders})',
            batch
        ).fetchall()
    conn.close()

    by_id = {r['id']: r for r in rows}
    deleted = []  # (image_id, drive_file_id)
    errors = []

    to_move = []  # rows that need an actual Drive move (admin only)
    for image_id in image_ids:
        row = by_id.get(image_id)
        if not row or (user_id != 1 and row['user_id'] != user_id):
            errors.append({'id': image_id, 'error': 'Image not found'})
            continue
        if user_id == 1:
            to_move.append(row)
        else:
            # Friends' deletes are DB-only (Viewer share, can't move files) —
            # nothing to parallelize, straight to the deleted list.
            deleted.append((image_id, row['drive_file_id']))

    if to_move:
        root_id = drive.get_root_folder_id(1)
        removed_folder_id = drive.get_or_create_removed_folder(drive.get_drive_service(), root_id)

        # One Drive service per worker thread, not one shared across all of
        # them or one built fresh per photo — building it is cheap (no
        # network call, static discovery doc), and threading.local keeps
        # each worker's httplib2 transport from being touched by another
        # thread mid-request.
        thread_local = threading.local()
        def _thread_service():
            if not hasattr(thread_local, 'service'):
                thread_local.service = drive.get_drive_service()
            return thread_local.service

        def move_one(row):
            file_id = row['drive_file_id']
            service = _thread_service()
            attempt = 0
            while True:
                attempt += 1
                try:
                    f = service.files().get(fileId=file_id, fields='parents').execute()
                    prev_parents = ','.join(f.get('parents', []))
                    service.files().update(
                        fileId=file_id,
                        addParents=removed_folder_id,
                        removeParents=prev_parents,
                        fields='id'
                    ).execute()
                    return row, None
                except Exception as e:
                    if attempt <= 2 and drive.drive_error_reason(e) in DRIVE_RATE_LIMIT_REASONS:
                        time.sleep(attempt)  # brief backoff, then retry this photo only
                        continue
                    return row, e

        with concurrent.futures.ThreadPoolExecutor(max_workers=BULK_DELETE_WORKERS) as pool:
            for row, err in pool.map(move_one, to_move):
                if err is None:
                    deleted.append((row['id'], row['drive_file_id']))
                else:
                    print(f"[bulk-delete] image {row['id']} ({row['filename']}) failed: {err}")
                    errors.append({'id': row['id'], 'filename': row['filename'], 'error': str(err)})

    if deleted:
        deleted_ids = [d[0] for d in deleted]
        conn = get_db()
        c = conn.cursor()
        for batch in chunked(deleted_ids):
            ph = ','.join('?' * len(batch))
            for table in ('tags', 'colors', 'embeddings', 'deck_images', 'filmography', 'user_favorites', 'user_flags', 'image_views'):
                c.execute(f'DELETE FROM {table} WHERE image_id IN ({ph})', batch)
            c.execute(f'DELETE FROM images WHERE id IN ({ph})', batch)
        if user_id != 1:
            c.executemany(
                'INSERT OR IGNORE INTO sync_exclusions (user_id, drive_file_id) VALUES (?, ?)',
                [(user_id, d[1]) for d in deleted]
            )
        conn.commit()
        conn.close()

    print(f"[bulk-delete] requested {len(image_ids)}, deleted {len(deleted)}, failed {len(errors)}")
    return jsonify({'deleted': [d[0] for d in deleted], 'errors': errors})

@bp.route('/api/images/<int:image_id>/crop', methods=['POST'])
def crop_image(image_id):
    """Queue a crop job to run in the background (V27).

    The browser sends the selection as percentages of the image (0-100), so it
    means the same thing at any resolution. Two shapes are accepted:

      · `box`     {x, y, w, h}          — the original axis-aligned rectangle
      · `corners` [{x,y} x4]  (V32)     — four free corners, de-skewed into a
                                          straight rectangle

    `corners` wins if both are present. A request with no `corners` field takes
    exactly the path it always did, byte for byte, so old clients and anything
    already sitting in the queue keep working.

    Instead of blocking, this queues the job and returns immediately so the
    user can navigate away. A progress endpoint tracks the queue.
    """
    user_id = session['user_id']
    data = request.get_json(silent=True) or {}
    raw_corners = data.get('corners')

    box = None
    corners = None
    if raw_corners is not None:
        # V32 perspective path. Validated here AND again in the worker: this
        # gives Ryan an immediate, readable 400 instead of a failure that only
        # surfaces minutes later in the progress panel, while the worker's own
        # check is what actually stands between a bad quad and the Drive write.
        try:
            corners = parse_perspective_corners(raw_corners)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        if perspective_is_whole_image(corners):
            return jsonify({'error': 'The corners cover the whole image — nothing to correct.'}), 400
    else:
        box = data.get('box') or {}
        try:
            x_pct = float(box['x'])
            y_pct = float(box['y'])
            w_pct = float(box['w'])
            h_pct = float(box['h'])
        except (KeyError, TypeError, ValueError):
            return jsonify({'error': 'Crop box must include numeric x, y, w, h percentages.'}), 400

        x_pct = min(max(x_pct, 0.0), 100.0)
        y_pct = min(max(y_pct, 0.0), 100.0)
        w_pct = min(max(w_pct, 0.0), 100.0 - x_pct)
        h_pct = min(max(h_pct, 0.0), 100.0 - y_pct)
        if w_pct < 1 or h_pct < 1:
            return jsonify({'error': 'Crop box is too small — it must cover at least 1% of the image.'}), 400
        if w_pct >= 99.5 and h_pct >= 99.5:
            return jsonify({'error': 'Crop box covers the whole image — nothing to crop.'}), 400
        box = {'x': x_pct, 'y': y_pct, 'w': w_pct, 'h': h_pct}

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT drive_file_id, filename, user_id FROM images WHERE id = ?', (image_id,))
    row = c.fetchone()
    conn.close()
    if not row or (user_id != 1 and row['user_id'] != user_id):
        return jsonify({'error': 'Image not found'}), 404

    with crop._crop_lock:
        crop._crop_job_counter += 1
        job_id = crop._crop_job_counter
        crop._crop_progress['total'] += 1
        crop._crop_progress['in_progress'] += 1

    job = {
        'id': job_id,
        'image_id': image_id,
        'user_id': user_id,
        'box': box,
        # Absent (not just empty) on rectangle jobs, so the worker's
        # `job.get('corners')` branch can never be tripped by an old job dict.
        'corners': corners,
        'filename': row['filename']
    }
    crop._crop_queue.put(job)

    return jsonify({
        'queued': True,
        'job_id': job_id,
        'message': 'Crop queued — check progress in the notification below.'
    })

@bp.route('/api/crop-progress', methods=['GET'])
def get_crop_progress():
    """Get current crop job queue progress and failures (V27)."""
    with crop._crop_lock:
        return jsonify({
            'in_progress': crop._crop_progress['in_progress'],
            'total': crop._crop_progress['total'],
            'completed': crop._crop_progress['completed'],
            'failed': crop._crop_progress['failed'],
            'active_jobs': list(crop._crop_progress['active_jobs'].values())
        })

@bp.route('/api/crop-progress/reset', methods=['POST'])
def reset_crop_progress():
    """Clear the progress state after user closes notifications (V27)."""
    with crop._crop_lock:
        crop._crop_progress['total'] = 0
        crop._crop_progress['completed'] = 0
        crop._crop_progress['failed'] = []
        crop._crop_progress['active_jobs'] = {}
    return jsonify({'reset': True})

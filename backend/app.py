import os
import base64
import sqlite3
import zlib
import threading
from datetime import timedelta
from functools import wraps
from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image
# Day 29 (Phase 3): all Google Drive connection/auth/folder code moved to
# drive.py. Day 42: the last Drive-upload user (/api/upload) and the last
# Gemini-client user (/api/models) left too — the workers and blueprints that
# talk to Drive / Gemini import their own copies.


# V45 (Day 27): pure image/colour/geometry maths, split out of this file.
#
# Imported BY NAME rather than as modules so every caller below reads exactly
# as it did before the split — and, more importantly, so app.py's public
# surface is unchanged. All 34 scripts/test_*_locally.py reach straight into
# this module (`mod.color_matches`, `mod.PALETTE_DARK_V`, `mod._hsv` …) after
# importing it from a temp copy, as does scripts/diagnose_color_filter.py.
#
# Some of these names are therefore unused *in this file* and a linter will say
# so. Do not delete them on that basis: they are the module's API, and dropping
# one breaks a test script silently, in a harness that copies app.py alone.
from colors import (
    PALETTE_DARK_V, PALETTE_GRAY_S, PALETTE_MERGE_HUE_TOL, PALETTE_VERSION,
    EXACTNESS_LOOSE_DEG, EXACTNESS_TIGHT_DEG, EXACTNESS_LOOSE_VAL, EXACTNESS_TIGHT_VAL,
    DEFAULT_EXACTNESS, DEFAULT_PROMINENCE,
    DUP_COLOR_HUE_TOL, DUP_COLOR_MIN_OVERLAP,
    _is_shadow_or_gray, _hsv, _chromatic_entries,
    extract_palette, hex_to_rgb, color_distance,
    exactness_to_hue_tol, exactness_to_value_tol,
    color_matches, color_match_share, palettes_overlap,
)
from fingerprint import (
    PHASH_GRID, PHASH_BITS, PHASH_HEX_LEN, PHASH_NEAR_DUP_THRESHOLD,
    SIGNATURE_GRID, SIGNATURE_MAX_DISTANCE,
    compute_phash, phash_distance,
    compute_signature, signature_distance, signatures_match,
)
from imaging import (
    STANDARD_ASPECT_RATIOS, AR_QUERY_ALIASES,
    generate_thumbnail, get_image_aspect_ratio,
    normalize_ar_label, ar_float_from_str, ar_query_labels,
)
from perspective import (
    PERSPECTIVE_MIN_AREA_PCT, PERSPECTIVE_MIN_CORNER_GAP_PCT,
    _quad_signed_area, parse_perspective_corners, perspective_is_whole_image,
    solve_linear_system, solve_perspective_coeffs,
    perspective_output_size, perspective_correct,
)

# Day 28 (Phase 3): shared basics + boot/schema code moved to their own
# files. Imported back BY NAME so app.py's public surface is unchanged and
# every scripts/test_*_locally.py that reaches into this module still works.
from core import (
    GEMINI_MODEL, GEMINI_PRICING, DEFAULT_GEMINI_PRICING, get_model_pricing,
    CAT_COLORS, CAT_LABELS, MANUAL_TAG_CATEGORIES,
    SQL_PARAM_CHUNK, chunked,
    TAG_PLURAL_STRIP_EXCEPTIONS, normalize_tag_value, clear_ai_tags,
    _shuffle_key, get_db, db_path, favorite_col,
    RUNNING_LOCALLY, admin_required, current_user_id, check_login_required,
    _scope_ids_to_user,
)
from schema import (
    _is_duplicate_column_error, EXPECTED_COLUMNS, missing_columns,
    check_schema, init_db, load_embeddings_seed,
)

# Day 29 (Phase 3): Google Drive layer. Call sites are qualified
# (drive.get_drive_service()); test scripts patch fakes onto this module.
import drive

# Day 30 (Phase 3): Gemini key encryption + per-user spend tracking. Call
# sites are qualified (gemini.get_user_gemini_key()); test scripts patch
# fakes onto this module.
import gemini

# Day 32 (Phase 3): Gemini auto-tag worker + SSE progress state. Call sites are
# qualified (tagging.trigger_tagging(), tagging._tag_progress, …); the ~8 test
# scripts that no-op the worker patch tagging.trigger_tagging. genai_client stays
# imported here too — /api/models still uses it directly (/api/interpret moved
# to routes_search.py on Day 37 and imports its own copy).
import tagging

# Day 31 (Phase 3): image-row hydration (build_image_dict / hydrate_image_rows
# / _fetch_image_dict), the palette writer, and the four boot-time backfills.
# Call sites are qualified (images_common.build_image_dict(); test scripts
# reach them as mod.images_common.<name>).
import images_common
import backup
import crop
import sync

# Day 36 (Phase 3): login/register/setup/invite-code routes as a Flask
# Blueprint. Registered below via app.register_blueprint(routes_auth.bp).
# Internal helpers (rate limiting, login lockout) are qualified
# (routes_auth._rate_limited(), …); test scripts patch fakes onto this module
# the same way they do for drive/sync/etc.
import routes_auth

# Day 37 (Phase 3): search routes as a Blueprint (routes_search.py), plus the
# shared filter builder they use (search_filters.py; app.py itself no longer
# calls it — the tag-removal preview moved to routes_tags.py on Day 38).
import routes_search

# Day 38 (Phase 3): tag editing + auto-tagger control routes as a Blueprint.
import routes_tags

# Day 40 (Phase 3): photo routes (view/favourite/film/notes/download/delete/
# crop) and the admin library tools (Duplicate Review, regenerate thumbnails,
# re-extract colours) as two Blueprints.
import routes_images
import routes_maintenance

# Day 41 (Phase 3): decks. Shared helpers + the boot self-test in decks_common;
# logged-in routes in routes_decks; the public share-link routes (no login)
# in routes_share. run_self_test and _display_name (analytics) are imported by
# name so their call sites here are unchanged.
from decks_common import run_self_test, _display_name
import decks_common  # reached as mod.decks_common by test_self_test_locally.py
import routes_decks
import routes_share

# Day 42 (Phase 3): photos-in (sync/upload/clip) and account/Google-connection
# routes as Blueprints; backups + /api/models joined routes_maintenance.
import routes_sync
import routes_account


app = Flask(__name__, static_folder='static', static_url_path='/static')
# Railway's proxy terminates HTTPS in front of us; without this, Flask thinks
# every request arrived over plain http and builds http:// URLs (which breaks
# the Google OAuth redirect_uri).
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
CORS(app)
# Signs the login session cookie. MUST be a fixed value set via the
# FLASK_SECRET_KEY Railway env var — falling back to a random one means every
# redeploy invalidates every logged-in session (everyone gets logged out on
# every push). The random fallback only exists so local dev works with zero
# setup.
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or os.urandom(24)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Day 28 security hardening.
#  - HTTPONLY: JavaScript can never read the session cookie (blunts an XSS
#    that would otherwise hand an attacker a live session).
#  - SECURE: the cookie only rides on HTTPS. Turned OFF for local runs
#    (FA_DB_PATH set — the same "am I local, not production" signal the test
#    harness already uses) because the Flask test client and http://localhost
#    dev talk plain HTTP, and a Secure cookie there is silently dropped, which
#    would break every login-gated test and local sign-in.
# RUNNING_LOCALLY itself moved to core.py in Day 36 — every route blueprint
# needs the same "am I local" signal for its own rate-limiting, and none of
# them may import app.py to get it.
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = not RUNNING_LOCALLY

# Snapshot of the active DB path for the handful of test scripts that read
# mod.DB_PATH directly. The live source of truth is core.db_path().
DB_PATH = db_path()

# Day 36 (Phase 3): auth routes (login/register/setup/invite-codes) as a
# Blueprint — see routes_auth.py. Every URL path is byte-identical to before
# the move, so this needed zero frontend changes.
app.register_blueprint(routes_auth.bp)
# Day 37: search / autocomplete / NL interpret / bookmarks / similar.
app.register_blueprint(routes_search.bp)
# Day 38: tag editing (single + bulk + cleanup preview) and auto-tagger controls.
app.register_blueprint(routes_tags.bp)
# Day 40: photo routes + admin library-maintenance tools.
app.register_blueprint(routes_images.bp)
app.register_blueprint(routes_maintenance.bp)
# Day 41: decks (logged in) + the public share-link routes.
app.register_blueprint(routes_decks.bp)
app.register_blueprint(routes_share.bp)
# Day 42: sync/upload/clip + account/Google connection.
app.register_blueprint(routes_sync.bp)
app.register_blueprint(routes_account.bp)

# Day 35 (Phase 3): the sync_state progress dict moved to sync.py with the
# worker. Read it qualified as sync.sync_state — it is only ever mutated in
# place, never rebound, so every reader sees the same live object. Note
# /api/regenerate-thumbnails borrows it for its own progress + running-lock
# (pre-existing behaviour, not introduced by the split).
# Day 32 (Phase 3): _tag_progress / _tag_progress_lock / _sse_queues / _sse_lock
# moved to tagging.py with the worker. The tag-progress routes (routes_tags.py
# since Day 38) reach them qualified as tagging._tag_progress etc.

# Day 34 (Phase 3): CROP_SAVE_FORMATS, the _crop_queue/_crop_progress/_crop_lock/
# _crop_job_counter state, and _process_crop_jobs() all moved to crop.py — the
# destructive-write tail (backup to _Removed, then overwrite, then refresh the
# DB) moved as one inseparable block, per the V27 lesson documented there. The
# crop_image() route below queues jobs by calling crop._crop_queue.put(...)
# etc., qualified; get_crop_progress()/reset_crop_progress() stay here too,
# reading crop._crop_progress / crop._crop_lock qualified — routes don't move
# until the Day 36+ blueprint work.
crop.start_crop_worker()

# Day 32 (Phase 3): GEMINI_TAGGING_PROMPT moved to tagging.py with the worker
# that uses it. Day 37: NL_INTERPRET_PROMPT moved to routes_search.py with
# /api/interpret, the only route that uses it.

# ============================================================================
# BOOT-TIME SELF-TEST — run_self_test() moved to decks_common.py (Day 41),
# next to the deck helpers it exercises; imported below and passed to
# init_db(run_self_test=...) exactly as before.
# ============================================================================

# ============================================================================
# AUTH — LOGIN, SESSIONS, INVITE CODES -> backend/routes_auth.py (Day 36 / V83)
# ============================================================================
# Every route (setup, login, logout, /api/auth/me, register, forgot/reset
# password, invite-codes) moved to routes_auth.py as a Blueprint, registered
# above. PUBLIC_API_ROUTES / RUNNING_LOCALLY / admin_required / current_user_id
# / adopt_session_from_header moved to core.py — the shared pieces every
# future route blueprint needs without importing app.py. Only the gate's
# REGISTRATION stays here: @app.before_request needs the `app` object, which
# core.check_login_required() takes as a parameter rather than closing over.
#
# Login-lockout constants/helpers (LOGIN_LOCK_THRESHOLD, _rate_limited, …) and
# current_user_row() are only ever used by routes_auth.py's own routes, so
# they moved there rather than to core.py — read them qualified
# (routes_auth._rate_limited(), routes_auth.LOGIN_LOCK_THRESHOLD, …) if a
# future route here ever needs them.

@app.before_request
def require_login():
    return check_login_required(app)

# ============================================================================
# GEMINI KEYS & USAGE  -> backend/gemini.py (Day 30 / V72)
# TAGGING WORKER + SSE PROGRESS  -> backend/tagging.py (Day 32 / V74)
# ============================================================================
# gemini.py: _fernet, encrypt_secret, decrypt_secret, set/get_user_gemini_key,
#   record_gemini_usage, ENCRYPTED_PREFIX — call sites here qualified gemini.*
# tagging.py: _tag_progress / _sse_queues (+ their locks), GEMINI_TAGGING_PROMPT,
#   _broadcast_progress, _select_pending_for_tagging, _run_tagging_job[_inner],
#   trigger_tagging — the tag-progress routes (routes_tags.py since Day 38) read
#   tagging._tag_progress etc.; upload/clip here call tagging.trigger_tagging().
#   genai_client stays imported in app.py too: /api/models still calls it directly (/api/interpret moved to
#   routes_search.py on Day 37).

# ============================================================================
# GOOGLE DRIVE & SYNC FUNCTIONS
# ============================================================================
# Day 29 (Phase 3): get_drive_service, get_user_drive_service,
# get_user_credentials, get_oauth_flow, get_service_account_email,
# parse_drive_folder_id, list_images_in_folder, get_root_folder_id,
# get_or_create_removed_folder, download_drive_file, drive_error_reason —
# plus REMOVED_FOLDER_NAME, PERSONAL_LIBRARY_CAP, UPLOAD_SCOPES — all moved to
# drive.py. Call them qualified: drive.get_drive_service(), etc.
# (Day 35: sync_folder_worker() and reconcile_drive_changes(), which used to
# live below this note, are now in sync.py.)

# ============================================================================
# V27: MONTHLY DATABASE BACKUP TO DRIVE
# ============================================================================
# Day 33 (Phase 3): run_db_backup, _backup_due, _backup_scheduler_loop,
# start_backup_scheduler, get_or_create_backups_folder + BACKUP_FOLDER_NAME /
# KEEP_BACKUP_COUNT all moved to backup.py (imports core + drive). The two
# Flask routes below (/api/backups/status, /api/backups/run) stay here and call
# backup.run_db_backup() / backup.KEEP_BACKUP_COUNT qualified. app.py boot calls
# backup.start_backup_scheduler().

# Day 31 (Phase 3): build_image_dict / hydrate_image_rows / save_palette and
# the four boot-time backfills (backfill_palettes / backfill_phashes /
# backfill_notes_fts / merge_plural_tag_duplicates) moved to images_common.py.
# Call sites are qualified images_common.<name>().
# Day 35 (Phase 3): sync_folder_worker() moved to sync.py, along with the
# sync_state dict (above) and the V30 half-the-library delete guard. The
# /api/sync/start route below launches it as sync.sync_folder_worker.

# ============================================================================
# API ROUTES
# ============================================================================

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

@app.route('/api/config', methods=['GET'])
def config():
    return jsonify({
        'app_name': 'Frame Atlas', 'version': 'V17', 'gemini_model': GEMINI_MODEL,
        # Both safe to expose to any logged-in browser: the OAuth client id
        # is meant to be public (only the client SECRET is sensitive, and
        # that never leaves the server), and the Picker key is restricted
        # server-side (Google Cloud Console) to the Picker API only.
        'google_client_id': os.environ.get('GOOGLE_OAUTH_CLIENT_ID'),
        'google_picker_api_key': os.environ.get('GOOGLE_PICKER_API_KEY'),
    })

# Day 42 (Phase 3): the rest of the old API ROUTES section moved out —
#   routes_sync.py      folder sync, /api/folders, /api/upload, /api/clip
#   routes_account.py   setup checklist, Gemini key + spend, Google connect
#   routes_maintenance  /api/backups/*, /api/models (admin tools)
# health and config stay here: they describe the app itself.

# ============================================================================
# DECKS + SCENES — moved (Day 41 / Phase 3)
# ============================================================================

# Helpers + the boot self-test: decks_common.py. Logged-in deck routes:
# routes_decks.py. Public share-link routes (no login): routes_share.py.

# ============================================================================
# DAY 13 (V12): ANALYTICS + UTILITY VIEWS
# ============================================================================

@app.route('/api/views/<view>')
def get_utility_view(view):
    """Filtered image lists for the Day 13 utility views.

    /api/views/favorites          — all starred images
    /api/views/recent?days=7      — images added in the last N days
                                    (?limit=30 caps how many come back)

    Returns the same full image dicts as /api/search, so the frontend can
    reuse the grid + detail panel unchanged.

    (A third view, 'flagged', was removed in V55 along with its two routes
    below and the whole Flagged nav item — see the session log. The initial
    V55 pass left is_flagged actively computed and served everywhere anyway
    (fav_flag_cols() ran an EXISTS subquery against user_flags for every
    image row on every request, and every image dict still carried the
    result) — real per-request cost for a value nothing read anymore.
    Corrected in the same session: the SQL helper (renamed favorite_col())
    now only computes is_favorite, and no response includes is_flagged. The
    user_flags table and the legacy is_flagged column on `images` deliberately
    stay as inert storage — cheap to keep, and there in case a flag-style
    feature comes back.)
    """
    uid = session['user_id']

    if view == 'favorites':
        where, params = 'user_id = ? AND id IN (SELECT image_id FROM user_favorites WHERE user_id = ?)', [uid, uid]
    elif view == 'recent':
        try:
            days = max(1, int(request.args.get('days', 7)))
        except ValueError:
            days = 7
        where, params = "user_id = ? AND date_added >= datetime('now', ?)", [uid, f'-{days} days']
    else:
        return jsonify({'error': 'Unknown view'}), 404

    limit_sql = ''
    limit_params = []
    limit_raw = request.args.get('limit', '').strip()
    if limit_raw:
        try:
            limit_sql = 'LIMIT ?'
            limit_params = [max(1, int(limit_raw))]
        except ValueError:
            limit_sql = ''
            limit_params = []

    conn = get_db()
    c = conn.cursor()
    rows = c.execute(f'''
        SELECT id, filename, thumbnail_blob, caption, aspect_ratio, md5_checksum,
               camera_rig, lens, lens_filter, stop, onset_notes, {favorite_col(uid)}
        FROM images WHERE {where}
        ORDER BY date_added DESC {limit_sql}
    ''', params + limit_params).fetchall()
    total = c.execute(f'SELECT COUNT(*) FROM images WHERE {where}', params).fetchone()[0]

    images_out = images_common.hydrate_image_rows(c, rows)
    conn.close()
    return jsonify({'images': images_out, 'total': total})

# ============================================================================
# V14: SHUFFLED HOME FEED — VIEW LOG
# ============================================================================

@app.route('/api/views/log', methods=['POST'])
def log_image_views():
    """Record that the logged-in user scrolled past these images just now.

    The frontend batches IDs as tiles enter the viewport and flushes them when
    the user leaves the page (tab hidden / navigated away). Flushing only on
    exit — never mid-scroll — keeps the shuffled order stable while paginating:
    nothing an ORDER BY depends on changes until the visit is over.

    Upsert per image: one row per (user, image), bumping last_seen_at and
    seen_count on repeat views.
    """
    uid = session['user_id']
    data = request.get_json(silent=True) or {}
    raw_ids = data.get('image_ids', [])
    if not isinstance(raw_ids, list):
        return jsonify({'error': 'image_ids must be a list'}), 400
    ids = [int(i) for i in raw_ids if str(i).isdigit()][:500]
    if not ids:
        return jsonify({'logged': 0})

    conn = get_db()
    c = conn.cursor()
    ph = ','.join('?' * len(ids))
    owned = [r[0] for r in c.execute(
        f'SELECT id FROM images WHERE user_id = ? AND id IN ({ph})', [uid] + ids
    ).fetchall()]
    for image_id in owned:
        c.execute('''
            INSERT INTO image_views (user_id, image_id, last_seen_at, seen_count)
            VALUES (?, ?, CURRENT_TIMESTAMP, 1)
            ON CONFLICT(user_id, image_id)
            DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP,
                          seen_count = seen_count + 1
        ''', (uid, image_id))
    conn.commit()
    conn.close()
    return jsonify({'logged': len(owned)})

@app.route('/api/analytics')
def analytics():
    """Read-only rollups for the Analytics dashboard. One call returns
    everything the page needs: headline totals, tag counts grouped by
    category (the frontend picks which categories to chart), and library
    growth by month (added + running total)."""
    uid = session['user_id']
    conn = get_db()
    c = conn.cursor()

    totals = {
        'images': c.execute('SELECT COUNT(*) FROM images WHERE user_id = ?', (uid,)).fetchone()[0],
        'favorites': c.execute('SELECT COUNT(*) FROM user_favorites WHERE user_id = ?', (uid,)).fetchone()[0],
        'added_last_7_days': c.execute(
            "SELECT COUNT(*) FROM images WHERE user_id = ? AND date_added >= datetime('now', '-7 days')", (uid,)
        ).fetchone()[0],
        'tags': c.execute('SELECT COUNT(*) FROM tags WHERE user_id = ?', (uid,)).fetchone()[0],
        'distinct_tags': c.execute('SELECT COUNT(DISTINCT value) FROM tags WHERE user_id = ?', (uid,)).fetchone()[0],
        'decks': c.execute('SELECT COUNT(*) FROM decks WHERE user_id = ?', (uid,)).fetchone()[0],
    }

    categories = {}
    for row in c.execute('''
        SELECT category, value, COUNT(*) AS cnt FROM tags
        WHERE user_id = ?
        GROUP BY category, value
        ORDER BY cnt DESC, value ASC
    ''', (uid,)).fetchall():
        categories.setdefault(row['category'], []).append(
            {'value': row['value'], 'count': row['cnt']}
        )

    growth = []
    running = 0
    for row in c.execute('''
        SELECT strftime('%Y-%m', date_added) AS month, COUNT(*) AS cnt
        FROM images WHERE user_id = ? GROUP BY month ORDER BY month ASC
    ''', (uid,)).fetchall():
        running += row['cnt']
        growth.append({'month': row['month'], 'added': row['cnt'], 'total': running})

    conn.close()
    return jsonify({
        'totals': totals,
        'categories': categories,
        'category_labels': CAT_LABELS,
        'category_colors': CAT_COLORS,
        'growth': growth,
    })

@app.route('/api/analytics/users')
@admin_required
def analytics_users():
    """Admin-only rollup across every account — aggregate totals plus a
    per-user breakdown (content, storage, activity). Storage is estimated
    from thumbnail_blob size since that's the only binary data stored
    per-image; it's an approximation, not an exact DB page count."""
    conn = get_db()
    c = conn.cursor()

    users = c.execute('''
        SELECT id, username, email, role, created_at, last_login_at
        FROM users ORDER BY id ASC
    ''').fetchall()

    per_user = []
    for u in users:
        uid = u['id']
        image_count = c.execute('SELECT COUNT(*) FROM images WHERE user_id = ?', (uid,)).fetchone()[0]
        tag_count = c.execute('SELECT COUNT(*) FROM tags WHERE user_id = ?', (uid,)).fetchone()[0]
        deck_count = c.execute('SELECT COUNT(*) FROM decks WHERE user_id = ?', (uid,)).fetchone()[0]
        storage_bytes = c.execute(
            'SELECT COALESCE(SUM(LENGTH(thumbnail_blob)), 0) FROM images WHERE user_id = ?', (uid,)
        ).fetchone()[0]
        sync_row = c.execute(
            'SELECT folder_name, last_sync FROM sync_settings WHERE user_id = ? ORDER BY id DESC LIMIT 1', (uid,)
        ).fetchone()

        per_user.append({
            'id': uid,
            'name': _display_name(u),
            'email': u['email'],
            'role': u['role'],
            'created_at': u['created_at'],
            'last_login_at': u['last_login_at'],
            'image_count': image_count,
            'image_cap': None if uid == 1 else drive.PERSONAL_LIBRARY_CAP,
            'tag_count': tag_count,
            'deck_count': deck_count,
            'storage_bytes': storage_bytes,
            'folder_name': sync_row['folder_name'] if sync_row else None,
            'last_sync': sync_row['last_sync'] if sync_row else None,
        })

    aggregate = {
        'total_users': len(users),
        'total_images': sum(u['image_count'] for u in per_user),
        'total_storage_bytes': sum(u['storage_bytes'] for u in per_user),
        'active_last_7_days': c.execute(
            "SELECT COUNT(*) FROM users WHERE last_login_at >= datetime('now', '-7 days')"
        ).fetchone()[0],
    }

    conn.close()
    return jsonify({'aggregate': aggregate, 'users': per_user})

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path.startswith('api/'):
        from flask import abort
        abort(404)
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    full_path = os.path.join(static_dir, path)
    if path and os.path.exists(full_path):
        return send_from_directory(static_dir, path)
    return send_from_directory(static_dir, 'index.html')

# ============================================================================
# STARTUP
# ============================================================================

if __name__ == '__main__':
    init_db(run_self_test=run_self_test)
    load_embeddings_seed()
    images_common.backfill_palettes()
    images_common.backfill_phashes()
    images_common.backfill_notes_fts()
    images_common.merge_plural_tag_duplicates()
    threading.Thread(target=sync.reconcile_drive_changes, daemon=True).start()
    backup.start_backup_scheduler()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

init_db(run_self_test=run_self_test)
load_embeddings_seed()
images_common.backfill_palettes()
images_common.backfill_phashes()
images_common.backfill_notes_fts()
images_common.merge_plural_tag_duplicates()
threading.Thread(target=sync.reconcile_drive_changes, daemon=True).start()
backup.start_backup_scheduler()

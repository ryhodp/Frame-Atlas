import os
import json
import base64
import secrets
import io
import re
import sqlite3
import time
import zlib
import threading
import queue as queue_module
import concurrent.futures
import urllib.parse
from array import array
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, jsonify, request, send_file, send_from_directory, Response, stream_with_context, redirect, session
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image
# Day 29 (Phase 3): all Google Drive connection/auth/folder code moved to
# drive.py. MediaIoBaseDownload/Upload stay here — the sync worker, backup,
# crop worker and upload route use them directly and did not move.
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google import genai as genai_client

from pdf_export import build_deck_pdf, pdf_download_name, LAYOUTS as PDF_LAYOUTS

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
# imported here too — /api/interpret and /api/models still use it directly.
import tagging

# Day 31 (Phase 3): image-row hydration (build_image_dict / hydrate_image_rows
# / _fetch_image_dict), the palette writer, and the four boot-time backfills.
# Call sites are qualified (images_common.build_image_dict(); test scripts
# reach them as mod.images_common.<name>).
import images_common
import backup
import crop
import sync


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
RUNNING_LOCALLY = bool(os.environ.get('FA_DB_PATH'))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = not RUNNING_LOCALLY

# Snapshot of the active DB path for the handful of test scripts that read
# mod.DB_PATH directly. The live source of truth is core.db_path().
DB_PATH = db_path()

# Day 35 (Phase 3): the sync_state progress dict moved to sync.py with the
# worker. Read it qualified as sync.sync_state — it is only ever mutated in
# place, never rebound, so every reader sees the same live object. Note
# /api/regenerate-thumbnails borrows it for its own progress + running-lock
# (pre-existing behaviour, not introduced by the split).
# Day 32 (Phase 3): _tag_progress / _tag_progress_lock / _sse_queues / _sse_lock
# moved to tagging.py with the worker. The tag-progress routes below reach them
# qualified as tagging._tag_progress etc.

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
# that uses it. NL_INTERPRET_PROMPT stays — /api/interpret (search) still uses it.

NL_INTERPRET_PROMPT = """You translate a cinematographer's search phrase into tags from a fixed taxonomy.

ALLOWED TAGS (use ONLY these, exactly as written):
mood: lonely, intimate, tense, ominous, serene, chaotic, melancholic, warm, euphoric, epic, mundane, dreamlike, claustrophobic, vast
lighting_quality: hard, soft, motivated, unmotivated, single-source, practical-heavy, high-key, low-key, no-fill, bounce-heavy, silhouette, chiaroscuro
lighting_color_temperature: warm-tungsten, cool-daylight, mixed-sources, green-practical, neon, firelight, moonlight
color_palette: desaturated, high-contrast, monochromatic, warm-palette, cool-palette, earthy, high-saturation, bleach-bypass, golden, teal-orange
shot_type: extreme-wide, wide, medium-wide, medium, close-up, extreme-close-up, aerial, POV, over-shoulder, two-shot
framing_composition: centered, rule-of-thirds, dutch-angle, low-angle, high-angle, eye-level, negative-space, symmetrical, foreground-frame
location_type: interior, exterior, diner, hospital, warehouse, rooftop, forest, urban-street, office, home, car, bar, stage, industrial, desert, water
time_of_day_weather: golden-hour, magic-hour, midday, blue-hour, night, overcast, dawn, rain, fog, snow, harsh-sun
source_type: film-still, BTS, production-still, mood-texture, abstract
subject_count: no-subject, solo, pair, group, crowd
subject_camera_relationship: looking-at-camera, looking-away, profile, back-to-camera
performance_emotion: joy, grief, fear, rage, longing, neutral, shock, tenderness, defiance
genre_aesthetic: horror, western, sci-fi, romance, documentary, thriller, noir, drama, comedy, action
era_decade: period-piece, 70s, 80s, 90s, contemporary, futuristic
camera_format: 35mm-film, 16mm-film, anamorphic, spherical, digital, arri, red, sony, blackmagic
subjects: man, woman, child, couple, wedding, hand, hands, body, face, animal, dog, cat, horse, bird, building, house, car, door, window, street, bridge, fire, water, mirror, glass, weapon, crowd, performance

Pick the 2-5 tags that best capture the FEELING and VISUAL QUALITIES of the phrase.
Return ONLY a JSON array of tag strings, e.g. ["lonely","low-key","night"]. No markdown, no explanation.

Phrase: """

# ============================================================================
# BOOT-TIME SELF-TEST
# (schema build + migrations live in schema.py; init_db() calls back into
#  run_self_test here because it exercises deck helpers defined in this file)
# ============================================================================

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


# ============================================================================
# AUTH — LOGIN, SESSIONS, INVITE CODES (Day 14 / V13)
# ============================================================================

# Reachable without being logged in. Exact-path matches, plus anything under
# /api/share/ (public read-only deck links). Non-API paths are never gated
# here — the React app shell always loads; it's the frontend's own routing
# that decides whether to show a login screen.
PUBLIC_API_ROUTES = {
    '/api/health',
    '/api/auth/login',
    '/api/auth/register',
    '/api/auth/me',
    '/api/auth/forgot-password',
    '/api/auth/reset-password',
    '/api/setup',
    '/api/setup/status',
}

def _adopt_session_from_header():
    """Let the browser extension reuse the login you already have.

    Session cookies are SameSite-restricted, so they don't ride along on a
    request made from a chrome-extension:// origin. The extension reads the
    cookie itself (chrome.cookies) and echoes the value in X-FA-Session; this
    verifies that signature with Flask's own serializer and adopts it.

    Not a CSRF hole: a custom header can't be set by a cross-site form or
    image tag, and the value is the very cookie the caller would have needed
    anyway — no new capability, just a different envelope.
    """
    raw = request.headers.get('X-FA-Session')
    if not raw:
        return
    serializer = app.session_interface.get_signing_serializer(app)
    if serializer is None:
        return
    try:
        data = serializer.loads(
            raw, max_age=int(app.permanent_session_lifetime.total_seconds())
        )
    except Exception:
        return          # forged, tampered with, or simply expired
    if isinstance(data, dict) and data.get('user_id'):
        session.update(data)


@app.before_request
def require_login():
    path = request.path
    if not path.startswith('/api/'):
        return None
    if path in PUBLIC_API_ROUTES or path.startswith('/api/share/'):
        return None
    if not session.get('user_id'):
        _adopt_session_from_header()
    if session.get('user_id'):
        return None
    return jsonify({'error': 'login_required'}), 401

def current_user_id():
    return session.get('user_id')

def current_user_row():
    uid = session.get('user_id')
    if not uid:
        return None
    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT id, username, email, role FROM users WHERE id = ?', (uid,)).fetchone()
    conn.close()
    return row

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get('role') != 'admin':
            return jsonify({'error': 'admin_required'}), 403
        return fn(*args, **kwargs)
    return wrapper

# favorite_col() moved to core.py in Day 31 (Phase 3) — imported back above so
# every SELECT that inlines it is unchanged.

# ── V44 (Day 26): LOGIN THROTTLING ──────────────────────────────────────────
# Before this there was no limit at all: passwords could be guessed as fast as
# requests could be sent. werkzeug's pbkdf2 hashing was already correct, but
# hashing only makes a STOLEN database expensive to crack — it does nothing
# about guessing against a live login form.
#
# Keyed on the ACCOUNT, never the caller's IP address. Two reasons, both
# deliberate: an attacker can rotate IPs freely (so IP throttling buys almost
# nothing), and this app is explicitly used from shared networks — CLAUDE.md's
# own V43 notes cite hotel and set wifi as normal conditions — where one
# guesser would otherwise lock out everyone else sharing that connection.
LOGIN_LOCK_THRESHOLD = 5      # wrong passwords allowed before any lock kicks in
LOGIN_LOCK_BASE_SECONDS = 30  # first lock; doubles per failure past the threshold
LOGIN_LOCK_MAX_SECONDS = 3600  # ceiling, so an account is never bricked outright

def _login_lock_remaining(locked_until):
    """Seconds still to wait on a lock, or 0 if not locked. An unparseable
    timestamp counts as NOT locked — a corrupted value must never be able to
    permanently lock a real user out of their own account."""
    if not locked_until:
        return 0
    try:
        until = datetime.fromisoformat(str(locked_until))
    except (TypeError, ValueError) as e:
        print(f"[auth] Ignoring unparseable login_locked_until value {locked_until!r}: {e}")
        return 0
    return max(0, int((until - datetime.now()).total_seconds()))

def _format_lock_wait(seconds):
    """Human wording for the retry message — '45 seconds' / '3 minutes'."""
    if seconds < 60:
        return f'{seconds} second{"" if seconds == 1 else "s"}'
    minutes = (seconds + 59) // 60
    return f'{minutes} minute{"" if minutes == 1 else "s"}'

def _record_failed_login(row):
    """Bump the consecutive-failure counter and, past the threshold, set an
    exponentially growing lockout window."""
    count = (row['failed_login_count'] or 0) + 1
    locked_until = None
    if count >= LOGIN_LOCK_THRESHOLD:
        # 5th failure -> base, 6th -> 2x, 7th -> 4x, ... capped.
        wait = min(LOGIN_LOCK_BASE_SECONDS * (2 ** (count - LOGIN_LOCK_THRESHOLD)), LOGIN_LOCK_MAX_SECONDS)
        locked_until = datetime.now() + timedelta(seconds=wait)
        print(f"[auth] '{row['username']}' hit {count} consecutive failed logins — locked for {wait}s")

    conn = get_db()
    conn.execute(
        'UPDATE users SET failed_login_count = ?, login_locked_until = ? WHERE id = ?',
        (count, locked_until.isoformat(sep=' ', timespec='seconds') if locked_until else None, row['id'])
    )
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# Day 28 (Phase 3): rate limiting on the public, unauthenticated auth
# endpoints. Login already has per-account throttling (V44); this caps
# /api/auth/register and /api/auth/forgot-password, which have no account to
# key on yet. Hand-rolled against a tiny table (rate_limit_hits, built in
# schema.py) — same reasoning as the V44 lockout: no Flask-Limiter dependency,
# no added Railway deploy time.
#
# Keyed on IP here (unlike the login lockout, which is per-account) — it's the
# only identifier a pre-account request has. Best-effort by nature: an
# attacker behind many IPs isn't stopped, but casual scripted abuse from one
# host is. Disabled entirely for local runs so the test suite and local dev
# aren't throttled.
RATE_LIMIT_MAX = 5              # allowed hits per IP...
RATE_LIMIT_WINDOW_SECONDS = 60  # ...per this rolling window

def _client_ip():
    """Best-guess caller IP. ProxyFix only normalises proto/host, not
    X-Forwarded-For, so read the header ourselves. Take the LAST entry — the
    one Railway's own proxy appended — since earlier entries are
    client-supplied and trivially spoofed."""
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[-1].strip() or 'unknown'
    return request.remote_addr or 'unknown'

def _rate_limited(scope):
    """Record this request and return True if this IP has already exceeded
    RATE_LIMIT_MAX hits on `scope` within the window. Fails OPEN on any DB
    error (returns False) — a limiter that 500s the endpoint it guards is
    worse than the abuse it prevents. No-op locally."""
    if RUNNING_LOCALLY:
        return False
    ip = _client_ip()
    now = datetime.now()
    cutoff = (now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)).isoformat(sep=' ', timespec='seconds')
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('DELETE FROM rate_limit_hits WHERE hit_at < ?', (cutoff,))
        recent = c.execute(
            'SELECT COUNT(*) FROM rate_limit_hits WHERE scope = ? AND client_ip = ? AND hit_at >= ?',
            (scope, ip, cutoff)
        ).fetchone()[0]
        c.execute('INSERT INTO rate_limit_hits (scope, client_ip, hit_at) VALUES (?, ?, ?)',
                  (scope, ip, now.isoformat(sep=' ', timespec='seconds')))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ratelimit] check failed for {scope} ({e}) — allowing request through")
        return False
    if recent >= RATE_LIMIT_MAX:
        print(f"[ratelimit] {ip} exceeded {RATE_LIMIT_MAX}/{RATE_LIMIT_WINDOW_SECONDS}s on {scope}")
        return True
    return False

@app.route('/api/setup/status')
def setup_status():
    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT password_hash FROM users WHERE id = 1').fetchone()
    conn.close()
    return jsonify({'needs_setup': not bool(row and row['password_hash'])})

@app.route('/api/setup', methods=['POST'])
def setup_admin():
    """One-time admin bootstrap. The moment user 1 has a password set, this
    route refuses forever — the password itself is the lock, so there's no
    separate flag to leave open by mistake."""
    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT password_hash FROM users WHERE id = 1').fetchone()
    if row and row['password_hash']:
        conn.close()
        return jsonify({'error': 'Setup already completed'}), 403

    data = request.get_json(force=True) or {}
    password = data.get('password') or ''
    email = (data.get('email') or '').strip()
    if len(password) < 8:
        conn.close()
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    if not email:
        conn.close()
        return jsonify({'error': 'Email is required'}), 400

    c.execute(
        'UPDATE users SET password_hash = ?, email = ?, role = ? WHERE id = 1',
        (generate_password_hash(password), email, 'admin')
    )
    conn.commit()
    conn.close()

    session['user_id'] = 1
    session['username'] = 'ryan'
    session['role'] = 'admin'
    return jsonify({'success': True, 'user': {'id': 1, 'username': 'ryan', 'email': email, 'role': 'admin'}})

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json(force=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        'SELECT id, username, email, role, password_hash, failed_login_count, login_locked_until '
        'FROM users WHERE username = ? COLLATE NOCASE',
        (username,)
    ).fetchone()
    conn.close()

    # V44 (Day 26): the lockout check runs BEFORE the password check, so a
    # locked account can't be probed at all — otherwise the throttle would
    # still leak "was that the right password?" one attempt at a time.
    if row:
        locked_for = _login_lock_remaining(row['login_locked_until'])
        if locked_for > 0:
            print(f"[auth] Rejected login for '{row['username']}' — locked for another {locked_for}s")
            return jsonify({
                'error': f'Too many failed attempts. Try again in {_format_lock_wait(locked_for)}.',
                'locked': True,
                'retry_after_seconds': locked_for,
            }), 429

    if not row or not row['password_hash'] or not check_password_hash(row['password_hash'], password):
        if row:
            _record_failed_login(row)
        return jsonify({'error': 'Invalid username or password'}), 401

    conn = get_db()
    # A successful login clears the throttle — the counter tracks CONSECUTIVE
    # failures, so a legitimate user who mistypes twice then gets it right
    # starts clean rather than creeping toward a lockout over weeks.
    conn.execute(
        "UPDATE users SET last_login_at = CURRENT_TIMESTAMP, failed_login_count = 0, "
        "login_locked_until = NULL WHERE id = ?",
        (row['id'],)
    )
    conn.commit()
    conn.close()

    session['user_id'] = row['id']
    session['username'] = row['username']
    session['role'] = row['role']
    return jsonify({'success': True, 'user': {
        'id': row['id'], 'username': row['username'], 'email': row['email'], 'role': row['role']
    }})

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/auth/me')
def me():
    if not session.get('user_id'):
        return jsonify({'logged_in': False})
    row = current_user_row()
    if not row:
        session.clear()
        return jsonify({'logged_in': False})
    return jsonify({'logged_in': True, 'user': {
        'id': row['id'], 'username': row['username'], 'email': row['email'], 'role': row['role']
    }})

@app.route('/api/auth/register', methods=['POST'])
def register():
    if _rate_limited('register'):
        return jsonify({'error': 'Too many attempts. Please wait a minute and try again.'}), 429
    data = request.get_json(force=True) or {}
    invite_code = (data.get('invite_code') or '').strip()
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not invite_code or not username or not email or len(password) < 8:
        return jsonify({'error': 'Invite code, username, email, and an 8+ character password are all required'}), 400
    if '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'error': 'That email address doesn\'t look right'}), 400

    conn = get_db()
    c = conn.cursor()

    invite = c.execute(
        'SELECT id FROM invite_codes WHERE code = ? AND used_by IS NULL', (invite_code,)
    ).fetchone()
    if not invite:
        conn.close()
        return jsonify({'error': 'Invite code is invalid or already used'}), 400

    if c.execute('SELECT 1 FROM users WHERE username = ? COLLATE NOCASE', (username,)).fetchone():
        conn.close()
        return jsonify({'error': 'That username is taken'}), 400
    if c.execute('SELECT 1 FROM users WHERE email = ? COLLATE NOCASE', (email,)).fetchone():
        conn.close()
        return jsonify({'error': 'An account with that email already exists'}), 400

    c.execute(
        'INSERT INTO users (username, password_hash, role, email) VALUES (?, ?, ?, ?)',
        (username, generate_password_hash(password), 'user', email)
    )
    new_user_id = c.lastrowid
    c.execute(
        'UPDATE invite_codes SET used_by = ?, used_at = CURRENT_TIMESTAMP WHERE id = ?',
        (new_user_id, invite['id'])
    )
    conn.commit()
    conn.close()

    session['user_id'] = new_user_id
    session['username'] = username
    session['role'] = 'user'
    return jsonify({'success': True, 'user': {'id': new_user_id, 'username': username, 'email': email, 'role': 'user'}})


@app.route('/api/auth/forgot-password', methods=['POST'])
def forgot_password():
    # NOTE (Day 28): this endpoint still returns the reset token in its JSON
    # response — no email is sent yet. Until email delivery is wired up
    # (Flask-Mail / Mailgun), treat this as an ADMIN-ONLY recovery path, not
    # something to point friends at. The token is 256-bit, one-time-use, and
    # expires in 1 hour, but a token in an HTTP response is a token anyone on
    # the wire or in a log can use. See CLAUDE.md → Auth.
    if _rate_limited('forgot-password'):
        return jsonify({'error': 'Too many attempts. Please wait a minute and try again.'}), 429
    data = request.get_json(force=True) or {}
    email = (data.get('email') or '').strip().lower()
    if not email:
        return jsonify({'error': 'Email is required'}), 400

    conn = get_db()
    c = conn.cursor()
    user = c.execute('SELECT id, username FROM users WHERE email = ? COLLATE NOCASE', (email,)).fetchone()
    if not user:
        conn.close()
        return jsonify({'error': 'No account uses that email address'}), 404

    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=1)
    c.execute(
        'INSERT INTO password_resets (user_id, token, expires_at) VALUES (?, ?, ?)',
        (user['id'], token, expires_at)
    )
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'username': user['username'], 'reset_path': f'/reset-password?token={token}'})


@app.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json(force=True) or {}
    token = (data.get('token') or '').strip()
    password = data.get('password') or ''
    if not token or len(password) < 8:
        return jsonify({'error': 'A valid reset link and an 8+ character password are required'}), 400

    conn = get_db()
    c = conn.cursor()
    reset = c.execute(
        'SELECT id, user_id, expires_at, used_at FROM password_resets WHERE token = ?', (token,)
    ).fetchone()
    if not reset or reset['used_at'] or datetime.utcnow() > datetime.fromisoformat(reset['expires_at']):
        conn.close()
        return jsonify({'error': 'This reset link is invalid or has expired. Request a new one.'}), 400

    c.execute('UPDATE users SET password_hash = ? WHERE id = ?', (generate_password_hash(password), reset['user_id']))
    c.execute('UPDATE password_resets SET used_at = CURRENT_TIMESTAMP WHERE id = ?', (reset['id'],))
    conn.commit()
    conn.close()

    return jsonify({'success': True})

@app.route('/api/admin/invite-codes', methods=['GET'])
@admin_required
def list_invite_codes():
    conn = get_db()
    c = conn.cursor()
    rows = c.execute('''
        SELECT ic.id, ic.code, ic.created_at, ic.used_at, u.username AS used_by_username
        FROM invite_codes ic
        LEFT JOIN users u ON u.id = ic.used_by
        ORDER BY ic.created_at DESC
    ''').fetchall()
    conn.close()
    return jsonify([{
        'id': r['id'], 'code': r['code'], 'created_at': r['created_at'],
        'used_at': r['used_at'], 'used_by_username': r['used_by_username']
    } for r in rows])

@app.route('/api/admin/invite-codes', methods=['POST'])
@admin_required
def create_invite_code():
    code = secrets.token_urlsafe(8)
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO invite_codes (code, created_by) VALUES (?, ?)', (code, session['user_id']))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'id': new_id, 'code': code})

@app.route('/api/admin/invite-codes/<int:invite_id>', methods=['DELETE'])
@admin_required
def revoke_invite_code(invite_id):
    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT used_by FROM invite_codes WHERE id = ?', (invite_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Invite code not found'}), 404
    if row['used_by'] is not None:
        conn.close()
        return jsonify({'error': 'Already used, cannot revoke'}), 400
    c.execute('DELETE FROM invite_codes WHERE id = ?', (invite_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ============================================================================
# GEMINI KEYS & USAGE  -> backend/gemini.py (Day 30 / V72)
# TAGGING WORKER + SSE PROGRESS  -> backend/tagging.py (Day 32 / V74)
# ============================================================================
# gemini.py: _fernet, encrypt_secret, decrypt_secret, set/get_user_gemini_key,
#   record_gemini_usage, ENCRYPTED_PREFIX — call sites here qualified gemini.*
# tagging.py: _tag_progress / _sse_queues (+ their locks), GEMINI_TAGGING_PROMPT,
#   _broadcast_progress, _select_pending_for_tagging, _run_tagging_job[_inner],
#   trigger_tagging — the tag-progress routes below read tagging._tag_progress
#   etc. and call tagging.trigger_tagging(). genai_client stays imported in
#   app.py too: /api/interpret and /api/models still call it directly.

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

@app.route('/api/tag/retry-failed', methods=['POST'])
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

@app.route('/api/models', methods=['GET'])
@admin_required
def list_models():
    """Diagnostic: list Gemini models this API key can use. Kept on purpose
    (Day 13 decision) — this is the first-stop check when auto-tagging
    mass-fails because Google retired the model in GEMINI_MODEL."""
    gemini_api_key = os.environ.get('GEMINI_API_KEY')
    if not gemini_api_key:
        return jsonify({'error': 'GEMINI_API_KEY not set'}), 500
    try:
        client = genai_client.Client(api_key=gemini_api_key)
        names = [m.name for m in client.models.list()]
        return jsonify({'current': GEMINI_MODEL, 'available': names})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/folders', methods=['GET'])
@admin_required
def get_folders():
    return jsonify({'folders': [
        {'id': '1LHPVyo3QjOEcizc1Io2UVjxzX4FQ7yDG', 'name': 'Inspiration Images'}
    ]})

@app.route('/api/sync/settings', methods=['GET', 'POST'])
def sync_settings():
    user_id = session['user_id']

    if request.method == 'GET':
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT folder_id, folder_name, last_sync FROM sync_settings WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        conn.close()
        if row:
            return jsonify({'folder_id': row[0], 'folder_name': row[1], 'last_sync': row[2]})
        return jsonify({'folder_id': None, 'folder_name': None, 'last_sync': None})

    elif request.method == 'POST':
        data = request.get_json()
        folder_id = data.get('folder_id')
        folder_name = data.get('folder_name')
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT id FROM sync_settings WHERE user_id = ?', (user_id,))
        exists = c.fetchone()
        if exists:
            c.execute('UPDATE sync_settings SET folder_id = ?, folder_name = ? WHERE user_id = ?',
                      (folder_id, folder_name, user_id))
        else:
            c.execute('INSERT INTO sync_settings (user_id, folder_id, folder_name) VALUES (?, ?, ?)',
                      (user_id, folder_id, folder_name))
        conn.commit()
        conn.close()
        return jsonify({'success': True})

@app.route('/api/sync/connect-folder', methods=['POST'])
def connect_folder():
    """V17: friend pastes their Drive folder link (or bare ID). We check the
    service account can actually see it — the proof that the Share step was
    done — then save it as their sync folder and report how many images are
    waiting inside."""
    user_id = session['user_id']
    data = request.get_json(silent=True) or {}
    folder_id = drive.parse_drive_folder_id(data.get('folder', ''))
    robot = drive.get_service_account_email() or 'the Frame Atlas robot email'

    if not folder_id:
        return jsonify({'error': "That doesn't look like a Drive folder link — open the folder "
                                 'in Google Drive and copy the address from the browser bar.'}), 400

    try:
        service = drive.get_drive_service()
        meta = service.files().get(fileId=folder_id, fields='id, name, mimeType').execute()
    except Exception as e:
        msg = str(e)
        if '404' in msg or 'notFound' in msg or '403' in msg or 'insufficient' in msg.lower():
            return jsonify({'error': f"Frame Atlas can't see that folder yet. In Drive: right-click "
                                     f'the folder → Share → add {robot} as a Viewer, then try again.',
                            'not_shared': True}), 403
        return jsonify({'error': f'Google Drive error: {msg}'}), 500

    if meta.get('mimeType') != 'application/vnd.google-apps.folder':
        return jsonify({'error': 'That link points to a file, not a folder — paste the link '
                                 'to the folder that holds your images.'}), 400

    try:
        image_count = len(drive.list_images_in_folder(service, folder_id))
    except Exception:
        image_count = None  # folder itself is visible; count is best-effort

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id FROM sync_settings WHERE user_id = ?', (user_id,))
    if c.fetchone():
        c.execute('UPDATE sync_settings SET folder_id = ?, folder_name = ? WHERE user_id = ?',
                  (folder_id, meta['name'], user_id))
    else:
        c.execute('INSERT INTO sync_settings (user_id, folder_id, folder_name) VALUES (?, ?, ?)',
                  (user_id, folder_id, meta['name']))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'folder_id': folder_id, 'folder_name': meta['name'],
                    'image_count': image_count})

@app.route('/api/account/setup-status', methods=['GET'])
def account_setup_status():
    """V17: everything the Home-page setup checklist and Account page need in
    one call — robot email to share with, whether a folder is connected,
    library size, and whether a Gemini key is saved."""
    user_id = session['user_id']
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT folder_id, folder_name, last_sync FROM sync_settings WHERE user_id = ?', (user_id,))
    folder = c.fetchone()
    image_count = c.execute('SELECT COUNT(*) FROM images WHERE user_id = ?', (user_id,)).fetchone()[0]
    key_row = c.execute('SELECT gemini_api_key FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return jsonify({
        'service_account_email': drive.get_service_account_email(),
        'folder_connected': bool(folder and folder['folder_id']),
        'folder_name': folder['folder_name'] if folder else None,
        'last_sync': folder['last_sync'] if folder else None,
        'image_count': image_count,
        'image_cap': None if user_id == 1 else drive.PERSONAL_LIBRARY_CAP,
        'has_gemini_key': user_id == 1 or bool(key_row and key_row['gemini_api_key']),
    })

@app.route('/api/backups/status', methods=['GET'])
@admin_required
def backups_status():
    """History of automatic monthly database backups, newest first (V27)."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT filename, created_at FROM db_backups ORDER BY created_at DESC')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify({'backups': rows, 'keep_count': backup.KEEP_BACKUP_COUNT})

@app.route('/api/backups/run', methods=['POST'])
@admin_required
def backups_run_now():
    """Manually trigger a database backup right now (V27) — for testing the
    monthly job without waiting a month, or forcing a fresh copy on demand."""
    ok = backup.run_db_backup()
    if not ok:
        return jsonify({'error': 'Backup failed — check server logs for details.'}), 500
    return jsonify({'success': True})

@app.route('/api/sync/start', methods=['POST'])
def start_sync():
    user_id = session['user_id']

    if sync.sync_state['in_progress']:
        return jsonify({'error': 'Sync already in progress', 'user_id': sync.sync_state['user_id']}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT folder_id FROM sync_settings WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return jsonify({'error': 'No sync folder configured'}), 400

    folder_id = row[0]
    thread = threading.Thread(target=sync.sync_folder_worker, args=(folder_id, user_id))
    thread.daemon = True
    thread.start()

    return jsonify({'success': True, 'message': 'Sync started'})

@app.route('/api/sync/status', methods=['GET'])
def sync_status():
    # One sync runs at a time app-wide. Only the person whose sync it is
    # (or the admin) sees filenames/errors — another user just learns the
    # slot is busy, not what's in someone else's Drive folder. (V17)
    uid = session['user_id']
    if sync.sync_state['user_id'] in (None, uid) or uid == 1:
        return jsonify({**sync.sync_state, 'yours': sync.sync_state['user_id'] in (None, uid)})
    return jsonify({'in_progress': sync.sync_state['in_progress'], 'yours': False,
                    'processed': 0, 'total': 0, 'current_file': '', 'errors': []})

@app.route('/api/tag-progress/stream')
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

@app.route('/api/tag-progress')
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

@app.route('/api/tag/start', methods=['POST'])
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

@app.route('/api/account/gemini-key', methods=['GET', 'POST'])
def account_gemini_key():
    """Non-admin users save their own Gemini key here (admin rides the shared
    Railway env var — see get_user_gemini_key). Fully optional: skipping this
    just means a friend's synced photos stay untagged but searchable."""
    uid = current_user_id()
    conn = get_db()
    c = conn.cursor()

    if request.method == 'POST':
        key = (request.get_json() or {}).get('key', '').strip()
        if not key:
            conn.close()
            return jsonify({'error': 'No key provided'}), 400
        conn.close()
        # V44: goes through set_user_gemini_key so it's encrypted at rest.
        # key_last4 is computed from what the user just typed, never read
        # back out of the database.
        gemini.set_user_gemini_key(uid, key)
        return jsonify({'success': True, 'has_key': True, 'key_last4': key[-4:]})

    row = c.execute('SELECT gemini_api_key FROM users WHERE id = ?', (uid,)).fetchone()
    conn.close()
    key = gemini.decrypt_secret(row['gemini_api_key']) if row and row['gemini_api_key'] else None
    return jsonify({'has_key': bool(key), 'key_last4': key[-4:] if key else None})

@app.route('/api/tag/mine', methods=['POST'])
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

@app.route('/api/tag-progress/mine')
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

@app.route('/api/billing/spend')
def billing_spend():
    """This month's estimated Gemini spend for the logged-in user. Only
    meaningful for someone with a usable key (admin's shared key, or a
    friend's own saved key) — everyone else gets a clear next step instead."""
    uid = current_user_id()
    if not gemini.get_user_gemini_key(uid):
        return jsonify({
            'error': 'no_key',
            'message': 'Add your Gemini API key in Account settings to track your spend.'
        }), 400

    month = datetime.utcnow().strftime('%Y-%m')
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        'SELECT input_tokens, output_tokens, cost_usd FROM gemini_usage WHERE user_id = ? AND month = ?',
        (uid, month)
    ).fetchone()
    conn.close()

    return jsonify({
        'month': month,
        'input_tokens': row['input_tokens'] if row else 0,
        'output_tokens': row['output_tokens'] if row else 0,
        'cost_usd': round(row['cost_usd'], 4) if row else 0.0,
    })

@app.route('/api/interpret', methods=['POST'])
def interpret_nl():
    phrase = (request.get_json() or {}).get('phrase', '').strip()
    if not phrase:
        return jsonify({'error': 'No phrase provided'}), 400

    uid = current_user_id()
    gemini_api_key = gemini.get_user_gemini_key(uid)
    if not gemini_api_key:
        return jsonify({'error': 'Add your Gemini API key in Account settings to use natural-language search.'}), 400

    try:
        client = genai_client.Client(api_key=gemini_api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[NL_INTERPRET_PROMPT + phrase]
        )
        gemini.record_gemini_usage(uid, getattr(response, 'usage_metadata', None))
        raw = response.text.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        tags = json.loads(raw)
        if not isinstance(tags, list):
            return jsonify({'error': 'Bad interpretation'}), 500
        tags = [str(t).strip() for t in tags if str(t).strip()][:5]
        return jsonify({'phrase': phrase, 'tags': tags})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/autocomplete')
def autocomplete():
    q = request.args.get('q', '').strip().lower()
    active_chips = [t.strip() for t in request.args.get('chips', '').split(',') if t.strip()]

    if not q:
        return jsonify([])

    uid = session['user_id']
    conn = get_db()
    c = conn.cursor()

    if active_chips:
        placeholders = ','.join('?' * len(active_chips))
        rows = c.execute(f'''
            SELECT t.value, t.category, COUNT(*) as cnt
            FROM tags t
            WHERE t.user_id = ?
            AND t.image_id IN (
                SELECT image_id FROM tags
                WHERE value IN ({placeholders})
                GROUP BY image_id
                HAVING COUNT(DISTINCT value) = ?
            )
            AND LOWER(t.value) LIKE ?
            AND t.value NOT IN ({placeholders})
            GROUP BY t.value, t.category
            ORDER BY cnt DESC
            LIMIT 20
        ''', [uid] + active_chips + [len(active_chips), f'{q}%'] + active_chips).fetchall()
    else:
        rows = c.execute('''
            SELECT value, category, COUNT(*) as cnt
            FROM tags
            WHERE user_id = ? AND LOWER(value) LIKE ?
            GROUP BY value, category
            ORDER BY cnt DESC
            LIMIT 20
        ''', (uid, f'{q}%')).fetchall()

    # Filmography matches — lets the same search bar find "Her" by title or
    # "Spike Jonze" by director/DP, reusing the exact film= filter that
    # clicking a name in the detail panel already applies (see /api/search).
    like = f'{q}%'
    film_rows = c.execute('''
        SELECT f.title AS value, 'title' AS field, COUNT(DISTINCT f.image_id) AS cnt
        FROM filmography f JOIN images i ON i.id = f.image_id
        WHERE i.user_id = ? AND f.title IS NOT NULL AND LOWER(f.title) LIKE ?
        GROUP BY f.title
        UNION ALL
        SELECT f.director, 'director', COUNT(DISTINCT f.image_id)
        FROM filmography f JOIN images i ON i.id = f.image_id
        WHERE i.user_id = ? AND f.director IS NOT NULL AND LOWER(f.director) LIKE ?
        GROUP BY f.director
        UNION ALL
        SELECT f.dp, 'dp', COUNT(DISTINCT f.image_id)
        FROM filmography f JOIN images i ON i.id = f.image_id
        WHERE i.user_id = ? AND f.dp IS NOT NULL AND LOWER(f.dp) LIKE ?
        GROUP BY f.dp
        ORDER BY cnt DESC
        LIMIT 8
    ''', (uid, like, uid, like, uid, like)).fetchall()

    # V15: aspect-ratio matches — "9:16", "2.35", "scope" etc. suggest format
    # buckets. Counting requires a scan of the user's images, so only do it
    # when the query actually looks like a ratio (ar_query_labels is pure
    # string logic and returns [] for normal tag searches).
    ar_results = []
    ar_labels = ar_query_labels(q)
    if ar_labels:
        bucket_counts = {}
        for row in c.execute('SELECT aspect_ratio FROM images WHERE user_id = ?', (uid,)).fetchall():
            label = normalize_ar_label(ar_float_from_str(row['aspect_ratio']))
            bucket_counts[label] = bucket_counts.get(label, 0) + 1
        ar_results = [{
            'type': 'ar',
            'value': label,
            'count': bucket_counts[label]
        } for label in ar_labels if bucket_counts.get(label)]

    # V39: on-set notes — live suggestion, not a list of discrete values like
    # tags/film. There's no fixed vocabulary to suggest FROM (notes are
    # freeform prose), so this checks whether the CURRENT typed text has any
    # match at all and, if so, offers exactly one entry: "run this phrase as
    # a notes search." A prefix MATCH (see _fts5_match_query) so it updates
    # as Ryan keeps typing, same as everything else in this dropdown.
    # Deliberately NOT scoped by active_chips co-occurrence like tag
    # suggestions are — a global per-user count is enough for v1.
    note_results = []
    match_query = _fts5_match_query(q, prefix=True)
    if match_query:
        # FTS5's special MATCH binding only recognizes the table by its real
        # name, not an alias — `n MATCH ?` throws "no such column: n" even
        # though `n` is a valid alias for notes_fts everywhere else in this
        # query (verified directly against sqlite3, not assumed).
        note_count = c.execute('''
            SELECT COUNT(DISTINCT n.rowid) AS cnt
            FROM notes_fts n JOIN images i ON i.id = n.rowid
            WHERE i.user_id = ? AND notes_fts MATCH ?
        ''', (uid, match_query)).fetchone()['cnt']
        if note_count:
            note_results = [{'type': 'note', 'value': q, 'count': note_count}]

    conn.close()

    tag_results = [{
        'type': 'tag',
        'value': row['value'],
        'category': row['category'],
        'catLabel': CAT_LABELS.get(row['category'], row['category']),
        'color': CAT_COLORS.get(row['category'], '#9c988d'),
        'count': row['cnt']
    } for row in rows]

    film_results = [{
        'type': 'film',
        'value': row['value'],
        'field': row['field'],
        'count': row['cnt']
    } for row in film_rows]

    # An exact match (typed "Tenet", there's a film called Tenet) should
    # always sit at the very top regardless of type or how many images carry
    # it — otherwise a popular tag that merely starts with the same letters
    # can bury the one result you actually typed for.
    combined = tag_results + film_results + ar_results + note_results
    combined.sort(key=lambda r: (r['value'].lower() != q, -r['count']))
    return jsonify(combined)

@app.route('/api/tag-categories')
def tag_categories():
    """Full fixed list of tag categories (not just ones currently in use),
    so the frontend can always show a complete category picker."""
    return jsonify([{
        'key': key,
        'label': CAT_LABELS[key],
        'color': CAT_COLORS.get(key, '#9c988d')
    } for key in CAT_LABELS])

def _fts5_match_query(phrase, prefix=False):
    """Turns a raw user phrase into a safe notes_fts MATCH query. A raw
    phrase can't go straight into MATCH — FTS5's query syntax gives meaning
    to characters like -, ", *, : — so every token is quoted to be treated
    literally. A bareword sequence of quoted tokens implicitly ANDs them,
    which is exactly the "forgiving of word order" behavior Ryan wants
    (an Omnisearch-style match, not a rigid substring/phrase match).

    prefix=True additionally leaves the LAST token unquoted with a trailing
    * (FTS5 prefix syntax), for live-typing autocomplete against a query
    that isn't finished yet. Embedded double-quote characters are stripped
    from every token first — otherwise one could break out of the quoting."""
    tokens = [t.replace('"', '') for t in phrase.split() if t.replace('"', '')]
    if not tokens:
        return None
    if prefix:
        *head, last = tokens
        parts = [f'"{t}"' for t in head]
        # The trailing token is deliberately left UNQUOTED so the * prefix
        # wildcard means anything to FTS5 — but that also means every OTHER
        # FTS5-meaningful character (-, *, :, (, ), ^) is live here too, not
        # neutralized by quoting the way it is for every other token above.
        # Strip to alphanumerics before appending the wildcard (verified: a
        # raw token like `weird"-*query` 500'd the endpoint before this).
        last_clean = ''.join(ch for ch in last if ch.isalnum())
        if last_clean:
            parts.append(f'{last_clean}*')
        if not parts:
            return None
    else:
        parts = [f'"{t}"' for t in tokens]
    return ' '.join(parts)

def build_search_filters(c, uid, args):
    """Turn the search query params into (conditions, params, is_unfiltered)
    for a WHERE clause over the `images` table.

    V32: pulled out of search() so /api/search, /api/search/ids and the tag
    removal preview all filter through ONE piece of code. A "select all 118
    results" button that quietly disagreed with the 118 results on screen
    would be worse than having no button at all, and a second hand-copied
    version of five filter types (chips / natural language / colour /
    aspect ratio / film) will drift apart the first time one of them changes.

    Every condition here refers to plain `images` columns (`user_id`, `id`),
    never a table alias, so callers can also drop the whole WHERE clause
    inside a `SELECT id FROM images ...` subquery.
    """
    chips_raw = args.get('chips', '').strip()
    nl_raw = args.get('nl', '').strip()
    notes_raw = args.get('notes', '').strip()  # V39: JSON array of on-set-notes phrases
    color_raw = args.get('color', '').strip()
    film_raw = args.get('film', '').strip()
    ar_raw = args.get('ar', '').strip()  # V15: aspect-ratio bucket, e.g. "2.39:1"
    # V24: color search knobs. Absent (old bookmarks, old clients) = the new
    # defaults, which is the agreed behaviour — a saved search returns fewer,
    # cleaner results than it used to rather than keeping the old noise.
    try:
        prominence = float(args.get('prom', DEFAULT_PROMINENCE))
    except ValueError:
        prominence = DEFAULT_PROMINENCE
    try:
        exactness = float(args.get('exact', DEFAULT_EXACTNESS))
    except ValueError:
        exactness = DEFAULT_EXACTNESS
    prominence = max(0.0, min(100.0, prominence))
    active_chips = [t.strip() for t in chips_raw.split(',') if t.strip()] if chips_raw else []

    # NL groups: JSON array of tag arrays. Image must match >=1 tag per group.
    nl_groups = []
    if nl_raw:
        try:
            parsed = json.loads(nl_raw)
            nl_groups = [[str(t) for t in g] for g in parsed if isinstance(g, list) and g]
        except Exception:
            nl_groups = []

    # V39: notes phrases. JSON array of plain strings — each is its own
    # AND'd notes_fts MATCH, same shape as an nl_groups entry above. Invalid
    # JSON or a non-list just means no notes filter, never a 500.
    notes_phrases = []
    if notes_raw:
        try:
            parsed = json.loads(notes_raw)
            notes_phrases = [str(p) for p in parsed if isinstance(p, str) and p.strip()]
        except Exception:
            notes_phrases = []

    conditions = ['user_id = ?']
    params = [uid]

    if active_chips:
        placeholders = ','.join('?' * len(active_chips))
        conditions.append(f'''id IN (
            SELECT image_id FROM tags WHERE value IN ({placeholders})
            GROUP BY image_id HAVING COUNT(DISTINCT value) = ?
        )''')
        params.extend(active_chips + [len(active_chips)])

    for group in nl_groups:
        gph = ','.join('?' * len(group))
        conditions.append(f'id IN (SELECT image_id FROM tags WHERE value IN ({gph}))')
        params.extend(group)

    for phrase in notes_phrases:
        match_query = _fts5_match_query(phrase)
        if not match_query:
            continue
        conditions.append('id IN (SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?)')
        params.append(match_query)

    if color_raw:
        # Small library — compute color matches in Python.
        # V24: an image matches when the palette entries close enough in hue
        # to the picked color TOGETHER cover at least `prominence` percent of
        # the frame. This is what kills the old false positives: a lipstick-
        # sized patch of red is a real red, but it's ~1% of the frame, so it
        # only survives at a low prominence setting.
        hue_tol = exactness_to_hue_tol(exactness)
        value_tol = exactness_to_value_tol(exactness)
        min_share = prominence / 100.0

        entries_by_image = {}
        legacy_rank_hits = set()  # pre-V24 rows: share unknown
        for row in c.execute(
            'SELECT image_id, hex, rank, share FROM colors WHERE user_id = ?', (uid,)
        ).fetchall():
            entries_by_image.setdefault(row['image_id'], []).append((row['hex'], row['share']))
            if row['share'] is None and row['rank'] is not None and row['rank'] <= 5:
                legacy_rank_hits.add(row['image_id'])

        matched_ids = set()
        for image_id, entries in entries_by_image.items():
            if color_match_share(color_raw, entries, hue_tol, value_tol) >= min_share:
                matched_ids.add(image_id)
                continue
            # Graceful degradation: palettes extracted before V24 have no
            # share, so prominence can't be judged. Rather than have color
            # search go silently empty until the backfill runs, fall back to
            # the old hue-only test on the top ranks for those images.
            if image_id in legacy_rank_hits and any(
                s is None and color_matches(color_raw, h, hue_tol, value_tol)
                for h, s in entries
            ):
                matched_ids.add(image_id)

        if matched_ids:
            cph = ','.join('?' * len(matched_ids))
            conditions.append(f'id IN ({cph})')
            params.extend(list(matched_ids))
        else:
            conditions.append('1 = 0')

    if ar_raw:
        # V15: aspect-ratio filter. Same trick as the color filter above —
        # small library, so snap every image to its nearest standard format
        # in Python (identical math to the ar_label shown on tiles) and pass
        # the matching ids into SQL.
        ar_ids = [
            row['id'] for row in c.execute(
                'SELECT id, aspect_ratio FROM images WHERE user_id = ?', (uid,)
            ).fetchall()
            if normalize_ar_label(ar_float_from_str(row['aspect_ratio'])) == ar_raw
        ]
        if ar_ids:
            aph = ','.join('?' * len(ar_ids))
            conditions.append(f'id IN ({aph})')
            params.extend(ar_ids)
        else:
            conditions.append('1 = 0')

    if film_raw:
        # Clicking a name in the detail panel sends the exact string, so try an
        # exact (case-insensitive) match first. Only fall back to substring
        # matching when nothing matches exactly — otherwise a short title like
        # "Her" would also return every "Christopher Nolan" film.
        exact_hit = c.execute('''
            SELECT 1 FROM filmography
            WHERE title = ? COLLATE NOCASE OR director = ? COLLATE NOCASE
               OR dp = ? COLLATE NOCASE LIMIT 1
        ''', (film_raw, film_raw, film_raw)).fetchone()
        if exact_hit:
            conditions.append('''id IN (
                SELECT image_id FROM filmography
                WHERE title = ? COLLATE NOCASE OR director = ? COLLATE NOCASE
                   OR dp = ? COLLATE NOCASE
            )''')
            params.extend([film_raw, film_raw, film_raw])
        else:
            like = f'%{film_raw}%'
            conditions.append('''id IN (
                SELECT image_id FROM filmography
                WHERE title LIKE ? OR director LIKE ? OR dp LIKE ?
            )''')
            params.extend([like, like, like])

    is_unfiltered = not (active_chips or nl_groups or notes_phrases or color_raw or film_raw or ar_raw)
    return conditions, params, is_unfiltered

@app.route('/api/search')
def search():
    page = int(request.args.get('page', 0))
    per = int(request.args.get('per', 50))

    uid = session['user_id']
    conn = get_db()
    c = conn.cursor()

    conditions, params, is_unfiltered = build_search_filters(c, uid, request.args)
    where = 'WHERE ' + ' AND '.join(conditions)

    # V14: shuffled home feed. When the default (unfiltered) grid sends a seed,
    # order by a seeded shuffle instead of newest-first. Any active filter
    # switches back to the normal newest-first ordering.
    #
    # V35: dropped the "seen in the last 7 days sinks to the bottom" bucket.
    # Once most of the library has been viewed recently (Ryan's case: 3496 of
    # 3499 images), that bucket swallows almost everything and only the tiny
    # unseen leftover ever occupies the top of the feed — so the "shuffle"
    # stops looking random, since day to day the same few unseen images keep
    # winning the top slots. A straight seeded shuffle stays fresh regardless
    # of view history.
    seed = request.args.get('seed', '').strip()
    if seed and is_unfiltered:
        order_by = 'shuffle_key(?, images.id)'
        order_params = [seed]
    else:
        order_by = 'date_added DESC'
        order_params = []

    rows = c.execute(f'''
        SELECT id, filename, thumbnail_blob, caption, aspect_ratio, md5_checksum,
               camera_rig, lens, lens_filter, stop, onset_notes, {favorite_col(uid)}
        FROM images {where}
        ORDER BY {order_by} LIMIT ? OFFSET ?
    ''', params + order_params + [per, page * per]).fetchall()
    total = c.execute(f'SELECT COUNT(*) FROM images {where}', params).fetchone()[0]

    images_out = images_common.hydrate_image_rows(c, rows)
    conn.close()

    return jsonify({'images': images_out, 'total': total, 'page': page, 'per': per, 'has_more': (page + 1) * per < total})

@app.route('/api/search/ids')
def search_ids():
    """Every image id matching the current filter — not just the page the
    browser happens to have scrolled to (V32).

    Select Mode's old "Select all loaded" only ever selected the thumbnails
    already in the grid, so on a 118-result search that had loaded 60 it
    silently grabbed 60 and said nothing. Sending ids instead of forcing the
    grid to fetch every remaining page is what makes "select all" cheap: a
    few kilobytes of numbers versus tens of megabytes of base64 thumbnails.

    Takes exactly the same query params as /api/search and shares
    build_search_filters() with it, so the ids returned here are precisely
    the images on screen — they cannot drift apart. No `seed` handling:
    ordering is irrelevant to a selection, and the shuffle only ever applies
    to the unfiltered grid anyway."""
    uid = session['user_id']
    conn = get_db()
    c = conn.cursor()
    conditions, params, _ = build_search_filters(c, uid, request.args)
    where = 'WHERE ' + ' AND '.join(conditions)
    rows = c.execute(f'SELECT id FROM images {where} ORDER BY date_added DESC', params).fetchall()
    conn.close()
    ids = [r['id'] for r in rows]
    return jsonify({'ids': ids, 'total': len(ids)})

@app.route('/api/bookmarks', methods=['GET', 'POST'])
def bookmarks():
    user_id = session['user_id']

    if request.method == 'GET':
        conn = get_db()
        c = conn.cursor()
        rows = c.execute('''
            SELECT id, name, chips_json, created_at FROM saved_searches
            WHERE user_id = ? ORDER BY created_at DESC
        ''', (user_id,)).fetchall()
        conn.close()
        out = []
        for r in rows:
            try:
                state = json.loads(r['chips_json'] or '{}')
            except Exception:
                state = {}
            out.append({'id': r['id'], 'name': r['name'], 'state': state, 'created_at': r['created_at']})
        return jsonify(out)

    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    state = data.get('state') or {}
    if not name:
        return jsonify({'error': 'Name required'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO saved_searches (user_id, name, chips_json) VALUES (?, ?, ?)',
              (user_id, name, json.dumps(state)))
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return jsonify({'success': True, 'id': new_id})

@app.route('/api/bookmarks/<int:bookmark_id>', methods=['DELETE'])
def delete_bookmark(bookmark_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM saved_searches WHERE id = ? AND user_id = ?', (bookmark_id, session['user_id']))
    found = c.rowcount > 0
    conn.commit()
    conn.close()
    if not found:
        return jsonify({'error': 'Bookmark not found'}), 404
    return jsonify({'success': True})

@app.route('/api/images', methods=['GET'])
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

@app.route('/api/images/<int:image_id>/full')
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

@app.route('/api/images/<int:image_id>/thumb')
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

def _cosine_similarity(vec_a, vec_b):
    """Plain-Python cosine similarity between two equal-length float lists.
    Re-normalizes defensively (the seed vectors are already L2-normalized,
    but we don't want to trust that blindly), and guards against a
    zero-magnitude vector blowing up with a divide-by-zero."""
    dot = 0.0
    mag_a = 0.0
    mag_b = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        mag_a += a * a
        mag_b += b * b
    mag_a = mag_a ** 0.5
    mag_b = mag_b ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)

@app.route('/api/images/<int:image_id>/similar')
def get_similar_images(image_id):
    """Visual + tag similarity for the 'more like this' feature. Combines a
    CLIP embedding cosine similarity (how visually alike two images are) with
    a tag overlap score (how much cinematography vocabulary they share):
    combined = 0.7 * cosine + 0.3 * tag_overlap.
    Requires embeddings_seed.json.gz to have been loaded (see
    load_embeddings_seed) — if the source image has no vector yet, this
    returns 404 rather than guessing."""
    limit = request.args.get('limit', 40, type=int)
    if not limit or limit <= 0:
        limit = 40
    limit = min(limit, 100)

    uid = session['user_id']
    conn = get_db()
    c = conn.cursor()

    c.execute('SELECT filename FROM images WHERE id = ? AND user_id = ?', (image_id, uid))
    source_img = c.fetchone()
    if not source_img:
        conn.close()
        return jsonify({'error': 'Image not found'}), 404

    c.execute('SELECT clip_vector FROM embeddings WHERE image_id = ?', (image_id,))
    source_row = c.fetchone()
    if not source_row or not source_row['clip_vector']:
        conn.close()
        return jsonify({'error': 'no_embedding'}), 404

    source_vec = array('f', source_row['clip_vector']).tolist()

    # All embeddings, joined to the columns build_image_dict() needs — one
    # query, no per-candidate lookups. Scoped to this user's own images.
    candidates = c.execute(f'''
        SELECT e.image_id, e.clip_vector,
               i.id, i.filename, i.thumbnail_blob, i.caption, i.aspect_ratio, i.md5_checksum,
               i.camera_rig, i.lens, i.lens_filter, i.stop, i.onset_notes,
               {favorite_col(uid, alias='i')}
        FROM embeddings e
        JOIN images i ON i.id = e.image_id
        WHERE e.image_id != ? AND e.clip_vector IS NOT NULL AND i.user_id = ?
    ''', (image_id, uid)).fetchall()

    # Tags for the source image plus every candidate, in one query — grouped
    # by image_id in Python instead of one query per candidate. Keep both the
    # full {'category','value'} dicts (for the response, same shape as
    # /api/search) and a plain set of values (for the overlap score).
    all_ids = [image_id] + [row['image_id'] for row in candidates]
    tags_by_image = {}
    tag_values_by_image = {}
    if all_ids:
        ph = ','.join('?' * len(all_ids))
        for tr in c.execute(f'SELECT image_id, category, value FROM tags WHERE image_id IN ({ph})', all_ids).fetchall():
            tags_by_image.setdefault(tr['image_id'], []).append({'category': tr['category'], 'value': tr['value']})
            tag_values_by_image.setdefault(tr['image_id'], set()).add(tr['value'])

    source_tag_values = tag_values_by_image.get(image_id, set())

    scored = []
    for row in candidates:
        cand_vec = array('f', row['clip_vector']).tolist()
        cosine = _cosine_similarity(source_vec, cand_vec)

        cand_tag_values = tag_values_by_image.get(row['image_id'], set())
        if source_tag_values and cand_tag_values:
            overlap = len(source_tag_values & cand_tag_values) / min(len(source_tag_values), len(cand_tag_values))
        else:
            overlap = 0.0

        combined = 0.7 * cosine + 0.3 * overlap
        scored.append((combined, row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[:limit]

    # Build response dicts only for the images we're actually returning —
    # no point base64-encoding thumbnails we're about to throw away.
    top_ids = [row['image_id'] for _, row in top]
    colors_map = {}
    if top_ids:
        ph = ','.join('?' * len(top_ids))
        for cr in c.execute(f'SELECT image_id, hex FROM colors WHERE image_id IN ({ph}) ORDER BY rank ASC', top_ids).fetchall():
            colors_map.setdefault(cr['image_id'], []).append(cr['hex'])
        film_map = {}
        for fr in c.execute(f'SELECT image_id, title, director, dp, year FROM filmography WHERE image_id IN ({ph})', top_ids).fetchall():
            film_map[fr['image_id']] = {
                'title': fr['title'], 'director': fr['director'],
                'dp': fr['dp'], 'year': fr['year']
            }
    else:
        film_map = {}

    conn.close()

    images_out = []
    for combined, row in top:
        img_dict = images_common.build_image_dict(
            row,
            tags_by_image.get(row['image_id'], []),
            colors_map.get(row['image_id'], []),
            film_map.get(row['image_id'])
        )
        img_dict['similarity'] = round(combined, 3)
        images_out.append(img_dict)

    return jsonify({
        'source': {'id': image_id, 'filename': source_img['filename']},
        'images': images_out
    })

@app.route('/api/regenerate-thumbnails', methods=['POST'])
@admin_required
def regenerate_thumbnails():
    def _regenerate_job():
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute('SELECT id, user_id, drive_file_id FROM images ORDER BY id DESC')
            images = c.fetchall()
            conn.close()

            sync.sync_state['total'] = len(images)
            sync.sync_state['processed'] = 0

            service = drive.get_drive_service()
            for img in images:
                try:
                    sync.sync_state['current_file'] = f"regenerating #{img['id']}"
                    file_id = img['drive_file_id']
                    req = service.files().get_media(fileId=file_id)
                    fh = io.BytesIO()
                    downloader = MediaIoBaseDownload(fh, req)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()

                    image_data = fh.getvalue()
                    thumbnail = generate_thumbnail(image_data)

                    if thumbnail:
                        conn = get_db()
                        c = conn.cursor()
                        c.execute('UPDATE images SET thumbnail_blob = ? WHERE id = ?', (thumbnail, img['id']))
                        conn.commit()
                        conn.close()

                        hexes = extract_palette(thumbnail)
                        if hexes:
                            images_common.save_palette(img['id'], img['user_id'], hexes)
                except Exception as e:
                    print(f"[regenerate] Failed {img['id']}: {e}")
                sync.sync_state['processed'] += 1
            print("[regenerate] All thumbnails updated")
        finally:
            sync.sync_state['in_progress'] = False

    if sync.sync_state['in_progress']:
        return jsonify({'error': 'Sync already in progress'}), 400

    sync.sync_state['in_progress'] = True
    thread = threading.Thread(target=_regenerate_job, daemon=True)
    thread.start()

    return jsonify({'success': True, 'message': 'Thumbnail regeneration started'})

@app.route('/api/extract-colors', methods=['POST'])
@admin_required
def extract_colors():
    """Backfill palettes from stored thumbnails — no Drive downloads needed.
    Pass ?force=true to re-extract every image (e.g. after a palette-size change)."""
    force = request.args.get('force', '').lower() == 'true'
    conn = get_db()
    c = conn.cursor()
    if force:
        c.execute('SELECT id, user_id, thumbnail_blob FROM images')
    else:
        c.execute('''
            SELECT id, user_id, thumbnail_blob FROM images
            WHERE id NOT IN (SELECT DISTINCT image_id FROM colors)
        ''')
    images = c.fetchall()
    conn.close()

    count = 0
    for img in images:
        hexes = extract_palette(img['thumbnail_blob'])
        if hexes:
            images_common.save_palette(img['id'], img['user_id'], hexes)
            count += 1

    return jsonify({'success': True, 'extracted': count, 'skipped': len(images) - count})

# ============================================================================
# DAY 8 (V7): GOOGLE SIGN-IN + UPLOAD
# ============================================================================

@app.route('/api/auth/status')
def auth_status():
    """Day 8: admin-only (upload sign-in). Day 14 Stage 2: generalized to
    whoever's logged in, since every user now connects their own Google
    account — session['user_id'] instead of a hardcoded 1."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT google_oauth_token FROM users WHERE id = ?', (session['user_id'],))
    row = c.fetchone()
    conn.close()
    return jsonify({'signed_in': bool(row and row['google_oauth_token'])})

@app.route('/api/auth/google/login')
def google_login():
    redirect_uri = request.url_root.rstrip('/') + '/api/auth/google/callback'
    flow = drive.get_oauth_flow(redirect_uri)
    auth_url, state = flow.authorization_url(
        access_type='offline', prompt='consent', include_granted_scopes='true')
    session['oauth_state'] = state
    return redirect(auth_url)

@app.route('/api/drive/picker-token')
def drive_picker_token():
    """A short-lived OAuth access token for the CURRENT user, handed to the
    Google Picker widget in the browser so they can pick a folder from their
    own Drive. Same drive.file scope as everything else here — the Picker is
    what lets that narrow scope reach an arbitrary folder the user chooses,
    without ever requesting broader Drive access."""
    creds = drive.get_user_credentials(session['user_id'])
    if not creds:
        return jsonify({'error': 'not_signed_in'}), 401
    return jsonify({'access_token': creds.token})

@app.route('/api/auth/google/callback')
def google_callback():
    redirect_uri = request.url_root.rstrip('/') + '/api/auth/google/callback'
    flow = drive.get_oauth_flow(redirect_uri)
    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as e:
        return redirect(f'/?auth_error={e}')

    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE users SET google_oauth_token = ? WHERE id = ?', (flow.credentials.to_json(), session['user_id']))
    conn.commit()
    conn.close()
    return redirect('/?signed_in=1')

@app.route('/api/auth/google/disconnect', methods=['POST'])
def google_disconnect():
    """Clear the user's Google OAuth token. Re-authenticates on next upload attempt."""
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE users SET google_oauth_token = NULL WHERE id = ?', (session['user_id'],))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# Day 35 (Phase 3): _load_existing_phashes() and _ingest_image() moved to
# sync.py. They serve /api/upload and /api/clip (NOT the folder sync), and
# both routes below call them qualified as sync.<name>().


@app.route('/api/upload', methods=['POST'])
@admin_required
def upload_images():
    # Uploads always go into the shared admin library (Stage 1 decision,
    # unchanged by Stage 2) — always user 1's own Google connection/folder,
    # regardless of who's calling (only admin can reach this route anyway).
    try:
        service = drive.get_user_drive_service(1)
    except Exception as e:
        print(f"[auth] Upload's get_user_drive_service(1) failed: {e}")
        return jsonify({
            'error': 'google_auth_failed',
            'message': 'Your Google authentication token is invalid or expired. Please disconnect and reconnect in Settings.'
        }), 401

    if not service:
        return jsonify({
            'error': 'not_signed_in',
            'message': 'Sign in with Google first. Click the Connect button in Settings.'
        }), 401

    force = request.args.get('force', '').lower() == 'true'
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No files provided'}), 400

    folder_id = drive.get_root_folder_id(1)
    existing = sync._load_existing_phashes()

    results = [
        sync._ingest_image(service, folder_id, f.read(), f.filename, f.mimetype, existing, force=force)
        for f in files
    ]

    if any(r['status'] == 'uploaded' for r in results):
        tagging.trigger_tagging()

    return jsonify({'results': results})

# ============================================================================
# V25: WEB CLIPPING — browser extension endpoint
# ============================================================================

CLIP_MAX_BYTES = 25 * 1024 * 1024        # a clipped still well past any sane size
CLIP_ALLOWED_MIME = {'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif'}


def _clip_filename(source_url, mimetype, fallback='clip'):
    """A sensible Drive filename for a clipped image.

    Web image URLs are frequently junk for this — query strings, CDN hashes,
    no extension at all — so anything unusable falls back to a timestamp, and
    the extension is always corrected to match the actual image type.
    """
    ext = {
        'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp',
        'image/gif': '.gif', 'image/avif': '.avif',
    }.get(mimetype, '.jpg')

    stem = ''
    try:
        path = urllib.parse.urlparse(source_url or '').path
        stem = os.path.splitext(os.path.basename(urllib.parse.unquote(path)))[0]
    except Exception:
        stem = ''

    stem = re.sub(r'[^A-Za-z0-9._-]+', '-', stem).strip('-.')[:60]
    if not stem:
        stem = f"{fallback}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    return f'{stem}{ext}'


@app.route('/api/clip', methods=['POST'])
def clip_image():
    """Save one image grabbed from a web page by the browser extension.

    The extension sends the actual bytes rather than a URL: it captures in the
    browser, where the page's own cookies and hotlink protection already
    apply, so images this server could never fetch on its own still work — and
    video frames, which exist only as canvas pixels, work at all.

    Writes to the logged-in user's Drive folder. Friends with their own
    personal libraries can clip to their own folders. Ryan clips to his folder.
    """
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'login_required', 'message': 'Sign in to Frame Atlas first.'}), 401

    data = request.get_json(silent=True) or {}
    raw = data.get('image') or ''
    source_url = (data.get('source_url') or '').strip()[:2000] or None
    force = bool(data.get('force'))

    if not raw:
        return jsonify({'error': 'no_image', 'message': 'No image data was sent.'}), 400

    # data:image/jpeg;base64,XXXX
    m = re.match(r'^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$', raw, re.DOTALL)
    if not m:
        return jsonify({'error': 'bad_image', 'message': 'Expected a base64 image data URL.'}), 400

    mimetype = m.group(1).lower()
    if mimetype not in CLIP_ALLOWED_MIME:
        return jsonify({
            'error': 'unsupported_type',
            'message': f"{mimetype} isn't a supported image type."
        }), 400

    try:
        image_data = base64.b64decode(m.group(2), validate=True)
    except Exception:
        return jsonify({'error': 'bad_image', 'message': 'Image data was not valid base64.'}), 400

    if not image_data:
        return jsonify({'error': 'bad_image', 'message': 'Image data was empty.'}), 400
    if len(image_data) > CLIP_MAX_BYTES:
        return jsonify({
            'error': 'too_large',
            'message': f'That image is over {CLIP_MAX_BYTES // (1024 * 1024)}MB.'
        }), 413

    # Confirm it really is a decodable image before it reaches Drive — the
    # extension can hand us whatever a page served under an image URL.
    try:
        Image.open(io.BytesIO(image_data)).verify()
    except Exception:
        return jsonify({'error': 'bad_image', 'message': "That file isn't a readable image."}), 400

    try:
        service = drive.get_user_drive_service(user_id)
    except Exception:
        return jsonify({
            'error': 'google_auth_failed',
            'message': 'Your Google connection expired. Reconnect it in Frame Atlas → Settings.'
        }), 401
    if not service:
        return jsonify({
            'error': 'not_signed_in',
            'message': 'Connect Google Drive in Frame Atlas → Settings first.'
        }), 401

    existing = sync._load_existing_phashes()
    result = sync._ingest_image(
        service, drive.get_root_folder_id(user_id), image_data,
        _clip_filename(source_url, mimetype), mimetype,
        existing, force=force, source_url=source_url,
    )

    if result['status'] == 'uploaded':
        tagging.trigger_tagging()
        return jsonify({
            'status': 'clipped',
            'image_id': result['image_id'],
            'filename': result['filename'],
        })
    if result['status'] == 'duplicate':
        return jsonify({
            'status': 'duplicate',
            'existing': result['existing'],
            'message': 'Already in your library.',
        })
    return jsonify({'status': 'error', 'message': result.get('message', 'Clip failed.')}), 500


# ============================================================================
# DAY 8 (V7): IMAGE ACTIONS — favorite, tags, download, delete
# ============================================================================

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

@app.route('/api/images/<int:image_id>/favorite', methods=['POST'])
def toggle_favorite(image_id):
    result = _toggle_membership('user_favorites', session['user_id'], image_id)
    if result is None:
        return jsonify({'error': 'Image not found'}), 404
    return jsonify({'success': True, 'is_favorite': result})

@app.route('/api/images/<int:image_id>/tags', methods=['POST', 'DELETE'])
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

def _scope_ids_to_user(c, image_ids):
    """Cut a client-supplied image_id list down to the photos the current user
    is allowed to edit metadata on: an admin keeps the whole list (byte-for-
    byte — no query runs), a friend keeps only the ids their own user_id owns.

    Every bulk tag / filmography endpoint runs its id list through this (V75),
    so 'apply this to my selection' from a friend can never reach into another
    person's library even if the request body is hand-tampered. Chunked for the
    same reason count_tags_for_images() is — a friend's whole-library selection
    can exceed SQLite's placeholder limit just like the admin's can."""
    if session.get('role') == 'admin':
        return list(image_ids)
    uid = session.get('user_id')
    owned = set()
    for batch in chunked(image_ids):
        placeholders = ','.join('?' * len(batch))
        for row in c.execute(
            f'SELECT id FROM images WHERE id IN ({placeholders}) AND user_id = ?',
            list(batch) + [uid]
        ).fetchall():
            owned.add(row['id'])
    return [i for i in image_ids if i in owned]

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

@app.route('/api/tags/bulk-apply', methods=['POST'])
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

@app.route('/api/tags/bulk-remove', methods=['POST'])
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

@app.route('/api/tags/removal-preview')
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
    conditions, params, _ = build_search_filters(c, uid, request.args)
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

@app.route('/api/tags/selection-summary', methods=['POST'])
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
                        'common_filmography': {f: None for f in ('title', 'director', 'dp', 'year')}})
    tag_counts = count_tags_for_images(c, image_ids)

    # Filmography consensus: a field only counts as "common" when EVERY
    # selected image already agrees on the same non-empty value — missing
    # data on even one image breaks the consensus (so the bulk form doesn't
    # falsely imply a field's been verified across the whole selection).
    film_rows = []
    for batch in chunked(image_ids):
        placeholders = ','.join('?' * len(batch))
        film_rows += c.execute(f'''
            SELECT image_id, title, director, dp, year FROM filmography
            WHERE image_id IN ({placeholders})
        ''', batch).fetchall()
    conn.close()

    film_by_image = {r['image_id']: r for r in film_rows}
    common_filmography = {}
    for field in ('title', 'director', 'dp', 'year'):
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

@app.route('/api/tags/suggestions', methods=['POST'])
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

    seed_placeholders = ','.join('?' * len(seed_values))
    candidate_rows = c.execute(f'''
        SELECT t2.category, t2.value, COUNT(DISTINCT t2.image_id) as cnt
        FROM tags t2
        WHERE t2.image_id IN (
            SELECT DISTINCT image_id FROM tags WHERE value IN ({seed_placeholders})
        )
        AND t2.value NOT IN ({seed_placeholders})
        GROUP BY t2.category, t2.value
        ORDER BY cnt DESC
        LIMIT 30
    ''', seed_values + seed_values).fetchall()
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

@app.route('/api/images/<int:image_id>/filmography', methods=['POST'])
def update_filmography(image_id):
    """Set or clear the film info Gemini guessed for this image. Sending all
    empty fields clears it entirely.

    V75: owner-or-admin, not admin-only — a friend knows what they shot and can
    fix the film credit on their own photo, same rule as tag editing and the
    On-Set Notes editor (V39)."""
    data = request.get_json(force=True) or {}
    title = (data.get('title') or '').strip()
    director = (data.get('director') or '').strip()
    dp = (data.get('dp') or '').strip()
    year = str(data.get('year') or '').strip()

    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT user_id FROM images WHERE id = ?', (image_id,)).fetchone()
    if not row or (row['user_id'] != session['user_id'] and session.get('role') != 'admin'):
        conn.close()
        return jsonify({'error': 'Image not found'}), 404

    c.execute('DELETE FROM filmography WHERE image_id = ?', (image_id,))
    filmography = None
    if any([title, director, dp, year]):
        c.execute(
            'INSERT INTO filmography (image_id, title, director, dp, year) VALUES (?,?,?,?,?)',
            (image_id, title or None, director or None, dp or None, year or None)
        )
        filmography = {'title': title or None, 'director': director or None,
                       'dp': dp or None, 'year': year or None}
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'filmography': filmography})

@app.route('/api/images/<int:image_id>/notes', methods=['POST'])
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

@app.route('/api/filmography/bulk-set', methods=['POST'])
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
    }
    touched = {k: v for k, v in touched.items() if v}
    if not touched:
        return jsonify({'error': 'At least one of title/director/dp/year is required'}), 400

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
            'SELECT title, director, dp, year FROM filmography WHERE image_id = ?', (image_id,)
        ).fetchone()
        merged = {
            field: touched.get(field, existing[field] if existing else None)
            for field in ('title', 'director', 'dp', 'year')
        }
        c.execute('DELETE FROM filmography WHERE image_id = ?', (image_id,))
        if any(merged.values()):
            c.execute(
                'INSERT INTO filmography (image_id, title, director, dp, year) VALUES (?,?,?,?,?)',
                (image_id, merged['title'], merged['director'], merged['dp'], merged['year'])
            )
    conn.commit()
    conn.close()

    return jsonify({
        'updated': len(valid_ids),
        'invalid_ids': invalid_ids,
        'fields_applied': touched,
    })

@app.route('/api/filmography/bulk-clear', methods=['POST'])
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

@app.route('/api/images/<int:image_id>/download')
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

@app.route('/api/images/<int:image_id>', methods=['DELETE'])
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

# V36: bulk delete moves each admin photo on its own Drive API round trip
# (get current parents, then move into _Removed) — sequentially, 16 photos
# was taking 10-20+ seconds, long enough that the browser sometimes gave up
# on the request before it finished. This many workers run those moves at
# once; the underlying Drive HTTP client isn't safe to share across threads,
# so BULK_DELETE_WORKERS also caps how many separate service objects (one
# per thread, via threading.local in bulk_delete_images) get created.
BULK_DELETE_WORKERS = 5

# Google's machine-readable reasons for "you're calling too fast" (as
# opposed to a real permissions/quota problem, which shouldn't be retried).
DRIVE_RATE_LIMIT_REASONS = {'userRateLimitExceeded', 'rateLimitExceeded'}

@app.route('/api/images/bulk-delete', methods=['POST'])
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

# ============================================================================
# V18: CROP — replace an image with a cropped version of itself
# ============================================================================

# Day 29 (Phase 3): download_drive_file() and drive_error_reason() moved to
# drive.py — call them qualified (drive.download_drive_file(...),
# drive.drive_error_reason(e)).

# Day 34 (Phase 3): CROP_SAVE_FORMATS moved to crop.py with the worker that
# uses it.

@app.route('/api/images/<int:image_id>/crop', methods=['POST'])
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

@app.route('/api/crop-progress', methods=['GET'])
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

@app.route('/api/crop-progress/reset', methods=['POST'])
def reset_crop_progress():
    """Clear the progress state after user closes notifications (V27)."""
    with crop._crop_lock:
        crop._crop_progress['total'] = 0
        crop._crop_progress['completed'] = 0
        crop._crop_progress['failed'] = []
        crop._crop_progress['active_jobs'] = {}
    return jsonify({'reset': True})

# ============================================================================
# DAY 8 (V7): DUPLICATE DETECTION
# ============================================================================

# Day 35 (Phase 3): _users_with_synced_folders() and reconcile_drive_changes()
# moved to sync.py. reconcile_drive_changes() never deletes anything — that
# is deliberately sync_folder_worker()'s job alone. The duplicates/scan route
# below and both boot blocks call sync.reconcile_drive_changes().

@app.route('/api/duplicates/scan', methods=['POST'])
@admin_required
def duplicates_scan():
    """Self-heals the library's derived data, then returns duplicate groups.

    Everything the duplicate check reads is rebuilt here first, because a
    missing piece doesn't just weaken the check — it silently changes which
    gates apply:

      1. Fingerprints — missing ones, and (V30) ones still at the old 8x8
         width, rebuilt from the stored thumbnail. Instant.
      2. Colour palettes — missing ones, likewise from the thumbnail.
      3. Drive reconciliation (see reconcile_drive_changes) — also runs at
         boot now, but re-running it here means a click on this scan always
         reflects the current state of Drive, not just whatever boot found.
    """
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, thumbnail_blob FROM images WHERE phash IS NULL OR LENGTH(phash) != ?',
              (PHASH_HEX_LEN,))
    for r in c.fetchall():
        ph = compute_phash(r['thumbnail_blob'])
        if ph:
            c.execute('UPDATE images SET phash = ? WHERE id = ?', (ph, r['id']))
    conn.commit()
    c.execute('''
        SELECT id, user_id, thumbnail_blob FROM images
        WHERE id NOT IN (SELECT DISTINCT image_id FROM colors)
    ''')
    missing_palette = c.fetchall()
    conn.close()

    for r in missing_palette:
        hexes = extract_palette(r['thumbnail_blob'])
        if hexes:
            images_common.save_palette(r['id'], r['user_id'], hexes)

    sync.reconcile_drive_changes()

    return find_duplicates()

@app.route('/api/duplicates', methods=['GET'])
@admin_required
def find_duplicates():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT id, filename, thumbnail_blob, md5_checksum, phash, date_added, aspect_ratio
        FROM images ORDER BY date_added ASC
    ''')
    rows = c.fetchall()
    c.execute('SELECT image_id, hex, share FROM colors')
    palette_map = {}
    for r in c.fetchall():
        palette_map.setdefault(r['image_id'], []).append((r['hex'], r['share']))
    conn.close()

    # Union-find: any two images linked by an exact or near match end up in
    # the same group, even chains (A~B, B~C => one group of three).
    n = len(rows)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # Signatures are decoded lazily and memoised: only images the fingerprint
    # actually nominates get their thumbnail decoded, so this stays O(library)
    # decodes at worst instead of one per comparison.
    _sig_cache = {}

    def signature_for(idx):
        if idx not in _sig_cache:
            _sig_cache[idx] = compute_signature(rows[idx]['thumbnail_blob'])
        return _sig_cache[idx]

    exact_pairs = set()
    for i in range(n):
        for j in range(i + 1, n):
            a, b = rows[i], rows[j]
            if a['md5_checksum'] and a['md5_checksum'] == b['md5_checksum']:
                parent[find(i)] = find(j)
                exact_pairs.add((i, j))
            # Three gates, cheapest first. The fingerprint only nominates a
            # candidate; the signature (does it actually look alike?) and the
            # palette (is it actually the same colour?) both have to agree.
            elif (a['phash'] and b['phash']
                  and phash_distance(a['phash'], b['phash']) <= PHASH_NEAR_DUP_THRESHOLD
                  and signatures_match(signature_for(i), signature_for(j))
                  and palettes_overlap(palette_map.get(a['id'], []), palette_map.get(b['id'], []))):
                parent[find(i)] = find(j)

    buckets = {}
    for i in range(n):
        buckets.setdefault(find(i), []).append(i)

    groups = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        all_exact = all(
            (min(i, j), max(i, j)) in exact_pairs
            for i in members for j in members if i < j
        )
        groups.append({
            'kind': 'exact' if all_exact else 'near',
            'images': [{
                'id': rows[i]['id'],
                'filename': rows[i]['filename'],
                'thumbnail': f"data:image/jpeg;base64,{base64.b64encode(rows[i]['thumbnail_blob']).decode('utf-8')}",
                'date_added': rows[i]['date_added'],
                'aspect_ratio': rows[i]['aspect_ratio'],
            } for i in members]
        })

    return jsonify({'groups': groups, 'count': len(groups)})

# ============================================================================
# DECKS + SCENES
# ============================================================================

# _fetch_image_dict() moved to images_common.py in Day 31 (Phase 3) — the decks
# endpoints call images_common._fetch_image_dict(...).

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

@app.route('/api/decks', methods=['GET'])
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

@app.route('/api/decks', methods=['POST'])
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

@app.route('/api/decks/<int:deck_id>', methods=['PATCH'])
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

@app.route('/api/decks/<int:deck_id>', methods=['DELETE'])
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

@app.route('/api/decks/<int:deck_id>', methods=['GET'])
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

@app.route('/api/decks/<int:deck_id>/members', methods=['GET'])
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

@app.route('/api/decks/<int:deck_id>/invite', methods=['POST'])
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

@app.route('/api/decks/<int:deck_id>/members/<int:user_id>', methods=['DELETE'])
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

@app.route('/api/decks/<int:deck_id>/invite-link', methods=['POST', 'DELETE'])
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

@app.route('/api/decks/invite/<token>/accept', methods=['POST'])
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

@app.route('/api/decks/<int:deck_id>/activity', methods=['GET'])
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

@app.route('/api/scenes', methods=['POST'])
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

@app.route('/api/scenes/<int:scene_id>', methods=['PATCH'])
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

@app.route('/api/scenes/<int:scene_id>', methods=['DELETE'])
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

@app.route('/api/decks/<int:deck_id>/scenes/reorder', methods=['POST'])
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

@app.route('/api/decks/<int:deck_id>/images', methods=['POST'])
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

@app.route('/api/deck-images/<int:deck_image_id>/move', methods=['POST'])
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

@app.route('/api/deck-images/<int:deck_image_id>', methods=['DELETE'])
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

@app.route('/api/deck-images/<int:deck_image_id>/note', methods=['POST'])
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

@app.route('/api/decks/<int:deck_id>/reorder', methods=['POST'])
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

@app.route('/api/decks/<int:deck_id>/share', methods=['POST', 'DELETE'])
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

@app.route('/api/decks/join/<token>', methods=['POST'])
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

@app.route('/api/share/<token>')
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

@app.route('/api/decks/<int:deck_id>/feedback', methods=['GET'])
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

@app.route('/api/decks/<int:deck_id>/feedback-enabled', methods=['POST'])
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

@app.route('/api/decks/<int:deck_id>/comments/<int:comment_id>', methods=['DELETE'])
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

@app.route('/api/share/<token>/feedback', methods=['GET'])
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

@app.route('/api/share/<token>/picks', methods=['POST'])
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

@app.route('/api/share/<token>/picks', methods=['DELETE'])
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

@app.route('/api/share/<token>/comments', methods=['POST'])
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

@app.route('/api/decks/<int:deck_id>/export.pdf')
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

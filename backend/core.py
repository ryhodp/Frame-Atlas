"""core.py — Frame Atlas shared foundation (Day 28 / Phase 3).

The genuinely shared, low-level pieces every other backend module leans on:
the database connection, tag-value normalisation, the tag-category display
maps, and the Gemini model/pricing constants. Split out of app.py verbatim —
every function here is character-for-character what it was, only its home
changed.

Rule for this phase: this module imports nothing PROJECT-INTERNAL — never
app.py, never a blueprint (that would be circular, since they import FROM
here). Standard library plus Flask itself are fine, and became necessary as
of Day 36: the login gate (require_login/admin_required/PUBLIC_API_ROUTES)
moved in here so every future route blueprint can use @admin_required and
read session state without importing app.py.
"""
import os
import sqlite3
import zlib
from functools import wraps

from flask import jsonify, request, session

# FA_DB_PATH lets test scripts point the app at a throwaway database without
# editing any source file (V45 part 2) — unset in production, so Railway keeps
# using the real path with zero config change. Read live via db_path() rather
# than snapshotted at import, so a test harness that boots several app
# instances in one process (each with its own FA_DB_PATH) gets the right file
# every time even though this module is imported only once.
DEFAULT_DB_PATH = '/app/data/library.db'


def db_path():
    return os.environ.get('FA_DB_PATH', DEFAULT_DB_PATH)


# Gemini model — overridable via Railway env var if Google retires this one
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')

# USD per 1,000,000 tokens. Every user is expected to run the same
# GEMINI_MODEL (see get_user_gemini_key) so one entry covers everyone — if
# that ever changes, add a row here per model.
GEMINI_PRICING = {
    'gemini-2.5-flash': {'input': 0.30, 'output': 2.50},
}
DEFAULT_GEMINI_PRICING = {'input': 0.30, 'output': 2.50}

def get_model_pricing(model_name):
    return GEMINI_PRICING.get(model_name, DEFAULT_GEMINI_PRICING)


# Fixed tag category taxonomy — display color/label for each of the 15
# categories Gemini tags images with. Used by /api/autocomplete,
# /api/tag-categories, and the bulk tag endpoints below.
CAT_COLORS = {
    'mood': '#8b7cf6', 'lighting_quality': '#f59e0b',
    'lighting_color_temperature': '#f97316', 'color_palette': '#ec4899',
    'shot_type': '#06b6d4', 'framing_composition': '#10b981',
    'location_type': '#84cc16', 'time_of_day_weather': '#c9a253',
    'source_type': '#6366f1', 'subject_count': '#94a3b8',
    'subject_camera_relationship': '#a78bfa', 'genre_aesthetic': '#f43f5e',
    'era_decade': '#fb923c', 'camera_format': '#22d3ee',
    'performance_emotion': '#e879f9',
    'subjects': '#f472b6',
    'my_work': '#d9a441',
}
CAT_LABELS = {
    'mood': 'Mood', 'lighting_quality': 'Lighting',
    'lighting_color_temperature': 'Color Temp', 'color_palette': 'Palette',
    'shot_type': 'Shot', 'framing_composition': 'Framing',
    'location_type': 'Location', 'time_of_day_weather': 'Time / Weather',
    'source_type': 'Source', 'subject_count': 'Subjects',
    'subject_camera_relationship': 'Camera Rel.', 'genre_aesthetic': 'Genre',
    'era_decade': 'Era', 'camera_format': 'Format',
    'performance_emotion': 'Emotion',
    'subjects': 'Objects',
    'my_work': 'My Work',
}

# V15: categories only a human can apply — the AI tagger never writes these,
# and re-tagging an image must never delete them. 'my_work' marks Ryan's own
# projects (gaffed / DP'd / photographed); 'misc' is the free-form bucket the
# manual tag editor uses when no category is picked.
MANUAL_TAG_CATEGORIES = ('misc', 'my_work')

# V32: how many `?` placeholders we're willing to put in one statement. SQLite
# has a hard cap (999 on older builds), and "remove this tag from all 2,000
# results" can now hand a query the whole filtered library in one go, so any
# id list that could come from a select-all gets sliced into batches this big.
SQL_PARAM_CHUNK = 400

def chunked(seq, size=SQL_PARAM_CHUNK):
    """Slice a list into batches small enough to pass as SQL placeholders."""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]

# V30: words where stripping a trailing 's' would be wrong — either it isn't
# a plural at all (glass, lens, gas), or the plural is itself the natural
# search term (hands: "two hands in frame" is a distinct, useful composition
# detail from "a hand," not drift to collapse away).
TAG_PLURAL_STRIP_EXCEPTIONS = {
    'glass', 'glasses', 'sunglasses', 'grass', 'lens', 'bus', 'gas',
    'dress', 'stairs', 'clothes', 'scissors', 'binoculars', 'headlights',
    'hands', 'news', 'series', 'species',
}

def normalize_tag_value(value):
    """Lowercase and (V30) collapse a trailing plural 's' so 'car' and 'cars'
    land as the same searchable tag. Only the fixed-vocabulary categories
    (mood, location_type, etc.) are truly closed lists — `subjects` is
    explicitly open-ended free text in the tagging prompt, which is exactly
    where an LLM's own singular/plural word choice drifts run to run. This
    mirrors the lowercase-casing fix already applied at every tag-write site
    for the same reason (Gemini's casing isn't consistent either).

    Deliberately conservative: only strips a bare trailing 's' (not 'es'/
    'ies', which usually change the stem and are more likely a genuinely
    singular word that happens to end in 's') and skips a short exception
    list where the plural is itself the natural tag."""
    v = (value or '').strip().lower()
    if len(v) > 3 and v.endswith('s') and not v.endswith('ss') and v not in TAG_PLURAL_STRIP_EXCEPTIONS:
        return v[:-1]
    return v

def clear_ai_tags(cursor, image_id):
    """Delete an image's AI-written tags ahead of a re-tag, preserving every
    manually-applied category (see MANUAL_TAG_CATEGORIES)."""
    ph = ','.join('?' * len(MANUAL_TAG_CATEGORIES))
    cursor.execute(
        f"DELETE FROM tags WHERE image_id = ? AND category NOT IN ({ph})",
        (image_id, *MANUAL_TAG_CATEGORIES))


def favorite_col(user_id, alias='images'):
    """SQL fragment computing is_favorite for one user against the per-user
    user_favorites table — slots into any `images` SELECT in place of the old
    boolean column. user_id is always an int pulled from the session (never
    request input), so inlining it directly is safe and avoids threading an
    extra positional param through call sites that already build dynamic
    WHERE clauses.

    (V55: used to also compute is_flagged against user_flags for the since-
    removed Flagged feature. That table and the legacy is_flagged column on
    `images` deliberately still exist — see the removal note above
    get_utility_view() — but nothing queries or serves is_flagged anymore, so
    this function only computes the one column it's now named for.)

    Day 31 (Phase 3): moved here from app.py so images_common._fetch_image_dict
    can build its own favourite-aware SELECT without importing app.py. app.py's
    other call sites are unchanged — it re-imports this name from core."""
    uid = int(user_id)
    return f"EXISTS(SELECT 1 FROM user_favorites uf WHERE uf.user_id = {uid} AND uf.image_id = {alias}.id) AS is_favorite"


def _shuffle_key(seed, image_id):
    # Deterministic pseudo-random sort key: the same (seed, image) pair always
    # produces the same number, so page 2 of a shuffled feed continues exactly
    # where page 1 left off. A new seed produces a completely different order.
    # crc32 (unlike Python's hash()) gives identical results across restarts.
    return zlib.crc32(f'{seed}:{image_id}'.encode())


def get_db():
    # db_path() (not a module-level constant) so a multi-boot test harness
    # picks up each instance's FA_DB_PATH — see the note above.
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.create_function('shuffle_key', 2, _shuffle_key)
    return conn


# ============================================================================
# LOGIN GATE + ADMIN DECORATOR (Day 36 / Phase 3 — moved from app.py)
# ============================================================================
# The whole point of moving this here: every route blueprint (routes_auth.py
# today, more to follow) needs @admin_required and the session helpers, and
# none of them may import app.py (that's the circular-import rule this phase
# runs on). app.py still owns registering the gate itself, since only it has
# the `app` object _adopt_session_from_header needs — but the CHECK's logic
# lives here so it's one function, not something re-implemented per blueprint.

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

# Same "am I local, not production" signal db_path()'s fallback and the
# session cookie's Secure flag already key off of. A plain module attribute,
# not a live-computing function like db_path() — test_day28_hardening_locally.py
# overrides it to a fixed False to exercise the real (non-local) rate-limiting
# path regardless of what FA_DB_PATH is actually set to in the test harness,
# and a function recomputing from the env var every call couldn't be overridden
# that way.
RUNNING_LOCALLY = bool(os.environ.get('FA_DB_PATH'))


def current_user_id():
    return session.get('user_id')


def adopt_session_from_header(app):
    """Let the browser extension reuse the login you already have.

    Session cookies are SameSite-restricted, so they don't ride along on a
    request made from a chrome-extension:// origin. The extension reads the
    cookie itself (chrome.cookies) and echoes the value in X-FA-Session; this
    verifies that signature with Flask's own serializer and adopts it.

    Not a CSRF hole: a custom header can't be set by a cross-site form or
    image tag, and the value is the very cookie the caller would have needed
    anyway — no new capability, just a different envelope.

    Takes `app` as a parameter (rather than closing over a module-level Flask
    app) because this module is imported before any Flask app exists — it has
    to work for whichever app object app.py hands it."""
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


def check_login_required(app):
    """The actual @app.before_request gate logic. app.py registers this by
    wrapping it in its own before_request function (the decorator itself has
    to live in app.py, since it needs the `app` object to register against) —
    but the check itself lives here so PUBLIC_API_ROUTES and the adopt-from-
    header fallback are defined once, not duplicated if a future blueprint
    ever needed its own pre-request check."""
    path = request.path
    if not path.startswith('/api/'):
        return None
    if path in PUBLIC_API_ROUTES or path.startswith('/api/share/'):
        return None
    if not session.get('user_id'):
        adopt_session_from_header(app)
    if session.get('user_id'):
        return None
    return jsonify({'error': 'login_required'}), 401


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get('role') != 'admin':
            return jsonify({'error': 'admin_required'}), 403
        return fn(*args, **kwargs)
    return wrapper


# ── V75 owner scoping for bulk metadata edits (moved here Day 38) ───────────
# Used by routes_tags.py's bulk tag endpoints AND app.py's bulk filmography
# endpoints (Day 39's routes_images.py), so it lives in core rather than in
# either blueprint.
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

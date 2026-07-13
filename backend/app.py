import os
import json
import base64
import secrets
import io
import gzip
import sqlite3
import time
import threading
import queue as queue_module
from array import array
from datetime import datetime
from functools import wraps
from flask import Flask, jsonify, request, send_file, send_from_directory, Response, stream_with_context, redirect, session
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image
import google.auth.transport.requests
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google import genai as genai_client

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

DB_PATH = '/app/data/library.db'

# Gemini model — overridable via Railway env var if Google retires this one
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')

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
}

sync_state = {
    'in_progress': False,
    'user_id': None,  # whose sync is running — one sync at a time, app-wide (Day 14 Stage 2)
    'processed': 0,
    'total': 0,
    'current_file': '',
    'errors': []
}

_tag_progress = {
    'running': False,
    'total': 0,
    'done': 0,
    'failed': 0,
    'status': 'idle',
    'message': ''
}
_tag_progress_lock = threading.Lock()
_sse_queues = []
_sse_lock = threading.Lock()

# ============================================================================
# GEMINI TAG TAXONOMY PROMPT
# ============================================================================

GEMINI_TAGGING_PROMPT = """Analyze this image and return ONLY a JSON object with no markdown, no backticks, no explanation.

Return exactly this structure:
{
  "caption": "One vivid sentence describing the image cinematically (e.g. 'Lone figure at rain-soaked payphone, hard sodium backlight, urban night')",
  "tags": {
    "mood": [],
    "lighting_quality": [],
    "lighting_color_temperature": [],
    "color_palette": [],
    "shot_type": [],
    "framing_composition": [],
    "location_type": [],
    "time_of_day_weather": [],
    "source_type": [],
    "subject_count": [],
    "subject_camera_relationship": [],
    "performance_emotion": [],
    "genre_aesthetic": [],
    "era_decade": [],
    "camera_format": []
  },
  "filmography": {
    "title": null,
    "director": null,
    "dp": null,
    "year": null
  }
}

ONLY use tags from these allowed lists.

BE GENEROUS. This is a searchable reference library for a working cinematographer —
more tags means more discoverability. Include every tag that plausibly applies, not
just the single most obvious one per category. If an image sits between two moods,
tag both. If the lighting could read as both soft and low-key, tag both.
Aim for 12-25 tags total across all categories. Most categories should have at
least one tag; only leave an array empty [] when the category truly does not apply
(e.g. performance_emotion for a landscape with no people).

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

For filmography: only fill in if this is clearly a recognizable film still. Otherwise leave null.
Return ONLY the JSON. No other text."""

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

Pick the 2-5 tags that best capture the FEELING and VISUAL QUALITIES of the phrase.
Return ONLY a JSON array of tag strings, e.g. ["lonely","low-key","night"]. No markdown, no explanation.

Phrase: """

# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            drive_folder_id TEXT,
            gemini_api_key TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            drive_file_id TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            thumbnail_blob BLOB NOT NULL,
            caption TEXT,
            aspect_ratio TEXT,
            tagging_status TEXT DEFAULT 'pending',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_favorite INTEGER DEFAULT 0,
            is_flagged INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            value TEXT NOT NULL,
            FOREIGN KEY (image_id) REFERENCES images(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS colors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            hex TEXT NOT NULL,
            rank INTEGER,
            FOREIGN KEY (image_id) REFERENCES images(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            clip_vector BLOB,
            FOREIGN KEY (image_id) REFERENCES images(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS filmography (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER NOT NULL,
            title TEXT,
            director TEXT,
            dp TEXT,
            year TEXT,
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS saved_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            chips_json TEXT,
            nl_phrase TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS decks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            share_token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS scenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            sort_order INTEGER,
            FOREIGN KEY (deck_id) REFERENCES decks(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS deck_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL,
            scene_id INTEGER,
            image_id INTEGER NOT NULL,
            storyboard_order INTEGER,
            storyboard_note TEXT,
            FOREIGN KEY (deck_id) REFERENCES decks(id),
            FOREIGN KEY (scene_id) REFERENCES scenes(id),
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS sync_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            folder_id TEXT NOT NULL,
            folder_name TEXT,
            last_sync TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # DAY 14 (V13): invite-only accounts + per-user favorites/flags.
    c.execute('''
        CREATE TABLE IF NOT EXISTS invite_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            created_by INTEGER NOT NULL,
            used_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            used_at TIMESTAMP,
            FOREIGN KEY (created_by) REFERENCES users(id),
            FOREIGN KEY (used_by) REFERENCES users(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS user_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            image_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, image_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS user_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            image_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, image_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')

    conn.commit()

    try:
        c.execute("ALTER TABLE images ADD COLUMN tagging_status TEXT DEFAULT 'pending'")
        conn.commit()
        print("[migration] Added tagging_status column")
    except Exception:
        pass

    # V7: fingerprints for duplicate detection.
    # md5_checksum = exact-file fingerprint (comes free from Drive metadata)
    # phash        = perceptual hash, a visual fingerprint that survives resizing/re-saving
    for _col in ('md5_checksum', 'phash'):
        try:
            c.execute(f"ALTER TABLE images ADD COLUMN {_col} TEXT")
            conn.commit()
            print(f"[migration] Added {_col} column")
        except Exception:
            pass

    # V7 part 2: holds the signed-in user's Google OAuth token (for uploads),
    # separate from the read-only service account used for sync/download.
    try:
        c.execute("ALTER TABLE users ADD COLUMN google_oauth_token TEXT")
        conn.commit()
        print("[migration] Added google_oauth_token column")
    except Exception:
        pass

    # V13 (Day 14): admin's login email.
    try:
        c.execute("ALTER TABLE users ADD COLUMN email TEXT")
        conn.commit()
        print("[migration] Added email column")
    except Exception:
        pass

    c.execute("""
        INSERT INTO users (id, username, password_hash)
        SELECT 1, 'ryan', ''
        WHERE NOT EXISTS (SELECT 1 FROM users WHERE id = 1)
    """)
    conn.commit()

    c.execute("""
        UPDATE images SET tagging_status = 'done'
        WHERE id IN (SELECT DISTINCT image_id FROM tags)
        AND tagging_status = 'pending'
    """)
    conn.commit()

    # V13 (Day 14): the old is_favorite/is_flagged columns on `images` were a
    # single shared on/off switch — replaced by per-user user_favorites/
    # user_flags tables. One-time backfill: whatever was starred/flagged
    # before logins existed becomes user 1's (the admin's) favorites/flags.
    # INSERT OR IGNORE makes this safe to run on every boot.
    c.execute("""
        INSERT OR IGNORE INTO user_favorites (user_id, image_id)
        SELECT 1, id FROM images WHERE is_favorite = 1
    """)
    c.execute("""
        INSERT OR IGNORE INTO user_flags (user_id, image_id)
        SELECT 1, id FROM images WHERE is_flagged = 1
    """)
    conn.commit()

    # One-time cleanup: the AI tagging pipeline wasn't lowercasing Gemini's
    # output (fixed above), so a tag like "Tense" from one run and "tense"
    # from another could sit as two case-different rows on the same image —
    # invisible as a real duplicate anywhere tags get grouped (autocomplete,
    # search dropdown), since SQLite groups strings case-sensitively.
    # Idempotent: a no-op once everything's already lowercase and deduped.
    c.execute("UPDATE tags SET value = LOWER(value) WHERE value != LOWER(value)")
    c.execute("""
        DELETE FROM tags WHERE id NOT IN (
            SELECT MIN(id) FROM tags GROUP BY image_id, category, value
        )
    """)
    conn.commit()
    conn.close()

def load_embeddings_seed():
    """Loads pre-computed CLIP vectors (backend/embeddings_seed.json.gz) into
    the `embeddings` table, so the visual-similarity feature works without
    running CLIP on the server itself (Pillow/torch don't build here anyway —
    see Day 9 notes). The seed file is generated by a separate offline script
    and shipped in the repo. Safe to call on every boot: it's a no-op once the
    DB already matches the seed."""
    seed_path = os.path.join(os.path.dirname(__file__), 'embeddings_seed.json.gz')
    if not os.path.exists(seed_path):
        print("Embeddings seed: no embeddings_seed.json.gz found, skipping")
        return

    try:
        with gzip.open(seed_path, 'rt', encoding='utf-8') as f:
            seed = json.load(f)
        vectors = seed.get('vectors', {})
    except Exception as e:
        print(f"Embeddings seed: failed to read/parse file ({e}), skipping")
        return

    if not vectors:
        print("Embeddings seed: file has no vectors, skipping")
        return

    conn = get_db()
    c = conn.cursor()

    # Fast-path: if the table already has exactly as many rows as the seed
    # has vectors, and every seeded image_id already has a row, there's
    # nothing to do — skip the rewrite so boots stay quick.
    existing_count = c.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    if existing_count == len(vectors):
        seed_ids = set(int(k) for k in vectors.keys())
        existing_ids = set(r[0] for r in c.execute("SELECT image_id FROM embeddings").fetchall())
        if seed_ids == existing_ids:
            print(f"Embeddings seed: already up to date ({existing_count} vectors), skipping")
            conn.close()
            return

    valid_ids = set(r[0] for r in c.execute("SELECT id FROM images").fetchall())

    loaded = 0
    skipped = 0
    for image_id_str, vec in vectors.items():
        image_id = int(image_id_str)
        if image_id not in valid_ids:
            skipped += 1
            continue
        blob = array('f', vec).tobytes()
        c.execute("DELETE FROM embeddings WHERE image_id = ?", (image_id,))
        c.execute(
            "INSERT INTO embeddings (image_id, user_id, clip_vector) VALUES (?, 1, ?)",
            (image_id, blob)
        )
        loaded += 1

    conn.commit()
    conn.close()
    print(f"Embeddings seed: loaded {loaded} vectors ({skipped} skipped)")

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
    '/api/setup',
    '/api/setup/status',
}

@app.before_request
def require_login():
    path = request.path
    if not path.startswith('/api/'):
        return None
    if path in PUBLIC_API_ROUTES or path.startswith('/api/share/'):
        return None
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

def fav_flag_cols(user_id, alias='images'):
    """SQL fragment computing is_favorite/is_flagged for one user against the
    per-user user_favorites/user_flags tables — slots into any `images` SELECT
    in place of the old boolean columns. user_id is always an int pulled from
    the session (never request input), so inlining it directly is safe and
    avoids threading extra positional params through call sites that already
    build dynamic WHERE clauses."""
    uid = int(user_id)
    return (
        f"EXISTS(SELECT 1 FROM user_favorites uf WHERE uf.user_id = {uid} AND uf.image_id = {alias}.id) AS is_favorite, "
        f"EXISTS(SELECT 1 FROM user_flags fl WHERE fl.user_id = {uid} AND fl.image_id = {alias}.id) AS is_flagged"
    )

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
        'SELECT id, username, email, role, password_hash FROM users WHERE username = ? COLLATE NOCASE',
        (username,)
    ).fetchone()
    conn.close()

    if not row or not row['password_hash'] or not check_password_hash(row['password_hash'], password):
        return jsonify({'error': 'Invalid username or password'}), 401

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
    data = request.get_json(force=True) or {}
    invite_code = (data.get('invite_code') or '').strip()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not invite_code or not username or len(password) < 8:
        return jsonify({'error': 'Invite code, username, and an 8+ character password are all required'}), 400

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

    c.execute(
        'INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)',
        (username, generate_password_hash(password), 'user')
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
    return jsonify({'success': True, 'user': {'id': new_user_id, 'username': username, 'email': None, 'role': 'user'}})

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
# TAGGING PROGRESS — SSE HELPERS
# ============================================================================

def _broadcast_progress():
    with _tag_progress_lock:
        data = dict(_tag_progress)
    pct = int(data['done'] / data['total'] * 100) if data['total'] > 0 else 0
    payload = json.dumps({**data, 'pct': pct})
    with _sse_lock:
        dead = []
        for q in _sse_queues:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_queues.remove(q)

# ============================================================================
# TAGGING WORKER
# ============================================================================

def _run_tagging_job():
    gemini_api_key = os.environ.get('GEMINI_API_KEY')
    if not gemini_api_key:
        with _tag_progress_lock:
            _tag_progress.update({'status': 'error', 'message': 'GEMINI_API_KEY not set'})
        _broadcast_progress()
        return

    client = genai_client.Client(api_key=gemini_api_key)

    conn = get_db()
    c = conn.cursor()

    c.execute("""
        SELECT id, thumbnail_blob, filename
        FROM images
        WHERE tagging_status != 'done'
        ORDER BY
            CASE tagging_status
                WHEN 'pending' THEN 0
                WHEN 'failed'  THEN 1
                ELSE 2
            END,
            id ASC
    """)
    images = c.fetchall()
    conn.close()

    with _tag_progress_lock:
        _tag_progress.update({
            'running': True,
            'total': len(images),
            'done': 0,
            'failed': 0,
            'status': 'running',
            'message': f'Tagging {len(images)} images…'
        })
    _broadcast_progress()

    for img in images:
        img_id = img['id']
        thumb_blob = img['thumbnail_blob']
        filename = img['filename']

        try:
            pil_img = Image.open(io.BytesIO(thumb_blob))

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[GEMINI_TAGGING_PROMPT, pil_img]
            )
            raw = response.text.strip()

            if raw.startswith('```'):
                raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()

            data = json.loads(raw)

            conn = get_db()
            c = conn.cursor()

            c.execute("DELETE FROM tags WHERE image_id = ?", (img_id,))
            for category, values in data.get('tags', {}).items():
                for val in values:
                    if val and val.strip():
                        # Lowercase to match every other tag-writing path
                        # (manual edit, bulk apply) — Gemini's casing isn't
                        # consistent run to run, and SQLite string grouping
                        # is case-sensitive, so "Tense" and "tense" would
                        # otherwise sit as two separate-looking duplicates
                        # anywhere tags get grouped (autocomplete, detail
                        # panel, analytics).
                        c.execute(
                            "INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, ?, ?)",
                            (img_id, category, val.strip().lower())
                        )

            caption = data.get('caption', '')
            if caption:
                c.execute("UPDATE images SET caption = ? WHERE id = ?", (caption, img_id))

            film = data.get('filmography', {})
            if any(film.get(k) for k in ['title', 'director', 'dp', 'year']):
                c.execute("DELETE FROM filmography WHERE image_id = ?", (img_id,))
                c.execute(
                    "INSERT INTO filmography (image_id, title, director, dp, year) VALUES (?,?,?,?,?)",
                    (img_id, film.get('title'), film.get('director'), film.get('dp'), str(film.get('year', '')))
                )

            c.execute("UPDATE images SET tagging_status = 'done' WHERE id = ?", (img_id,))
            conn.commit()
            conn.close()

            with _tag_progress_lock:
                _tag_progress['done'] += 1
                remaining = _tag_progress['total'] - _tag_progress['done']
                _tag_progress['message'] = f"Tagged {_tag_progress['done']} of {_tag_progress['total']} — {remaining} remaining"

        except Exception as e:
            print(f"[tagging] Failed {filename}: {e}")
            try:
                conn = get_db()
                c = conn.cursor()
                c.execute("UPDATE images SET tagging_status = 'failed' WHERE id = ?", (img_id,))
                conn.commit()
                conn.close()
            except Exception:
                pass
            with _tag_progress_lock:
                _tag_progress['failed'] += 1
                _tag_progress['done'] += 1

        _broadcast_progress()
        time.sleep(0.05)

    with _tag_progress_lock:
        failed = _tag_progress['failed']
        total = _tag_progress['total']
        _tag_progress.update({
            'running': False,
            'status': 'complete',
            'message': f"Sync complete! Tagged {total - failed} images." + (f" {failed} failed." if failed else "")
        })
    _broadcast_progress()


def trigger_tagging():
    with _tag_progress_lock:
        if _tag_progress['running']:
            return
    t = threading.Thread(target=_run_tagging_job, daemon=True)
    t.start()

# ============================================================================
# GOOGLE DRIVE & SYNC FUNCTIONS
# ============================================================================

def get_drive_service():
    creds_json = os.environ.get('GOOGLE_DRIVE_CREDENTIALS')
    if not creds_json:
        raise ValueError("GOOGLE_DRIVE_CREDENTIALS environment variable not set")

    creds_dict = json.loads(creds_json)
    credentials = Credentials.from_service_account_info(
        creds_dict,
        # Full drive scope so delete can move files to _Removed. Actual power is
        # still capped by what the folder share grants the service account
        # (Viewer = read-only, Editor = can move files).
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=credentials)

REMOVED_FOLDER_NAME = '_Removed'

# Upload uses a separate OAuth sign-in (acting as Ryan) rather than the
# read-only service account, since the account needs write access to create
# files. drive.file is the narrowest scope that allows creating new files —
# it only ever sees files this app itself created, not the whole Drive.
UPLOAD_SCOPES = ['https://www.googleapis.com/auth/drive.file']

def get_oauth_flow(redirect_uri):
    client_config = {
        "web": {
            "client_id": os.environ.get('GOOGLE_OAUTH_CLIENT_ID'),
            "client_secret": os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET'),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    return Flow.from_client_config(client_config, scopes=UPLOAD_SCOPES, redirect_uri=redirect_uri)

def get_user_credentials(user_id):
    """Refreshed google-auth Credentials for this user's own Google sign-in
    (Day 8, generalized Day 14 Stage 2 — used to be admin-only/hardcoded to
    user 1). Returns None if that user hasn't connected Google yet."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT google_oauth_token FROM users WHERE id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if not row or not row['google_oauth_token']:
        return None

    creds = UserCredentials.from_authorized_user_info(json.loads(row['google_oauth_token']), UPLOAD_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        conn = get_db()
        c = conn.cursor()
        c.execute('UPDATE users SET google_oauth_token = ? WHERE id = ?', (creds.to_json(), user_id))
        conn.commit()
        conn.close()
    return creds

def get_user_drive_service(user_id):
    """Drive client acting as the given signed-in user. Returns None if that
    user hasn't connected Google yet."""
    creds = get_user_credentials(user_id)
    return build('drive', 'v3', credentials=creds) if creds else None

def list_images_in_folder(service, folder_id, page_token=None):
    images = []
    query = f"'{folder_id}' in parents and trashed=false"
    results = service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name, mimeType, size, md5Checksum), nextPageToken',
        pageSize=100,
        pageToken=page_token
    ).execute()

    items = results.get('files', [])
    for item in items:
        if item['mimeType'] == 'application/vnd.google-apps.folder':
            # Deleted images live in _Removed — never re-import them
            if item['name'] == REMOVED_FOLDER_NAME:
                continue
            images.extend(list_images_in_folder(service, item['id']))
        elif item['mimeType'] in ['image/jpeg', 'image/png', 'image/webp', 'image/gif']:
            images.append(item)

    if 'nextPageToken' in results:
        images.extend(list_images_in_folder(service, folder_id, results['nextPageToken']))

    return images

def generate_thumbnail(image_data, width=800, quality=85):
    try:
        img = Image.open(io.BytesIO(image_data))
        aspect_ratio = img.width / img.height if img.height > 0 else 1
        # Never upscale — a source narrower than the target stays at native size
        if img.width > width:
            height = int(width / aspect_ratio)
            img = img.resize((width, height), Image.Resampling.LANCZOS)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        thumb_io = io.BytesIO()
        img.save(thumb_io, format='JPEG', quality=quality)
        thumb_io.seek(0)
        return thumb_io.getvalue()
    except Exception:
        return None

def compute_phash(image_data):
    """Perceptual 'difference hash' — a 64-bit visual fingerprint.

    Shrinks the image to a 9x8 grayscale grid and records, for each pixel,
    whether it's brighter than its right-hand neighbor. Two visually identical
    images (even resized, screenshotted, or re-saved) produce nearly identical
    fingerprints; counting differing bits (hamming distance) measures how
    visually different they are."""
    try:
        img = Image.open(io.BytesIO(image_data)).convert('L').resize(
            (9, 8), Image.Resampling.LANCZOS)
        px = list(img.getdata())
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
        return f'{bits:016x}'
    except Exception:
        return None

def phash_distance(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count('1')

# At or below this many differing bits (out of 64), two images are considered
# near-duplicates. 0 = pixel-identical layout; 6 tolerates resize/re-compress.
PHASH_NEAR_DUP_THRESHOLD = 6

def get_root_folder_id(user_id):
    """The Drive folder being synced for this user — where their _Removed
    lives. MUST be scoped by user_id: with more than one person syncing,
    picking "whichever sync_settings row is newest" (the old behavior) could
    silently return a different user's folder."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT folder_id FROM sync_settings WHERE user_id = ? ORDER BY id DESC LIMIT 1', (user_id,))
    row = c.fetchone()
    conn.close()
    return row['folder_id'] if row else '1LHPVyo3QjOEcizc1Io2UVjxzX4FQ7yDG'

def get_or_create_removed_folder(service, root_id):
    q = (f"'{root_id}' in parents and name = '{REMOVED_FOLDER_NAME}' "
         "and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    res = service.files().list(q=q, fields='files(id)').execute()
    found = res.get('files', [])
    if found:
        return found[0]['id']
    meta = {
        'name': REMOVED_FOLDER_NAME,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [root_id],
    }
    return service.files().create(body=meta, fields='id').execute()['id']

def get_image_aspect_ratio(image_data):
    try:
        img = Image.open(io.BytesIO(image_data))
        width, height = img.size
        from math import gcd
        g = gcd(width, height)
        return f"{width // g}:{height // g}"
    except Exception:
        return "16:9"

# Standard cinematography formats — raw ratios like 80:43 get displayed as the
# nearest of these. The exact ratio stays in the DB for layout math.
STANDARD_ASPECT_RATIOS = [
    ('9:16', 9 / 16),
    ('2:3', 2 / 3),
    ('3:4', 3 / 4),
    ('4:5', 4 / 5),
    ('1:1', 1.0),
    ('4:3', 4 / 3),
    ('3:2', 3 / 2),
    ('16:9', 16 / 9),
    ('1.85:1', 1.85),
    ('2:1', 2.0),
    ('2.39:1', 2.39),
]

def normalize_ar_label(ar_float):
    from math import log
    if not ar_float or ar_float <= 0:
        return '16:9'
    # Log distance treats "too wide" and "too tall" symmetrically
    return min(STANDARD_ASPECT_RATIOS, key=lambda s: abs(log(ar_float / s[1])))[0]

def build_image_dict(row, tags, palette, filmography):
    """Turns one `images` row (must include id, filename, thumbnail_blob,
    caption, aspect_ratio, is_favorite, is_flagged) into the JSON shape used
    by both /api/search and /api/images/<id>/similar. Keep these two routes
    using this single helper so their image objects can never drift apart."""
    ar_str = row['aspect_ratio'] or '16:9'
    try:
        w, h = ar_str.split(':', 1) if ':' in ar_str else (ar_str, '1')
        ar_float = float(w) / float(h)
    except Exception:
        ar_float = 16 / 9

    thumb_b64 = base64.b64encode(row['thumbnail_blob']).decode('utf-8')
    return {
        'id': row['id'],
        'filename': row['filename'],
        'thumbnail': f'data:image/jpeg;base64,{thumb_b64}',
        'caption': row['caption'] or '',
        'aspect_ratio': ar_str,
        'ar_label': normalize_ar_label(ar_float),
        'ar_float': round(ar_float, 4),
        'is_favorite': bool(row['is_favorite']),
        'is_flagged': bool(row['is_flagged']),
        'tags': tags,
        'palette': palette,
        'filmography': filmography
    }

def hydrate_image_rows(c, rows):
    """Given a list of `images` rows (each must include the columns
    build_image_dict needs), bulk-fetch their tags, palettes, and filmography
    in three queries and return finished image dicts. Shared by /api/search
    and the Day 13 utility views so their payloads can never drift apart."""
    img_ids = [r['id'] for r in rows]
    tags_map = {}
    colors_map = {}
    film_map = {}

    if img_ids:
        ph = ','.join('?' * len(img_ids))
        for tr in c.execute(f'SELECT image_id, category, value FROM tags WHERE image_id IN ({ph})', img_ids).fetchall():
            tags_map.setdefault(tr['image_id'], []).append({'category': tr['category'], 'value': tr['value']})
        for cr in c.execute(f'SELECT image_id, hex FROM colors WHERE image_id IN ({ph}) ORDER BY rank ASC', img_ids).fetchall():
            colors_map.setdefault(cr['image_id'], []).append(cr['hex'])
        for fr in c.execute(f'SELECT image_id, title, director, dp, year FROM filmography WHERE image_id IN ({ph})', img_ids).fetchall():
            film_map[fr['image_id']] = {
                'title': fr['title'], 'director': fr['director'],
                'dp': fr['dp'], 'year': fr['year']
            }

    return [
        build_image_dict(r, tags_map.get(r['id'], []), colors_map.get(r['id'], []), film_map.get(r['id']))
        for r in rows
    ]

def extract_palette(image_data, num_colors=10):
    """Vibrance-weighted palette. Colors are scored by area x saturation, so a
    small patch of vivid red outranks a large gray wall. Binning happens in HSV,
    which keeps vivid regions from being averaged into their muddy surroundings
    (the old quantize approach turned bright gems on dark backgrounds into
    sludge). Vivid colors fill the top ranks — color search reads those — and
    neutrals ride along at the end."""
    try:
        import colorsys
        img = Image.open(io.BytesIO(image_data)).convert('RGB')
        img.thumbnail((160, 160))

        bins = {}
        for r, g, b in img.getdata():
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            if s < 0.16 or v < 0.10:
                key = ('n', min(int(v * 5), 4))
            else:
                key = ('c', min(int(h * 20), 19), min(int(s * 3), 2), min(int(v * 4), 3))
            acc = bins.get(key)
            if acc is None:
                bins[key] = [1, r, g, b, s, v]
            else:
                acc[0] += 1; acc[1] += r; acc[2] += g; acc[3] += b; acc[4] += s; acc[5] += v

        total_px = img.width * img.height
        chromatic, neutrals = [], []
        for key, (n, rs, gs, bs, ss, vs) in bins.items():
            avg = (rs // n, gs // n, bs // n)
            sat, val = ss / n, vs / n
            share = n / total_px
            if key[0] == 'c':
                score = share * (0.15 + 2.5 * sat * sat) * (0.4 + 1.2 * val)
                chromatic.append((score, share, avg))
            else:
                neutrals.append((share * 0.2, share, avg))

        def _dist(a, b):
            return ((a[0]-b[0]) * 0.30) ** 2 + ((a[1]-b[1]) * 0.59) ** 2 + ((a[2]-b[2]) * 0.11) ** 2

        def _is_dup(rgb, chosen):
            # Hue-aware dedupe: the brightness-weighted distance alone thinks
            # dark green == dark brown and white == pale sage. Colors from
            # different hue families never merge unless nearly identical.
            h1, s1, v1 = colorsys.rgb_to_hsv(rgb[0]/255.0, rgb[1]/255.0, rgb[2]/255.0)
            for c in chosen:
                h2, s2, v2 = colorsys.rgb_to_hsv(c[0]/255.0, c[1]/255.0, c[2]/255.0)
                d = _dist(rgb, c)
                if d < 120:  # nearly identical regardless of hue
                    return True
                if s1 > 0.16 and s2 > 0.16:
                    hd = abs(h1 - h2)
                    hd = min(hd, 1 - hd)  # hue wraps around the color wheel
                    if d < 450 and hd < 0.09:
                        return True
                elif s1 <= 0.16 and s2 <= 0.16:
                    if abs(v1 - v2) < 0.25:  # spread neutrals across brightness
                        return True
            return False

        chromatic.sort(reverse=True)
        neutrals.sort(reverse=True)

        picked = []
        for score, share, rgb in chromatic:
            if len(picked) >= num_colors - 2:
                break
            if share < 0.001:  # under ~0.1% of pixels = JPEG noise, not a color
                continue
            if not _is_dup(rgb, picked):
                picked.append(rgb)
        for score, share, rgb in neutrals:
            if len(picked) >= num_colors:
                break
            if share < 0.01:  # a neutral must cover at least 1% of the frame
                continue
            if not _is_dup(rgb, picked):
                picked.append(rgb)

        return ['#%02x%02x%02x' % c for c in picked]
    except Exception:
        return []

def save_palette(image_id, user_id, hexes):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM colors WHERE image_id = ?', (image_id,))
    for rank, hex_color in enumerate(hexes):
        c.execute(
            'INSERT INTO colors (image_id, user_id, hex, rank) VALUES (?, ?, ?, ?)',
            (image_id, user_id, hex_color, rank)
        )
    conn.commit()
    conn.close()

def hex_to_rgb(hex_color):
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def color_distance(hex_a, hex_b):
    ra, ga, ba = hex_to_rgb(hex_a)
    rb, gb, bb = hex_to_rgb(hex_b)
    # Weighted RGB distance — human eyes are most sensitive to green
    return ((rb - ra) * 0.30) ** 2 + ((gb - ga) * 0.59) ** 2 + ((bb - ba) * 0.11) ** 2

def sync_folder_worker(folder_id, user_id):
    global sync_state
    try:
        sync_state['in_progress'] = True
        sync_state['user_id'] = user_id
        sync_state['processed'] = 0
        sync_state['total'] = 0
        sync_state['current_file'] = ''
        sync_state['errors'] = []

        # Admin keeps syncing through the shared read-only service account
        # (unchanged since Day 2/3). Everyone else (Day 14 Stage 2) syncs
        # through their OWN Google connection, since the service account has
        # no access to a friend's personal Drive folder at all.
        if user_id == 1:
            service = get_drive_service()
        else:
            service = get_user_drive_service(user_id)
            if not service:
                sync_state['errors'].append('Google Drive is not connected — reconnect and try again.')
                return
        print(f"Listing images in folder {folder_id}...")
        all_images = list_images_in_folder(service, folder_id)
        sync_state['total'] = len(all_images)

        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT drive_file_id FROM images WHERE user_id = ?', (user_id,))
        existing_ids = set(row[0] for row in c.fetchall())
        conn.close()

        new_count = 0
        for image in all_images:
            try:
                file_id = image['id']
                filename = image['name']

                if file_id in existing_ids:
                    sync_state['processed'] += 1
                    continue

                sync_state['current_file'] = filename

                req = service.files().get_media(fileId=file_id)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, req)
                done = False
                while not done:
                    status, done = downloader.next_chunk()

                image_data = fh.getvalue()
                thumbnail = generate_thumbnail(image_data)
                if not thumbnail:
                    sync_state['errors'].append(f"Failed thumbnail: {filename}")
                    sync_state['processed'] += 1
                    continue

                aspect_ratio = get_image_aspect_ratio(image_data)

                conn = get_db()
                c = conn.cursor()
                c.execute('''
                    INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status, md5_checksum, phash)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                ''', (user_id, file_id, filename, thumbnail, aspect_ratio,
                      image.get('md5Checksum'), compute_phash(thumbnail)))
                new_image_id = c.lastrowid
                conn.commit()
                conn.close()

                hexes = extract_palette(thumbnail)
                if hexes:
                    save_palette(new_image_id, user_id, hexes)

                new_count += 1
                sync_state['processed'] += 1

            except Exception as e:
                sync_state['errors'].append(f"{filename}: {str(e)}")
                sync_state['processed'] += 1
                continue

        conn = get_db()
        c = conn.cursor()
        c.execute('''
            UPDATE sync_settings SET last_sync = CURRENT_TIMESTAMP
            WHERE user_id = ? AND folder_id = ?
        ''', (user_id, folder_id))
        conn.commit()
        conn.close()

        print(f"Sync complete. {new_count} new images added.")

    except Exception as e:
        sync_state['errors'].append(f"Sync failed: {str(e)}")
    finally:
        sync_state['in_progress'] = False
        # Auto-tagging after sync uses the shared admin Gemini key (Day 5) —
        # only fire it for the admin's own library. Stage 2b will give each
        # user their own tag-my-photos trigger using their own key; until
        # then a friend's newly-synced photos just sit untagged (searchable,
        # zero cost) rather than silently spending the admin's budget.
        if user_id == 1:
            trigger_tagging()

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
        trigger_tagging()
    return jsonify({'success': True, 'reset': affected, 'message': f'Reset {affected} failed images, tagging started'})

@app.route('/api/config', methods=['GET'])
def config():
    return jsonify({
        'app_name': 'Frame Atlas', 'version': 'V5', 'gemini_model': GEMINI_MODEL,
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

@app.route('/api/sync/start', methods=['POST'])
def start_sync():
    user_id = session['user_id']

    if sync_state['in_progress']:
        return jsonify({'error': 'Sync already in progress', 'user_id': sync_state['user_id']}), 400

    if user_id != 1 and not get_user_credentials(user_id):
        return jsonify({'error': 'not_signed_in', 'message': 'Connect Google Drive first.'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT folder_id FROM sync_settings WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        return jsonify({'error': 'No sync folder configured'}), 400

    folder_id = row[0]
    thread = threading.Thread(target=sync_folder_worker, args=(folder_id, user_id))
    thread.daemon = True
    thread.start()

    return jsonify({'success': True, 'message': 'Sync started'})

@app.route('/api/sync/status', methods=['GET'])
def sync_status():
    return jsonify(sync_state)

@app.route('/api/tag-progress/stream')
@admin_required
def tag_progress_stream():
    def generate():
        q = queue_module.Queue(maxsize=50)
        with _sse_lock:
            _sse_queues.append(q)
        try:
            with _tag_progress_lock:
                data = dict(_tag_progress)
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
            with _sse_lock:
                if q in _sse_queues:
                    _sse_queues.remove(q)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )

@app.route('/api/tag-progress')
@admin_required
def tag_progress_snapshot():
    with _tag_progress_lock:
        data = dict(_tag_progress)
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
    with _tag_progress_lock:
        if _tag_progress['running']:
            return jsonify({'error': 'Tagging already in progress'}), 400

    if force:
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE images SET tagging_status = 'pending'")
        conn.commit()
        conn.close()

    trigger_tagging()
    return jsonify({'success': True, 'message': 'Tagging started', 'force': force})

@app.route('/api/interpret', methods=['POST'])
def interpret_nl():
    phrase = (request.get_json() or {}).get('phrase', '').strip()
    if not phrase:
        return jsonify({'error': 'No phrase provided'}), 400

    gemini_api_key = os.environ.get('GEMINI_API_KEY')
    if not gemini_api_key:
        return jsonify({'error': 'GEMINI_API_KEY not set'}), 500

    try:
        client = genai_client.Client(api_key=gemini_api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[NL_INTERPRET_PROMPT + phrase]
        )
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
    combined = tag_results + film_results
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

@app.route('/api/search')
def search():
    chips_raw = request.args.get('chips', '').strip()
    nl_raw = request.args.get('nl', '').strip()
    color_raw = request.args.get('color', '').strip()
    film_raw = request.args.get('film', '').strip()
    page = int(request.args.get('page', 0))
    per = int(request.args.get('per', 50))
    active_chips = [t.strip() for t in chips_raw.split(',') if t.strip()] if chips_raw else []

    # NL groups: JSON array of tag arrays. Image must match >=1 tag per group.
    nl_groups = []
    if nl_raw:
        try:
            parsed = json.loads(nl_raw)
            nl_groups = [[str(t) for t in g] for g in parsed if isinstance(g, list) and g]
        except Exception:
            nl_groups = []

    uid = session['user_id']
    conn = get_db()
    c = conn.cursor()

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

    if color_raw:
        # Small library — compute color matches in Python.
        # Palettes now hold 15 colors so subject colors (red dress in a blue
        # room) survive extraction. Search checks the top 6 with a tight
        # threshold: deep enough to catch the subject, tight enough that
        # "blue" doesn't return gray.
        threshold = 1000
        matched_ids = set()
        for row in c.execute('SELECT DISTINCT image_id, hex FROM colors WHERE rank <= 5').fetchall():
            try:
                if color_distance(color_raw, row['hex']) < threshold:
                    matched_ids.add(row['image_id'])
            except Exception:
                continue
        if matched_ids:
            cph = ','.join('?' * len(matched_ids))
            conditions.append(f'id IN ({cph})')
            params.extend(list(matched_ids))
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

    where = 'WHERE ' + ' AND '.join(conditions)

    rows = c.execute(f'''
        SELECT id, filename, thumbnail_blob, caption, aspect_ratio, {fav_flag_cols(uid)}
        FROM images {where}
        ORDER BY date_added DESC LIMIT ? OFFSET ?
    ''', params + [per, page * per]).fetchall()
    total = c.execute(f'SELECT COUNT(*) FROM images {where}', params).fetchone()[0]

    images_out = hydrate_image_rows(c, rows)
    conn.close()

    return jsonify({'images': images_out, 'total': total, 'page': page, 'per': per, 'has_more': (page + 1) * per < total})

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
        SELECT id, filename, thumbnail_blob, aspect_ratio, date_added, {fav_flag_cols(user_id)}
        FROM images WHERE user_id = ? ORDER BY date_added DESC
    ''', (user_id,))
    images = []
    for row in c.fetchall():
        thumb_b64 = base64.b64encode(row[2]).decode('utf-8')
        images.append({
            'id': row[0], 'filename': row[1],
            'thumbnail': f'data:image/jpeg;base64,{thumb_b64}',
            'aspect_ratio': row[3], 'date_added': row[4],
            'is_favorite': row[5], 'is_flagged': row[6]
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
        service = get_drive_service()
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
               i.id, i.filename, i.thumbnail_blob, i.caption, i.aspect_ratio,
               {fav_flag_cols(uid, alias='i')}
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
        img_dict = build_image_dict(
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

            sync_state['total'] = len(images)
            sync_state['processed'] = 0

            service = get_drive_service()
            for img in images:
                try:
                    sync_state['current_file'] = f"regenerating #{img['id']}"
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
                            save_palette(img['id'], img['user_id'], hexes)
                except Exception as e:
                    print(f"[regenerate] Failed {img['id']}: {e}")
                sync_state['processed'] += 1
            print("[regenerate] All thumbnails updated")
        finally:
            sync_state['in_progress'] = False

    if sync_state['in_progress']:
        return jsonify({'error': 'Sync already in progress'}), 400

    sync_state['in_progress'] = True
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
            save_palette(img['id'], img['user_id'], hexes)
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
    flow = get_oauth_flow(redirect_uri)
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
    creds = get_user_credentials(session['user_id'])
    if not creds:
        return jsonify({'error': 'not_signed_in'}), 401
    return jsonify({'access_token': creds.token})

@app.route('/api/auth/google/callback')
def google_callback():
    redirect_uri = request.url_root.rstrip('/') + '/api/auth/google/callback'
    flow = get_oauth_flow(redirect_uri)
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

@app.route('/api/upload', methods=['POST'])
@admin_required
def upload_images():
    # Uploads always go into the shared admin library (Stage 1 decision,
    # unchanged by Stage 2) — always user 1's own Google connection/folder,
    # regardless of who's calling (only admin can reach this route anyway).
    service = get_user_drive_service(1)
    if not service:
        return jsonify({'error': 'not_signed_in', 'message': 'Sign in with Google first.'}), 401

    force = request.args.get('force', '').lower() == 'true'
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No files provided'}), 400

    folder_id = get_root_folder_id(1)
    results = []

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, filename, thumbnail_blob, phash FROM images WHERE phash IS NOT NULL')
    existing = c.fetchall()
    conn.close()

    for f in files:
        image_data = f.read()
        filename = f.filename
        img_phash = compute_phash(image_data)

        if not force and img_phash:
            dup = next((r for r in existing
                        if phash_distance(img_phash, r['phash']) <= PHASH_NEAR_DUP_THRESHOLD), None)
            if dup:
                results.append({
                    'filename': filename,
                    'status': 'duplicate',
                    'existing': {
                        'id': dup['id'],
                        'filename': dup['filename'],
                        'thumbnail': f"data:image/jpeg;base64,{base64.b64encode(dup['thumbnail_blob']).decode('utf-8')}"
                    }
                })
                continue

        try:
            media = MediaIoBaseUpload(io.BytesIO(image_data), mimetype=f.mimetype or 'image/jpeg')
            drive_file = service.files().create(
                body={'name': filename, 'parents': [folder_id]},
                media_body=media, fields='id, md5Checksum'
            ).execute()
        except Exception as e:
            results.append({'filename': filename, 'status': 'error', 'message': str(e)})
            continue

        thumbnail = generate_thumbnail(image_data)
        aspect_ratio = get_image_aspect_ratio(image_data)

        conn = get_db()
        c = conn.cursor()
        c.execute('''
            INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status, md5_checksum, phash)
            VALUES (1, ?, ?, ?, ?, 'pending', ?, ?)
        ''', (drive_file['id'], filename, thumbnail, aspect_ratio, drive_file.get('md5Checksum'), img_phash))
        new_id = c.lastrowid
        conn.commit()
        conn.close()

        if thumbnail:
            hexes = extract_palette(thumbnail)
            if hexes:
                save_palette(new_id, 1, hexes)
            existing.append({'id': new_id, 'filename': filename, 'thumbnail_blob': thumbnail, 'phash': img_phash})

        results.append({'filename': filename, 'status': 'uploaded', 'image_id': new_id})

    if any(r['status'] == 'uploaded' for r in results):
        trigger_tagging()

    return jsonify({'results': results})

# ============================================================================
# DAY 8 (V7): IMAGE ACTIONS — favorite, flag, tags, download, delete
# ============================================================================

def _toggle_membership(table, user_id, image_id):
    """Shared on/off toggle for user_favorites/user_flags: insert if absent,
    delete if present. Returns the new state (True = now in the table)."""
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

@app.route('/api/images/<int:image_id>/flag', methods=['POST'])
def toggle_flag(image_id):
    result = _toggle_membership('user_flags', session['user_id'], image_id)
    if result is None:
        return jsonify({'error': 'Image not found'}), 404
    return jsonify({'success': True, 'is_flagged': result})

@app.route('/api/images/<int:image_id>/tags', methods=['POST', 'DELETE'])
@admin_required
def edit_tags(image_id):
    data = request.get_json(force=True) or {}
    # No category picked -> misc. Kept out of CAT_LABELS/CAT_COLORS on
    # purpose so it never shows up as a pickable option in the category
    # dropdown, but renders fine everywhere via the existing .get(x, x)
    # fallbacks (label becomes literally "misc", color a neutral gray).
    category = (data.get('category') or '').strip() or 'misc'
    value = (data.get('value') or '').strip().lower()
    if not value:
        return jsonify({'error': 'value is required'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT user_id FROM images WHERE id = ?', (image_id,))
    row = c.fetchone()
    if not row:
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

def _parse_bulk_tag_request(data):
    """Shared validation for the bulk-apply/bulk-remove endpoints. Returns
    (image_ids, category, value, error_response). error_response is None
    if validation passed."""
    image_ids = data.get('image_ids')
    # Blank category -> misc, same as the single-image tag editor.
    category = (data.get('category') or '').strip() or 'misc'
    value = (data.get('value') or '').strip().lower()

    if not isinstance(image_ids, list) or not image_ids or \
            not all(isinstance(i, int) for i in image_ids):
        return None, None, None, (jsonify({'error': 'image_ids must be a non-empty list of ints'}), 400)
    if category != 'misc' and category not in CAT_LABELS:
        return None, None, None, (jsonify({'error': 'invalid category'}), 400)
    if not value:
        return None, None, None, (jsonify({'error': 'value is required'}), 400)

    return image_ids, category, value, None

@app.route('/api/tags/bulk-apply', methods=['POST'])
@admin_required
def bulk_apply_tags():
    data = request.get_json(force=True) or {}
    image_ids, category, value, error = _parse_bulk_tag_request(data)
    if error:
        return error

    conn = get_db()
    c = conn.cursor()

    applied = 0
    already_had = 0
    invalid_ids = []

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
@admin_required
def bulk_remove_tags():
    data = request.get_json(force=True) or {}
    image_ids, category, value, error = _parse_bulk_tag_request(data)
    if error:
        return error

    conn = get_db()
    c = conn.cursor()
    placeholders = ','.join('?' * len(image_ids))
    c.execute(f'''
        DELETE FROM tags WHERE image_id IN ({placeholders}) AND category = ? AND value = ?
    ''', image_ids + [category, value])
    removed = c.rowcount
    conn.commit()
    conn.close()
    return jsonify({'removed': removed})

@app.route('/api/tags/selection-summary', methods=['POST'])
@admin_required
def tags_selection_summary():
    data = request.get_json(force=True) or {}
    image_ids = data.get('image_ids')
    if not isinstance(image_ids, list) or not image_ids or \
            not all(isinstance(i, int) for i in image_ids):
        return jsonify({'error': 'image_ids must be a non-empty list of ints'}), 400

    conn = get_db()
    c = conn.cursor()
    placeholders = ','.join('?' * len(image_ids))
    rows = c.execute(f'''
        SELECT category, value, COUNT(DISTINCT image_id) as cnt
        FROM tags
        WHERE image_id IN ({placeholders})
        GROUP BY category, value
        ORDER BY cnt DESC
    ''', image_ids).fetchall()

    # Filmography consensus: a field only counts as "common" when EVERY
    # selected image already agrees on the same non-empty value — missing
    # data on even one image breaks the consensus (so the bulk form doesn't
    # falsely imply a field's been verified across the whole selection).
    film_rows = c.execute(f'''
        SELECT image_id, title, director, dp, year FROM filmography
        WHERE image_id IN ({placeholders})
    ''', image_ids).fetchall()
    conn.close()

    film_by_image = {r['image_id']: r for r in film_rows}
    common_filmography = {}
    for field in ('title', 'director', 'dp', 'year'):
        values = {(film_by_image[iid][field] if iid in film_by_image else None) for iid in image_ids}
        only_value = next(iter(values)) if len(values) == 1 else None
        common_filmography[field] = only_value or None

    return jsonify({
        'total': len(image_ids),
        'tags': [{
            'category': row['category'],
            'value': row['value'],
            'catLabel': CAT_LABELS.get(row['category'], row['category']),
            'color': CAT_COLORS.get(row['category'], '#9c988d'),
            'count': row['cnt']
        } for row in rows],
        'common_filmography': common_filmography
    })

@app.route('/api/tags/suggestions', methods=['POST'])
@admin_required
def tags_suggestions():
    data = request.get_json(force=True) or {}
    image_ids = data.get('image_ids')
    if not isinstance(image_ids, list) or not image_ids or \
            not all(isinstance(i, int) for i in image_ids):
        return jsonify({'error': 'image_ids must be a non-empty list of ints'}), 400

    conn = get_db()
    c = conn.cursor()
    placeholders = ','.join('?' * len(image_ids))
    rows = c.execute(f'''
        SELECT category, value, COUNT(DISTINCT image_id) as cnt
        FROM tags
        WHERE image_id IN ({placeholders})
        GROUP BY category, value
        ORDER BY cnt DESC
    ''', image_ids).fetchall()

    total = len(image_ids)
    selection_tags = {(row['category'], row['value']): row['cnt'] for row in rows}

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
@admin_required
def update_filmography(image_id):
    """Set or clear the film info Gemini guessed for this image. Sending all
    empty fields clears it entirely."""
    data = request.get_json(force=True) or {}
    title = (data.get('title') or '').strip()
    director = (data.get('director') or '').strip()
    dp = (data.get('dp') or '').strip()
    year = str(data.get('year') or '').strip()

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT 1 FROM images WHERE id = ?', (image_id,))
    if not c.fetchone():
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

def _parse_bulk_image_ids(data):
    """Shared image_ids validation for the bulk filmography endpoints."""
    image_ids = data.get('image_ids')
    if not isinstance(image_ids, list) or not image_ids or \
            not all(isinstance(i, int) for i in image_ids):
        return None, (jsonify({'error': 'image_ids must be a non-empty list of ints'}), 400)
    return image_ids, None

@app.route('/api/filmography/bulk-set', methods=['POST'])
@admin_required
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
    valid_ids = [r[0] for r in c.execute(
        f"SELECT id FROM images WHERE id IN ({','.join('?' * len(image_ids))})", image_ids
    ).fetchall()]
    invalid_ids = [i for i in image_ids if i not in valid_ids]

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
@admin_required
def bulk_clear_filmography():
    """Wipes filmography from every selected image — for stills Gemini
    guessed a film on that isn't one at all."""
    data = request.get_json(force=True) or {}
    image_ids, error = _parse_bulk_image_ids(data)
    if error:
        return error

    conn = get_db()
    c = conn.cursor()
    valid_ids = [r[0] for r in c.execute(
        f"SELECT id FROM images WHERE id IN ({','.join('?' * len(image_ids))})", image_ids
    ).fetchall()]
    invalid_ids = [i for i in image_ids if i not in valid_ids]

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
        service = get_drive_service()
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
@admin_required
def delete_image(image_id):
    """Moves the Drive file into _Removed (recoverable), then removes the image
    and all its metadata from the library."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT drive_file_id, filename FROM images WHERE id = ?', (image_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Image not found'}), 404

    try:
        service = get_drive_service()
        file_id = row['drive_file_id']
        f = service.files().get(fileId=file_id, fields='parents').execute()
        prev_parents = ','.join(f.get('parents', []))
        removed_id = get_or_create_removed_folder(service, get_root_folder_id(1))
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
    for table in ('tags', 'colors', 'embeddings', 'deck_images', 'filmography'):
        c.execute(f'DELETE FROM {table} WHERE image_id = ?', (image_id,))
    c.execute('DELETE FROM images WHERE id = ?', (image_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'moved_to': REMOVED_FOLDER_NAME, 'filename': row['filename']})

# ============================================================================
# DAY 8 (V7): DUPLICATE DETECTION
# ============================================================================

@app.route('/api/duplicates/scan', methods=['POST'])
@admin_required
def duplicates_scan():
    """Backfills fingerprints for any image missing them, then returns
    duplicate groups. phash comes from stored thumbnails (instant); md5 comes
    from one Drive folder listing (a few seconds)."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, thumbnail_blob FROM images WHERE phash IS NULL')
    for r in c.fetchall():
        ph = compute_phash(r['thumbnail_blob'])
        if ph:
            c.execute('UPDATE images SET phash = ? WHERE id = ?', (ph, r['id']))
    conn.commit()
    c.execute('SELECT COUNT(*) FROM images WHERE md5_checksum IS NULL')
    missing_md5 = c.fetchone()[0]
    conn.close()

    if missing_md5:
        try:
            service = get_drive_service()
            files = list_images_in_folder(service, get_root_folder_id(1))
            md5_map = {f['id']: f.get('md5Checksum') for f in files}
            conn = get_db()
            c = conn.cursor()
            c.execute('SELECT id, drive_file_id FROM images WHERE md5_checksum IS NULL')
            for r in c.fetchall():
                m = md5_map.get(r['drive_file_id'])
                if m:
                    c.execute('UPDATE images SET md5_checksum = ? WHERE id = ?', (m, r['id']))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[duplicates] md5 backfill failed: {e}")

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

    exact_pairs = set()
    for i in range(n):
        for j in range(i + 1, n):
            a, b = rows[i], rows[j]
            if a['md5_checksum'] and a['md5_checksum'] == b['md5_checksum']:
                parent[find(i)] = find(j)
                exact_pairs.add((i, j))
            elif (a['phash'] and b['phash']
                  and phash_distance(a['phash'], b['phash']) <= PHASH_NEAR_DUP_THRESHOLD):
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

def _fetch_image_dict(c, image_id, owner_user_id):
    """Loads one images row plus its tags/palette/filmography and runs it
    through build_image_dict(). Used by the decks endpoints, which need the
    same image JSON shape as /api/search and /api/images/<id>/similar but
    are fetching images one at a time (via deck_images), not in bulk.
    is_favorite/is_flagged reflect the deck OWNER (not the viewer — the public
    share view has no logged-in viewer at all)."""
    row = c.execute(f'''
        SELECT id, filename, thumbnail_blob, caption, aspect_ratio, {fav_flag_cols(owner_user_id)}
        FROM images WHERE id = ?
    ''', (image_id,)).fetchone()
    if not row:
        return None

    tags = [
        {'category': tr['category'], 'value': tr['value']}
        for tr in c.execute('SELECT category, value FROM tags WHERE image_id = ?', (image_id,)).fetchall()
    ]
    palette = [
        cr['hex'] for cr in
        c.execute('SELECT hex FROM colors WHERE image_id = ? ORDER BY rank ASC', (image_id,)).fetchall()
    ]
    fr = c.execute(
        'SELECT title, director, dp, year FROM filmography WHERE image_id = ?', (image_id,)
    ).fetchone()
    filmography = {'title': fr['title'], 'director': fr['director'], 'dp': fr['dp'], 'year': fr['year']} if fr else None

    return build_image_dict(row, tags, palette, filmography)

@app.route('/api/decks', methods=['GET'])
def list_decks():
    conn = get_db()
    c = conn.cursor()
    deck_rows = c.execute(
        'SELECT id, name, created_at FROM decks WHERE user_id = ? ORDER BY created_at DESC',
        (session['user_id'],)
    ).fetchall()

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

        decks_out.append({
            'id': d['id'],
            'name': d['name'],
            'created_at': d['created_at'],
            'image_count': image_count,
            'preview_thumbnails': preview_thumbnails
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
    c.execute('INSERT INTO decks (user_id, name) VALUES (?, ?)', (session['user_id'], name))
    deck_id = c.lastrowid
    created_at = c.execute('SELECT created_at FROM decks WHERE id = ?', (deck_id,)).fetchone()['created_at']
    conn.commit()
    conn.close()

    return jsonify({
        'id': deck_id,
        'name': name,
        'created_at': created_at,
        'image_count': 0,
        'preview_thumbnails': []
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

    c.execute('DELETE FROM deck_images WHERE deck_id = ?', (deck_id,))
    c.execute('DELETE FROM scenes WHERE deck_id = ?', (deck_id,))
    c.execute('DELETE FROM decks WHERE id = ?', (deck_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/decks/<int:deck_id>', methods=['GET'])
def get_deck(deck_id):
    conn = get_db()
    c = conn.cursor()
    deck_row = c.execute(
        'SELECT id, name, created_at, share_token, user_id FROM decks WHERE id = ? AND user_id = ?',
        (deck_id, session['user_id'])
    ).fetchone()
    if not deck_row:
        conn.close()
        return jsonify({'error': 'Deck not found'}), 404

    payload = _deck_payload(c, deck_row)
    conn.close()
    return jsonify(payload)

def _deck_payload(c, deck_row):
    """Full deck JSON: deck info + ordered scenes + flat image list. Shared by
    the owner view (GET /api/decks/<id>) and the public share view
    (GET /api/share/<token>) so the two can never drift apart. Images come back
    in storyboard order (unordered rows last, then by row id) — the frontend
    preserves this order when it groups images into scene sections."""
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
        img_dict = _fetch_image_dict(c, di['image_id'], deck_row['user_id'])
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
        'share_token': deck_row['share_token'],
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
    conn.commit()
    conn.close()

    return jsonify({'id': scene_id, 'name': name, 'sort_order': next_order, 'deck_id': deck_id})

@app.route('/api/scenes/<int:scene_id>', methods=['PATCH'])
def update_scene(scene_id):
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()

    conn = get_db()
    c = conn.cursor()
    if not c.execute(
        'SELECT 1 FROM scenes s JOIN decks d ON d.id = s.deck_id WHERE s.id = ? AND d.user_id = ?',
        (scene_id, session['user_id'])
    ).fetchone():
        conn.close()
        return jsonify({'error': 'Scene not found'}), 404
    if not name:
        conn.close()
        return jsonify({'error': 'name is required'}), 400

    c.execute('UPDATE scenes SET name = ? WHERE id = ?', (name, scene_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/scenes/<int:scene_id>', methods=['DELETE'])
def delete_scene(scene_id):
    conn = get_db()
    c = conn.cursor()
    if not c.execute(
        'SELECT 1 FROM scenes s JOIN decks d ON d.id = s.deck_id WHERE s.id = ? AND d.user_id = ?',
        (scene_id, session['user_id'])
    ).fetchone():
        conn.close()
        return jsonify({'error': 'Scene not found'}), 404

    c.execute('DELETE FROM deck_images WHERE scene_id = ?', (scene_id,))
    c.execute('DELETE FROM scenes WHERE id = ?', (scene_id,))
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
        conn.commit()
        conn.close()
        return jsonify({'action': 'moved'})

    if current_scene_id is None:
        # Moving out of Unsorted into a named scene: simple move.
        c.execute('UPDATE deck_images SET scene_id = ? WHERE id = ?', (target_scene_id, deck_image_id))
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
    conn.commit()
    conn.close()
    return jsonify({'action': 'copied', 'new_deck_image_id': new_deck_image_id})

@app.route('/api/deck-images/<int:deck_image_id>', methods=['DELETE'])
def delete_deck_image(deck_image_id):
    conn = get_db()
    c = conn.cursor()
    if not c.execute('''
        SELECT 1 FROM deck_images di JOIN decks d ON d.id = di.deck_id
        WHERE di.id = ? AND d.user_id = ?
    ''', (deck_image_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'deck image not found'}), 404

    c.execute('DELETE FROM deck_images WHERE id = ?', (deck_image_id,))
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
    if not c.execute('''
        SELECT 1 FROM deck_images di JOIN decks d ON d.id = di.deck_id
        WHERE di.id = ? AND d.user_id = ?
    ''', (deck_image_id, session['user_id'])).fetchone():
        conn.close()
        return jsonify({'error': 'deck image not found'}), 404

    c.execute('UPDATE deck_images SET storyboard_note = ? WHERE id = ?', (note, deck_image_id))
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
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'updated': len(ordered_ids)})

@app.route('/api/decks/<int:deck_id>/share', methods=['POST', 'DELETE'])
def deck_share_token(deck_id):
    """POST creates (or returns the existing) share token for a deck.
    DELETE revokes it — the old link stops working immediately, and a later
    POST mints a brand new token rather than reviving the old one."""
    conn = get_db()
    c = conn.cursor()
    row = c.execute(
        'SELECT share_token FROM decks WHERE id = ? AND user_id = ?', (deck_id, session['user_id'])
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
        c.execute('UPDATE decks SET share_token = ? WHERE id = ?', (token, deck_id))
        conn.commit()
    conn.close()
    return jsonify({'share_token': token, 'share_path': f'/share/{token}'})

@app.route('/api/share/<token>')
def get_shared_deck(token):
    """Public read-only deck view — no login, the token IS the access grant.
    Viewers get thumbnails only (they're embedded in the payload as data URIs);
    none of the full-res, edit, or delete endpoints check tokens, so a shared
    link exposes nothing beyond what this one endpoint returns."""
    conn = get_db()
    c = conn.cursor()
    deck_row = c.execute(
        'SELECT id, name, created_at, share_token, user_id FROM decks WHERE share_token = ?', (token,)
    ).fetchone()
    if not deck_row:
        conn.close()
        return jsonify({'error': 'Share link not found or revoked'}), 404

    payload = _deck_payload(c, deck_row)
    conn.close()
    return jsonify(payload)

# ============================================================================
# DAY 13 (V12): ANALYTICS + UTILITY VIEWS
# ============================================================================

@app.route('/api/views/<view>')
def get_utility_view(view):
    """Filtered image lists for the Day 13 utility views.

    /api/views/favorites          — all starred images
    /api/views/flagged            — all flagged images
    /api/views/recent?days=7      — images added in the last N days
                                    (?limit=30 caps how many come back)

    Returns the same full image dicts as /api/search, so the frontend can
    reuse the grid + detail panel unchanged.
    """
    uid = session['user_id']

    if view == 'favorites':
        where, params = 'user_id = ? AND id IN (SELECT image_id FROM user_favorites WHERE user_id = ?)', [uid, uid]
    elif view == 'flagged':
        where, params = 'user_id = ? AND id IN (SELECT image_id FROM user_flags WHERE user_id = ?)', [uid, uid]
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
        SELECT id, filename, thumbnail_blob, caption, aspect_ratio, {fav_flag_cols(uid)}
        FROM images WHERE {where}
        ORDER BY date_added DESC {limit_sql}
    ''', params + limit_params).fetchall()
    total = c.execute(f'SELECT COUNT(*) FROM images WHERE {where}', params).fetchone()[0]

    images_out = hydrate_image_rows(c, rows)
    conn.close()
    return jsonify({'images': images_out, 'total': total})

@app.route('/api/flags/clear-all', methods=['POST'])
def clear_all_flags():
    """Unflag every image this user has flagged. Only ever removes the flag
    marker — no image is ever deleted by this."""
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM user_flags WHERE user_id = ?', (session['user_id'],))
    cleared = c.rowcount
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'cleared': cleared})

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
        'flagged': c.execute('SELECT COUNT(*) FROM user_flags WHERE user_id = ?', (uid,)).fetchone()[0],
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
    init_db()
    load_embeddings_seed()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

init_db()
load_embeddings_seed()

import os
import json
import base64
import secrets
import io
import gzip
import re
import sqlite3
import time
import zlib
import threading
import queue as queue_module
from array import array
from datetime import datetime, timedelta
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

def clear_ai_tags(cursor, image_id):
    """Delete an image's AI-written tags ahead of a re-tag, preserving every
    manually-applied category (see MANUAL_TAG_CATEGORIES)."""
    ph = ','.join('?' * len(MANUAL_TAG_CATEGORIES))
    cursor.execute(
        f"DELETE FROM tags WHERE image_id = ? AND category NOT IN ({ph})",
        (image_id, *MANUAL_TAG_CATEGORIES))

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
    "camera_format": [],
    "subjects": []
  },
  "filmography": {
    "title": null,
    "director": null,
    "dp": null,
    "year": null
  }
}

For cinematography tags, ONLY use tags from these allowed lists.
For subjects, identify any visible objects, people, animals, or elements in the frame — be specific and comprehensive (subjects are open-ended, not restricted to a list).

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
subjects: any objects, people, animals, or elements visible in the frame (e.g. man, woman, child, dog, cat, fish, horse, mountain, building, tree, water, fire, etc.)

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
subjects: man, woman, child, couple, wedding, hand, hands, body, face, animal, dog, cat, horse, bird, building, house, car, door, window, street, bridge, fire, water, mirror, glass, weapon, crowd, performance

Pick the 2-5 tags that best capture the FEELING and VISUAL QUALITIES of the phrase.
Return ONLY a JSON array of tag strings, e.g. ["lonely","low-key","night"]. No markdown, no explanation.

Phrase: """

# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def _shuffle_key(seed, image_id):
    # Deterministic pseudo-random sort key: the same (seed, image) pair always
    # produces the same number, so page 2 of a shuffled feed continues exactly
    # where page 1 left off. A new seed produces a completely different order.
    # crc32 (unlike Python's hash()) gives identical results across restarts.
    return zlib.crc32(f'{seed}:{image_id}'.encode())

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.create_function('shuffle_key', 2, _shuffle_key)
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
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
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

    # V14: which images each user has actually scrolled past, and when.
    # The shuffled home feed uses last_seen_at to demote images seen in the
    # last 7 days so fresh inspiration surfaces first.
    c.execute('''
        CREATE TABLE IF NOT EXISTS image_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            image_id INTEGER NOT NULL,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            seen_count INTEGER DEFAULT 1,
            UNIQUE(user_id, image_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')

    # Per-user Gemini spend: one running-total row per (user, calendar month),
    # updated in place every time that user's key gets a response back
    # (tagging or NL search). Powers the "Gemini spend" number on Settings.
    c.execute('''
        CREATE TABLE IF NOT EXISTS gemini_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            month TEXT NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            UNIQUE(user_id, month),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # V17: Drive files a friend deleted from their library. We can't move
    # files in a folder we only have Viewer access to, so sync skips these
    # instead — otherwise every deleted image would return on the next sync.
    c.execute('''
        CREATE TABLE IF NOT EXISTS sync_exclusions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            drive_file_id TEXT NOT NULL,
            UNIQUE(user_id, drive_file_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # V18: view-only crew members on a deck (distinct from the anonymous,
    # loginless /share/<token> link — a deck_members row is a real account
    # with permanent access, tracked so the owner can see and revoke it).
    c.execute('''
        CREATE TABLE IF NOT EXISTS deck_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(deck_id, user_id),
            FOREIGN KEY (deck_id) REFERENCES decks(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # V18: activity feed for a deck (who did what, when). Only the owner can
    # write to a deck, so user_id here is always the owner — except for the
    # 'invited'/'joined' rows, which record the two sides of a member joining.
    c.execute('''
        CREATE TABLE IF NOT EXISTS deck_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (deck_id) REFERENCES decks(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
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

    # V18: reusable "join this deck as a viewer" link, separate from the
    # anonymous share_token — accepting it requires login and creates a
    # deck_members row.
    try:
        c.execute("ALTER TABLE decks ADD COLUMN invite_token TEXT")
        conn.commit()
        print("[migration] Added invite_token column to decks")
    except Exception:
        pass

    # V19: last login timestamp, powers the admin per-user analytics view.
    try:
        c.execute("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP")
        conn.commit()
        print("[migration] Added last_login_at column to users")
    except Exception:
        pass

    # V23: crew collaboration — permission levels on deck_members (viewer/editor)
    try:
        c.execute("ALTER TABLE deck_members ADD COLUMN permission TEXT DEFAULT 'viewer'")
        conn.commit()
        print("[migration] Added permission column to deck_members")
    except Exception:
        pass

    # V23: track when a deck was last modified for the "new changes" banner
    try:
        c.execute("ALTER TABLE decks ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        conn.commit()
        print("[migration] Added updated_at column to decks")
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
    '/api/auth/forgot-password',
    '/api/auth/reset-password',
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

def check_deck_permission(deck_id, user_id, required_permission='editor'):
    """Check if a user has permission to edit a deck.
    Returns (has_permission: bool, is_owner: bool)"""
    conn = get_db()
    c = conn.cursor()

    # Owner bypass
    owner = c.execute('SELECT user_id FROM decks WHERE id = ?', (deck_id,)).fetchone()
    if owner and owner['user_id'] == user_id:
        conn.close()
        return True, True

    # Check collaborator permission
    perm = c.execute(
        'SELECT permission FROM deck_members WHERE deck_id = ? AND user_id = ?',
        (deck_id, user_id)
    ).fetchone()
    conn.close()

    if not perm:
        return False, False

    if required_permission == 'viewer':
        return perm['permission'] in ('viewer', 'editor'), False
    elif required_permission == 'editor':
        return perm['permission'] == 'editor', False
    return False, False

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

    conn = get_db()
    conn.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (row['id'],))
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
# GEMINI KEYS & USAGE
# ============================================================================

def get_user_gemini_key(user_id):
    """Admin (user 1) rides the shared Railway env key. Everyone else must
    have saved their own key in Account settings — a friend's AI tagging and
    NL search run on their own key/budget, never the admin's."""
    if user_id == 1:
        return os.environ.get('GEMINI_API_KEY')
    conn = get_db()
    c = conn.cursor()
    row = c.execute('SELECT gemini_api_key FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return row['gemini_api_key'] if row and row['gemini_api_key'] else None

def record_gemini_usage(user_id, usage_metadata, model_name=None):
    """Adds one API response's token counts to this user's running total for
    the current calendar month, so Settings can show an estimated spend."""
    if not usage_metadata:
        return
    pricing = get_model_pricing(model_name or GEMINI_MODEL)
    input_tokens = getattr(usage_metadata, 'prompt_token_count', 0) or 0
    output_tokens = getattr(usage_metadata, 'candidates_token_count', None)
    if output_tokens is None:
        output_tokens = getattr(usage_metadata, 'response_token_count', 0) or 0
    cost = (input_tokens / 1_000_000) * pricing['input'] + (output_tokens / 1_000_000) * pricing['output']

    month = datetime.utcnow().strftime('%Y-%m')
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO gemini_usage (user_id, month, input_tokens, output_tokens, cost_usd)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, month) DO UPDATE SET
            input_tokens = input_tokens + excluded.input_tokens,
            output_tokens = output_tokens + excluded.output_tokens,
            cost_usd = cost_usd + excluded.cost_usd
    ''', (user_id, month, input_tokens, output_tokens, cost))
    conn.commit()
    conn.close()

# ============================================================================
# TAGGING WORKER
# ============================================================================

def _run_tagging_job_inner(user_id=None):
    """user_id=None tags every pending/failed image across every owner (the
    admin's global 'tag now' / post-sync trigger). A specific user_id scopes
    the run to just that person's own library (friend's 'Tag my photos').
    Either way, each image is tagged with ITS OWNER's key — owners who
    haven't saved a key are skipped, their photos left untagged but
    searchable, at zero cost to anyone."""
    conn = get_db()
    c = conn.cursor()
    query = """
        SELECT id, user_id, thumbnail_blob, filename
        FROM images
        WHERE tagging_status != 'done'
        {owner_filter}
        ORDER BY
            CASE tagging_status
                WHEN 'pending' THEN 0
                WHEN 'failed'  THEN 1
                ELSE 2
            END,
            id ASC
    """
    if user_id is not None:
        rows = c.execute(query.format(owner_filter='AND user_id = ?'), (user_id,)).fetchall()
    else:
        rows = c.execute(query.format(owner_filter='')).fetchall()
    conn.close()

    clients = {}
    images = []
    for row in rows:
        owner_id = row['user_id']
        if owner_id not in clients:
            key = get_user_gemini_key(owner_id)
            clients[owner_id] = genai_client.Client(api_key=key) if key else None
        if clients[owner_id] is not None:
            images.append(row)

    if not images:
        with _tag_progress_lock:
            _tag_progress.update({
                'running': False, 'status': 'error',
                'message': 'No Gemini API key available for the queued photos.'
            })
        _broadcast_progress()
        return

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
        owner_id = img['user_id']
        thumb_blob = img['thumbnail_blob']
        filename = img['filename']
        client = clients[owner_id]

        try:
            pil_img = Image.open(io.BytesIO(thumb_blob))

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[GEMINI_TAGGING_PROMPT, pil_img]
            )
            record_gemini_usage(owner_id, getattr(response, 'usage_metadata', None))
            raw = response.text.strip()

            if raw.startswith('```'):
                raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()

            data = json.loads(raw)

            conn = get_db()
            c = conn.cursor()

            # V15: replace only the AI's own tags. Manual categories (My Work,
            # misc) are human decisions — a re-tag must never erase them.
            clear_ai_tags(c, img_id)
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
                            "INSERT INTO tags (image_id, user_id, category, value) VALUES (?, ?, ?, ?)",
                            (img_id, owner_id, category, val.strip().lower())
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


def _run_tagging_job(user_id=None):
    try:
        _run_tagging_job_inner(user_id=user_id)
    except Exception as e:
        print(f"[tagging] Job failed: {e}")
        with _tag_progress_lock:
            _tag_progress.update({'running': False, 'status': 'error', 'message': str(e)})
        _broadcast_progress()


def trigger_tagging(user_id=None):
    with _tag_progress_lock:
        if _tag_progress['running']:
            return
    t = threading.Thread(target=_run_tagging_job, kwargs={'user_id': user_id}, daemon=True)
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

# V17: personal libraries. Friends share their Drive folder with the service
# account's email and paste the folder link — no extra Google permissions,
# no unverified-app warning screens, no 7-day token expiry.
PERSONAL_LIBRARY_CAP = 1000  # max images per non-admin library (soft cap)

def get_service_account_email():
    """The service account's email — what friends paste into Drive's Share
    box so Frame Atlas can read their folder."""
    creds_json = os.environ.get('GOOGLE_DRIVE_CREDENTIALS')
    if not creds_json:
        return None
    try:
        return json.loads(creds_json).get('client_email')
    except Exception:
        return None

def parse_drive_folder_id(text):
    """Pull a folder ID out of whatever the user pasted — a full Drive URL
    (https://drive.google.com/drive/folders/<id>?usp=sharing, /drive/u/0/
    variants, ?id= form) or the bare ID itself. Returns None if nothing
    ID-shaped is found."""
    text = (text or '').strip()
    if not text:
        return None
    m = re.search(r'/folders/([A-Za-z0-9_-]+)', text)
    if m:
        return m.group(1)
    m = re.search(r'[?&]id=([A-Za-z0-9_-]+)', text)
    if m:
        return m.group(1)
    # Bare ID: Drive IDs are long unbroken strings of URL-safe characters
    if re.fullmatch(r'[A-Za-z0-9_-]{15,}', text):
        return text
    return None

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

def ar_float_from_str(ar_str):
    """Parse a stored aspect_ratio string ("80:43", "1.85:1", "2") to a float.
    Single source of truth for this parsing — build_image_dict (display) and
    /api/search's ar filter (V15) must always agree on what bucket an image
    falls in, or search results wouldn't match the labels shown on tiles."""
    ar_str = ar_str or '16:9'
    try:
        w, h = ar_str.split(':', 1) if ':' in ar_str else (ar_str, '1')
        return float(w) / float(h)
    except Exception:
        return 16 / 9

# V15: plain-English ways a cinematographer might type a format into search
AR_QUERY_ALIASES = {
    'scope': '2.39:1', 'anamorphic': '2.39:1', 'cinemascope': '2.39:1',
    'flat': '1.85:1', 'vertical': '9:16', 'portrait': '9:16',
    'square': '1:1', 'widescreen': '16:9',
}

def ar_query_labels(q):
    """Which standard aspect-ratio buckets does a search query point at?
    Pure string logic, no database. Three ways to match:
      - alias words: "scope" / "vertical" / "square" ...
      - a typed ratio or decimal ("2.35", "2.35:1", "9:16", "16x9") snaps to
        the nearest standard bucket — so 2.35 and 2.39 both find Scope.
        Bare integers are excluded here ("9" shouldn't mean "9.0:1 wide").
      - substring on the label itself: "16" hits both 16:9 and 9:16.
    """
    labels = []
    alias = AR_QUERY_ALIASES.get(q)
    if alias:
        labels.append(alias)
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*[:/x]\s*(\d+(?:\.\d+)?)|(\d+\.\d+)', q)
    if m:
        try:
            num = float(m.group(1) or m.group(3))
            den = float(m.group(2)) if m.group(2) else 1.0
            if num > 0 and den > 0:
                labels.append(normalize_ar_label(num / den))
        except (ValueError, ZeroDivisionError):
            pass
    for label, _ in STANDARD_ASPECT_RATIOS:
        if q in label:
            labels.append(label)
    return list(dict.fromkeys(labels))  # dedupe, keep order

def build_image_dict(row, tags, palette, filmography):
    """Turns one `images` row (must include id, filename, thumbnail_blob,
    caption, aspect_ratio, is_favorite, is_flagged) into the JSON shape used
    by both /api/search and /api/images/<id>/similar. Keep these two routes
    using this single helper so their image objects can never drift apart."""
    ar_str = row['aspect_ratio'] or '16:9'
    ar_float = ar_float_from_str(ar_str)

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

        # EVERYONE syncs through the shared service account (V17). Friends
        # share their folder with the service account's email (same as Ryan
        # did on Day 2) — their own Google sign-in stays drive.file-scoped,
        # which can only see files the app itself created, so it could never
        # read a pre-existing folder. Verified against Google's docs before
        # abandoning the OAuth read path: picking a folder in the Google
        # Picker grants access to the folder itself, NOT the files inside it.
        service = get_drive_service()
        print(f"Listing images in folder {folder_id}...")
        try:
            all_images = list_images_in_folder(service, folder_id)
        except Exception as e:
            msg = str(e)
            if '404' in msg or 'notFound' in msg or '403' in msg or 'insufficient' in msg.lower():
                sync_state['errors'].append(
                    'Frame Atlas can\'t see that folder — make sure it\'s shared with '
                    f'{get_service_account_email() or "the Frame Atlas robot email"} (Share → Viewer), then try again.')
                return
            raise
        sync_state['total'] = len(all_images)

        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT drive_file_id FROM images WHERE user_id = ?', (user_id,))
        existing_ids = set(row[0] for row in c.fetchall())
        library_count = len(existing_ids)
        # Files this user deleted from their library — never re-import (V17)
        c.execute('SELECT drive_file_id FROM sync_exclusions WHERE user_id = ?', (user_id,))
        existing_ids |= set(row[0] for row in c.fetchall())
        conn.close()

        new_count = 0
        for image in all_images:
            # Soft cap (V17): friends' thumbnails live in the shared database,
            # so one giant folder can't balloon storage. Admin is exempt.
            if user_id != 1 and library_count + new_count >= PERSONAL_LIBRARY_CAP:
                sync_state['errors'].append(
                    f'Stopped at the {PERSONAL_LIBRARY_CAP}-image limit — the rest of the '
                    'folder wasn\'t synced. Ask Ryan if you need more room.')
                break
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
        # Auto-tagging after sync: admin rides the shared key (Day 5). A
        # friend's photos auto-tag too, but ONLY if they've saved their own
        # Gemini key (V16) — scoped to just their images, on their key, so
        # it can never spend the admin's budget. Keyless friends' photos sit
        # untagged (searchable by filename, zero cost) until they add one.
        if user_id == 1:
            trigger_tagging()
        elif get_user_gemini_key(user_id):
            trigger_tagging(user_id=user_id)

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
    folder_id = parse_drive_folder_id(data.get('folder', ''))
    robot = get_service_account_email() or 'the Frame Atlas robot email'

    if not folder_id:
        return jsonify({'error': "That doesn't look like a Drive folder link — open the folder "
                                 'in Google Drive and copy the address from the browser bar.'}), 400

    try:
        service = get_drive_service()
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
        image_count = len(list_images_in_folder(service, folder_id))
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
        'service_account_email': get_service_account_email(),
        'folder_connected': bool(folder and folder['folder_id']),
        'folder_name': folder['folder_name'] if folder else None,
        'last_sync': folder['last_sync'] if folder else None,
        'image_count': image_count,
        'image_cap': None if user_id == 1 else PERSONAL_LIBRARY_CAP,
        'has_gemini_key': user_id == 1 or bool(key_row and key_row['gemini_api_key']),
    })

@app.route('/api/sync/start', methods=['POST'])
def start_sync():
    user_id = session['user_id']

    if sync_state['in_progress']:
        return jsonify({'error': 'Sync already in progress', 'user_id': sync_state['user_id']}), 400

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
    # One sync runs at a time app-wide. Only the person whose sync it is
    # (or the admin) sees filenames/errors — another user just learns the
    # slot is busy, not what's in someone else's Drive folder. (V17)
    uid = session['user_id']
    if sync_state['user_id'] in (None, uid) or uid == 1:
        return jsonify({**sync_state, 'yours': sync_state['user_id'] in (None, uid)})
    return jsonify({'in_progress': sync_state['in_progress'], 'yours': False,
                    'processed': 0, 'total': 0, 'current_file': '', 'errors': []})

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
        c.execute('UPDATE users SET gemini_api_key = ? WHERE id = ?', (key, uid))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'has_key': True, 'key_last4': key[-4:]})

    row = c.execute('SELECT gemini_api_key FROM users WHERE id = ?', (uid,)).fetchone()
    conn.close()
    key = row['gemini_api_key'] if row else None
    return jsonify({'has_key': bool(key), 'key_last4': key[-4:] if key else None})

@app.route('/api/tag/mine', methods=['POST'])
def tag_mine():
    """A friend's own 'Tag my photos' trigger — scoped to just their library,
    always using their own saved key (never the admin's)."""
    uid = current_user_id()
    if uid == 1:
        return jsonify({'error': 'Admin tagging runs automatically after sync.'}), 400

    if not get_user_gemini_key(uid):
        return jsonify({'error': 'Add your Gemini API key in Account settings first.'}), 400

    with _tag_progress_lock:
        if _tag_progress['running']:
            return jsonify({'error': 'Tagging already in progress'}), 400

    trigger_tagging(user_id=uid)
    return jsonify({'success': True, 'message': 'Tagging started'})

@app.route('/api/tag-progress/mine')
def tag_progress_mine():
    """Same shape as the admin-only /api/tag-progress, but scoped so a friend
    can poll their own 'Tag my photos' run without the admin_required gate."""
    uid = current_user_id()
    with _tag_progress_lock:
        data = dict(_tag_progress)
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
    if not get_user_gemini_key(uid):
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
    gemini_api_key = get_user_gemini_key(uid)
    if not gemini_api_key:
        return jsonify({'error': 'Add your Gemini API key in Account settings to use natural-language search.'}), 400

    try:
        client = genai_client.Client(api_key=gemini_api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[NL_INTERPRET_PROMPT + phrase]
        )
        record_gemini_usage(uid, getattr(response, 'usage_metadata', None))
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
    combined = tag_results + film_results + ar_results
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
    ar_raw = request.args.get('ar', '').strip()  # V15: aspect-ratio bucket, e.g. "2.39:1"
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

    where = 'WHERE ' + ' AND '.join(conditions)

    # V14: shuffled home feed. When the default (unfiltered) grid sends a seed,
    # order by a seeded shuffle instead of newest-first. Images the user has
    # scrolled past in the last 7 days sink below ones they haven't seen lately,
    # so each visit leads with fresher inspiration. Any active filter switches
    # back to the normal newest-first ordering.
    seed = request.args.get('seed', '').strip()
    is_unfiltered = not (active_chips or nl_groups or color_raw or film_raw or ar_raw)
    if seed and is_unfiltered:
        order_by = '''CASE WHEN EXISTS(
                SELECT 1 FROM image_views iv
                WHERE iv.user_id = ? AND iv.image_id = images.id
                  AND iv.last_seen_at > datetime('now', '-7 days')
            ) THEN 1 ELSE 0 END,
            shuffle_key(?, images.id)'''
        order_params = [uid, seed]
    else:
        order_by = 'date_added DESC'
        order_params = []

    rows = c.execute(f'''
        SELECT id, filename, thumbnail_blob, caption, aspect_ratio, {fav_flag_cols(uid)}
        FROM images {where}
        ORDER BY {order_by} LIMIT ? OFFSET ?
    ''', params + order_params + [per, page * per]).fetchall()
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

@app.route('/api/auth/google/disconnect', methods=['POST'])
def google_disconnect():
    """Clear the user's Google OAuth token. Re-authenticates on next upload attempt."""
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE users SET google_oauth_token = NULL WHERE id = ?', (session['user_id'],))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/upload', methods=['POST'])
@admin_required
def upload_images():
    # Uploads always go into the shared admin library (Stage 1 decision,
    # unchanged by Stage 2) — always user 1's own Google connection/folder,
    # regardless of who's calling (only admin can reach this route anyway).
    try:
        service = get_user_drive_service(1)
    except Exception as e:
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
                    'moved_to': REMOVED_FOLDER_NAME if user_id == 1 else None,
                    'filename': row['filename']})

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
        'SELECT id, name, created_at, share_token, invite_token, user_id FROM decks WHERE id = ?', (deck_id,)
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

def log_deck_activity(c, deck_id, action, detail=None):
    """Appends one row to the deck's activity feed, attributed to whoever's
    logged in right now. Only the deck owner can call the write endpoints
    this is hooked into, so `action` almost always describes an owner edit —
    the exception is 'invited'/'joined', which fire for the two sides of a
    member joining."""
    c.execute(
        'INSERT INTO deck_activity (deck_id, user_id, action, detail) VALUES (?, ?, ?, ?)',
        (deck_id, session['user_id'], action, detail)
    )

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
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'updated': len(ordered_ids)})

@app.route('/api/decks/<int:deck_id>/share', methods=['POST', 'DELETE'])
def deck_share_token(deck_id):
    """POST creates (or returns the existing) share token for a deck with permission level.
    DELETE revokes it — the old link stops working immediately, and a later
    POST mints a brand new token rather than reviving the old one.
    Accepts ?permission=viewer|editor (default: viewer)"""
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

    permission = request.args.get('permission', 'viewer')
    if permission not in ('viewer', 'editor'):
        conn.close()
        return jsonify({'error': 'permission must be viewer or editor'}), 400

    token = row['share_token']
    if not token:
        token = secrets.token_urlsafe(16)
        c.execute('UPDATE decks SET share_token = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', (token, deck_id))
        conn.commit()
    conn.close()
    return jsonify({'share_token': token, 'share_path': f'/share/{token}', 'permission': permission})

@app.route('/api/decks/join/<token>', methods=['POST'])
def join_deck_via_link(token):
    """Join a deck via its public share link. Must be logged in.
    Creates a deck_members row with the permission level specified when the link was created."""
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

    # Default to viewer for public links (permission was stored in the token generation, but we
    # store the permission level per-member, so for now default to viewer for public join)
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
            'image_cap': None if uid == 1 else PERSONAL_LIBRARY_CAP,
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
    init_db()
    load_embeddings_seed()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

init_db()
load_embeddings_seed()

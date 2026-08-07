import os
import json
import base64
import secrets
import io
import gzip
import math
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
from PIL import Image, ImageOps
import google.auth.transport.requests
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from googleapiclient.errors import HttpError
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

# V27: Background crop job queue. Multiple users can have crops running in
# parallel (each in its own thread) without blocking the UI.
_crop_queue = queue_module.Queue()
_crop_progress = {
    'in_progress': 0,
    'total': 0,
    'completed': 0,
    'failed': [],
    'active_jobs': {}  # Maps job_id to {image_id, filename, status, error}
}
_crop_lock = threading.Lock()
_crop_job_counter = 0

def _process_crop_jobs():
    """Background worker thread that processes crop jobs from the queue."""
    global _crop_job_counter
    while True:
        try:
            job = _crop_queue.get(timeout=1)
            if job is None:
                break

            job_id = job['id']
            image_id = job['image_id']
            user_id = job['user_id']
            box = job.get('box')
            # V32: present only on perspective jobs. .get() rather than [] so a
            # job dict queued by older code (or any future caller that only
            # knows about rectangles) still runs down the rectangle path.
            corners = job.get('corners')
            filename = job['filename']

            # NOTE: in_progress was already incremented by crop_image() when the
            # job was queued — incrementing again here (the original V27 bug)
            # meant two increments against one decrement, so the counter never
            # returned to 0 and CropModal's "wait until 0" poll spun forever.
            with _crop_lock:
                _crop_progress['active_jobs'][job_id] = {
                    'image_id': image_id,
                    'filename': filename,
                    'status': 'processing',
                    'error': None
                }

            try:
                # Geometry is validated BEFORE anything is downloaded and long
                # before anything is written — a bad selection must cost a
                # failed job, never a half-finished Drive file.
                if corners is not None:
                    # V32 perspective. parse_perspective_corners rejects
                    # bow-ties, degenerate and out-of-range quads.
                    corners = parse_perspective_corners(corners)
                    if perspective_is_whole_image(corners):
                        raise ValueError('Corners cover the whole image')
                else:
                    # Extract crop coordinates
                    box = box or {}
                    x_pct = float(box.get('x', 0))
                    y_pct = float(box.get('y', 0))
                    w_pct = float(box.get('w', 100))
                    h_pct = float(box.get('h', 100))

                    x_pct = min(max(x_pct, 0.0), 100.0)
                    y_pct = min(max(y_pct, 0.0), 100.0)
                    w_pct = min(max(w_pct, 0.0), 100.0 - x_pct)
                    h_pct = min(max(h_pct, 0.0), 100.0 - y_pct)

                    if w_pct < 1 or h_pct < 1:
                        raise ValueError('Crop box is too small')
                    if w_pct >= 99.5 and h_pct >= 99.5:
                        raise ValueError('Crop covers the whole image')

                conn = get_db()
                c = conn.cursor()
                c.execute('SELECT drive_file_id, filename, user_id FROM images WHERE id = ?', (image_id,))
                row = c.fetchone()
                conn.close()

                if not row or (user_id != 1 and row['user_id'] != user_id):
                    raise ValueError('Image not found or permission denied')

                old_file_id = row['drive_file_id']
                owner_id = row['user_id']

                service = get_drive_service()

                # Download original
                original_bytes = download_drive_file(service, old_file_id)
                meta = service.files().get(fileId=old_file_id, fields='mimeType').execute()

                # Crop in memory
                img = Image.open(io.BytesIO(original_bytes))
                img = ImageOps.exif_transpose(img)
                w_px, h_px = img.width, img.height

                if corners is not None:
                    # V32: four free corners straightened into a rectangle.
                    # Everything downstream of here — backup, overwrite,
                    # thumbnail, aspect ratio, phash, palette — is the SAME
                    # code the rectangle path runs, on purpose: the V27
                    # disaster was a crop path that overwrote Drive and then
                    # refreshed the DB differently (in that case, not at all).
                    cropped = perspective_correct(img, corners)
                else:
                    left = max(0, round(w_px * x_pct / 100.0))
                    top = max(0, round(h_px * y_pct / 100.0))
                    right = min(w_px, round(w_px * (x_pct + w_pct) / 100.0))
                    bottom = min(h_px, round(h_px * (y_pct + h_pct) / 100.0))

                    if right - left < 8 or bottom - top < 8:
                        raise ValueError('Crop is too small to produce a usable image')

                    cropped = img.crop((left, top, right, bottom))

                mime = meta.get('mimeType') or 'image/jpeg'
                fmt, save_kwargs = CROP_SAVE_FORMATS.get(mime, CROP_SAVE_FORMATS['image/jpeg'])
                if fmt == 'JPEG' and cropped.mode != 'RGB':
                    cropped = cropped.convert('RGB')

                out = io.BytesIO()
                cropped.save(out, format=fmt, **save_kwargs)
                cropped_bytes = out.getvalue()

                # Back up original to _Removed
                backup_service = get_user_drive_service(owner_id) or get_user_drive_service(1)
                if backup_service is None:
                    raise ValueError('No connected Google account for backup')

                removed_id = get_or_create_removed_folder(service, get_root_folder_id(owner_id))
                stem, dot, ext = (row['filename'] or 'image').rpartition('.')
                if not dot:
                    stem, ext = (row['filename'] or 'image'), 'jpg'
                stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                backup_service.files().create(
                    body={'name': f'{stem} (pre-crop {stamp}).{ext}', 'parents': [removed_id]},
                    media_body=MediaIoBaseUpload(io.BytesIO(original_bytes), mimetype=mime),
                    fields='id',
                ).execute()

                # Overwrite original file
                media = MediaIoBaseUpload(io.BytesIO(cropped_bytes), mimetype=mime)
                updated_file = service.files().update(
                    fileId=old_file_id, media_body=media, fields='id, md5Checksum'
                ).execute()

                # Refresh everything derived from the pixels. The original V27
                # worker wrote to width/height/crop_box — columns that have
                # never existed on `images` — so this UPDATE always threw, and
                # it threw AFTER the Drive file had already been overwritten:
                # Drive held the cropped image (so the full-res inspector
                # looked right) while the DB kept the stale pre-crop thumbnail
                # (so the home grid still looked uncropped). It also dropped
                # the thumbnail/md5/phash/palette refresh the V26 synchronous
                # version did, which would have left the grid stale anyway.
                thumbnail = generate_thumbnail(cropped_bytes)
                if not thumbnail:
                    raise ValueError(
                        'Crop saved to Drive but the new thumbnail could not be '
                        'generated. Run a duplicate scan to reconcile the library.')
                aspect_ratio = get_image_aspect_ratio(cropped_bytes)

                conn = get_db()
                c = conn.cursor()
                c.execute('''UPDATE images
                    SET thumbnail_blob = ?, aspect_ratio = ?,
                        md5_checksum = ?, phash = ?
                    WHERE id = ?''',
                    (thumbnail, aspect_ratio, updated_file.get('md5Checksum'),
                     compute_phash(thumbnail), image_id))
                conn.commit()
                conn.close()

                hexes = extract_palette(thumbnail)
                if hexes:
                    save_palette(image_id, owner_id, hexes)

                with _crop_lock:
                    _crop_progress['completed'] += 1
                    _crop_progress['active_jobs'][job_id]['status'] = 'completed'
                    _crop_progress['in_progress'] -= 1

            except Exception as e:
                error_msg = str(e)
                with _crop_lock:
                    _crop_progress['failed'].append({
                        'filename': filename,
                        'error': error_msg
                    })
                    _crop_progress['active_jobs'][job_id]['status'] = 'failed'
                    _crop_progress['active_jobs'][job_id]['error'] = error_msg
                    _crop_progress['in_progress'] -= 1

            finally:
                _crop_queue.task_done()

        except queue_module.Empty:
            continue
        except Exception as e:
            print(f"[crop worker] Unexpected error: {e}")
            continue

# Start crop worker thread (daemon, dies if main thread exits)
_crop_worker = threading.Thread(target=_process_crop_jobs, daemon=True)
_crop_worker.start()

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

    # V27: history of automatic monthly database backups uploaded to Drive.
    # Lets the backup job know when it last ran (so it fires once a month,
    # not on every boot) and which Drive file to delete once more than
    # KEEP_BACKUP_COUNT copies exist.
    c.execute('''
        CREATE TABLE IF NOT EXISTS db_backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drive_file_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    # V25: where a web-clipped image came from, so a still pulled off a blog
    # can be traced back to its page later. NULL for Drive syncs and uploads.
    try:
        c.execute("ALTER TABLE images ADD COLUMN source_url TEXT")
        conn.commit()
        print("[migration] Added source_url column to images")
    except Exception:
        pass

    # V24: how much of the frame each palette color actually covers (0.0-1.0).
    # extract_palette() always computed this and threw it away; color search
    # ranked by vibrance alone, so a lipstick-sized patch of red scored the
    # same as a red wall. NULL = extracted before V24; the backfill
    # (/api/extract-colors?force=true) fills those in.
    try:
        c.execute("ALTER TABLE colors ADD COLUMN share REAL")
        conn.commit()
        print("[migration] Added share column to colors")
    except Exception:
        pass

    # V33: which build of extract_palette() produced this row. NULL = before
    # versioning existed. backfill_palettes() rebuilds anything older than
    # PALETTE_VERSION, so an algorithm change can't leave the library half-old
    # and half-new — which would make colour search silently inconsistent
    # between two photos that look the same.
    try:
        c.execute("ALTER TABLE colors ADD COLUMN palette_version INTEGER")
        conn.commit()
        print("[migration] Added palette_version column to colors")
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
    try:
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
        print(f"Embeddings seed: loaded {loaded} vectors ({skipped} skipped)")
    except sqlite3.OperationalError as e:
        if 'disk' in str(e).lower() or 'full' in str(e).lower():
            print(f"⚠️  Embeddings seed skipped: storage full — app running without updated embeddings. Free up space in /app/data and redeploy to fix.")
            conn.rollback()
        else:
            raise
    finally:
        conn.close()

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
                        # normalize_tag_value: lowercase + plural-collapse, to
                        # match every other tag-writing path (manual edit,
                        # bulk apply) — Gemini's word choice isn't consistent
                        # run to run, so "Tense"/"tense" or "car"/"cars" would
                        # otherwise sit as separate-looking duplicates
                        # anywhere tags get grouped (autocomplete, detail
                        # panel, analytics).
                        c.execute(
                            "INSERT INTO tags (image_id, user_id, category, value) VALUES (?, ?, ?, ?)",
                            (img_id, owner_id, category, normalize_tag_value(val))
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
        # Phones store rotation as an EXIF tag, not in the pixels themselves.
        # Bake it in now (V36) — otherwise the re-saved JPEG below has no
        # orientation tag left and the thumbnail comes out sideways, even
        # though the untouched original (served straight from Drive) still
        # carries the tag and displays upright. Same fix as the crop path.
        img = ImageOps.exif_transpose(img)
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

# V30: the fingerprint grid. Was 8x8/64 bits through V29; see compute_phash.
PHASH_GRID = 16
PHASH_BITS = PHASH_GRID * PHASH_GRID   # 256
PHASH_HEX_LEN = PHASH_BITS // 4        # 64 hex characters

def compute_phash(image_data):
    """Perceptual 'difference hash' — a 256-bit visual fingerprint.

    Shrinks the image to a 17x16 grayscale grid and records, for each pixel,
    whether it's brighter than its right-hand neighbor. Two visually identical
    images (even resized, screenshotted, or re-saved) produce nearly identical
    fingerprints; counting differing bits (hamming distance) measures how
    visually different they are.

    V30 widened the grid from 8x8. This hash is now only a fast PRE-FILTER,
    never the decider — see compute_signature() for why it cannot be trusted
    alone no matter how fine the grid gets."""
    try:
        img = Image.open(io.BytesIO(image_data)).convert('L').resize(
            (PHASH_GRID + 1, PHASH_GRID), Image.Resampling.LANCZOS)
        px = list(img.getdata())
        bits = 0
        stride = PHASH_GRID + 1
        for row in range(PHASH_GRID):
            for col in range(PHASH_GRID):
                bits = (bits << 1) | (1 if px[row * stride + col] > px[row * stride + col + 1] else 0)
        return f'{bits:0{PHASH_HEX_LEN}x}'
    except Exception:
        return None

def phash_distance(a, b):
    """Differing bits between two fingerprints. Mismatched lengths mean one
    side predates the V30 widening and hasn't been backfilled yet — report
    them as maximally different rather than XOR-ing a 64-bit hash against a
    256-bit one, which would produce a meaningless (and often small) number."""
    if not a or not b or len(a) != len(b):
        return PHASH_BITS + 1
    return bin(int(a, 16) ^ int(b, 16)).count('1')

# At or below this many differing bits (out of 256), two images MAY be
# near-duplicates. Deliberately generous: measured on Ryan's real reference
# photos, a resized + heavily recompressed copy scored at worst 13, while
# genuinely unrelated pairs scored 47+. The gap is wide here, but it collapses
# on flat frames (see compute_signature), so this only nominates candidates —
# signature and colour still have to agree before anything is called a dupe.
PHASH_NEAR_DUP_THRESHOLD = 20

# ── V30: SIGNATURE — the check that actually decides ────────────────────────
# The difference-hash asks "is this pixel brighter than its neighbour?" On a
# soft, dark, letterboxed frame the answer is "no" almost everywhere, so the
# hash comes out nearly blank — measured on real moody stills, only 5-12 of
# the old 64 bits carried any signal. Two mostly-blank fingerprints are
# MATHEMATICALLY FORCED to look alike: their distance can never exceed the
# number of bits they set between them. That is why three unrelated warm,
# letterboxed frames kept grouping as duplicates, and why widening the grid
# alone does not fix it — at 16x16 those same frames set only 16 of 256 bits.
#
# The signature keeps ACTUAL brightness values instead of only their ordering,
# so a flat frame still describes itself. Contrast-normalising (subtract mean,
# divide by standard deviation) makes it immune to exposure and compression
# shifts, which is what lets the cutoff sit so far from real duplicates.
#
# Measured over 19 of Ryan's real reference photos (171 unrelated pairs):
#     resized 55% + JPEG q55 copies ... 0.004 - 0.029
#     genuinely unrelated pairs ....... 0.463 - 2.0+
# a 16x separation. The cutoff sits between, nearer the duplicate end.
SIGNATURE_GRID = 16
SIGNATURE_MAX_DISTANCE = 0.15

def compute_signature(image_data):
    """Contrast-normalised tiny grayscale. Returns a list of floats, or None."""
    try:
        img = Image.open(io.BytesIO(image_data)).convert('L').resize(
            (SIGNATURE_GRID, SIGNATURE_GRID), Image.Resampling.LANCZOS)
        px = list(img.getdata())
        if not px:
            return None
        mean = sum(px) / len(px)
        sd = (sum((p - mean) ** 2 for p in px) / len(px)) ** 0.5
        if sd < 1e-6:
            # A completely uniform frame (a solid colour card). It has no
            # structure to compare, so give it a flat signature — the colour
            # check is what will tell two solid cards apart.
            return [0.0] * len(px)
        return [(p - mean) / sd for p in px]
    except Exception:
        return None

def signature_distance(a, b):
    """RMS difference between two signatures. 0 = identical structure."""
    if not a or not b or len(a) != len(b):
        return None
    return (sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)) ** 0.5

def signatures_match(a, b):
    """Do two images actually look alike? Unlike the fingerprint this stays
    meaningful on flat/dark/letterboxed frames. A missing signature (an
    unreadable thumbnail) does not veto — phash and colour still apply."""
    d = signature_distance(a, b)
    if d is None:
        return True
    return d <= SIGNATURE_MAX_DISTANCE

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

# ============================================================================
# V27: MONTHLY DATABASE BACKUP TO DRIVE
# ============================================================================
# The images themselves already live on Drive — the SQLite database is the
# only copy of the tags, decks, bookmarks, and filmography built on top of
# them, and it lives solely on Railway's volume. This uploads a snapshot to
# a `_Backups` folder once a month and keeps only the newest KEEP_BACKUP_COUNT.

BACKUP_FOLDER_NAME = '_Backups'
KEEP_BACKUP_COUNT = 2

def get_or_create_backups_folder(service, root_id):
    q = (f"'{root_id}' in parents and name = '{BACKUP_FOLDER_NAME}' "
         "and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    res = service.files().list(q=q, fields='files(id)').execute()
    found = res.get('files', [])
    if found:
        return found[0]['id']
    meta = {
        'name': BACKUP_FOLDER_NAME,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [root_id],
    }
    return service.files().create(body=meta, fields='id').execute()['id']

def run_db_backup():
    """Snapshot the SQLite database and upload it to Drive, then prune old
    backups beyond KEEP_BACKUP_COUNT.

    Uses sqlite3's own backup() API rather than a raw file copy, so a backup
    running while the app is mid-write never captures a half-written page.

    Uploads through the ADMIN's OAuth (get_user_drive_service(1)), not the
    service account — same reason the pre-crop backup does: a service
    account has zero storage quota on a personal Drive, so any
    files().create() it makes fails with storageQuotaExceeded.
    """
    try:
        backup_service = get_user_drive_service(1)
        if backup_service is None:
            print("[db-backup] Skipped — admin hasn't connected Google.")
            return False

        # V35: backs up through RAM (Connection.serialize(), Python 3.11+),
        # never through a scratch file on the /app/data volume. That file
        # (library.db.backup-tmp) is what crashed the app on 2026-07-31: the
        # live database is 283MB on a 434MB volume, so a temp copy of it
        # can't fit alongside the original with any room to spare, and if the
        # backup dies partway (as it does whenever the volume is already
        # tight) the scratch file is never cleaned up and silently eats the
        # rest of the disk until something else — like a bulk delete needing
        # a moment's SQLite journal space — starts failing with "database or
        # disk is full" too.
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(':memory:')
        with dst:
            src.backup(dst)
        src.close()
        db_bytes = dst.serialize()
        dst.close()

        compressed = gzip.compress(db_bytes)
        stamp = datetime.now().strftime('%Y-%m-%d')
        filename = f'library-backup-{stamp}.db.gz'

        root_id = get_root_folder_id(1)
        folder_id = get_or_create_backups_folder(backup_service, root_id)

        uploaded = backup_service.files().create(
            body={'name': filename, 'parents': [folder_id]},
            media_body=MediaIoBaseUpload(io.BytesIO(compressed), mimetype='application/gzip'),
            fields='id',
        ).execute()

        conn = get_db()
        c = conn.cursor()
        c.execute('INSERT INTO db_backups (drive_file_id, filename) VALUES (?, ?)',
                  (uploaded['id'], filename))
        conn.commit()

        c.execute('SELECT id, drive_file_id, filename FROM db_backups ORDER BY created_at DESC')
        rows = c.fetchall()
        conn.close()

        for old in rows[KEEP_BACKUP_COUNT:]:
            try:
                backup_service.files().delete(fileId=old['drive_file_id']).execute()
            except Exception as e:
                print(f"[db-backup] Could not delete old backup {old['filename']}: {e}")
            conn = get_db()
            c = conn.cursor()
            c.execute('DELETE FROM db_backups WHERE id = ?', (old['id'],))
            conn.commit()
            conn.close()

        print(f"[db-backup] Uploaded {filename} to Drive.")
        return True
    except Exception as e:
        print(f"[db-backup] Failed: {e}")
        return False

def _backup_due():
    """True if no backup has completed yet this calendar month."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT created_at FROM db_backups ORDER BY created_at DESC LIMIT 1')
    row = c.fetchone()
    conn.close()
    if not row:
        return True
    last = datetime.strptime(row['created_at'].split('.')[0], '%Y-%m-%d %H:%M:%S')
    now = datetime.now()
    return (last.year, last.month) != (now.year, now.month)

def _backup_scheduler_loop():
    """Checks once a day whether this month's backup has run yet."""
    while True:
        try:
            if _backup_due():
                run_db_backup()
        except Exception as e:
            print(f"[db-backup] Scheduler error: {e}")
        time.sleep(24 * 60 * 60)

def start_backup_scheduler():
    threading.Thread(target=_backup_scheduler_loop, daemon=True).start()

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

# ── V33: PALETTE MERGE GUARDS ──────────────────────────────────────────────
# A palette entry means "this color FAMILY", and merged-away shades donate
# their share to the family (V24). That is what makes a wall split into
# dark/mid/bright red read as one big red. It is also what made colour search
# lie: nothing stopped a family from absorbing something it had no business
# absorbing, and the search only ever tests the family's stored representative.
#
# PALETTE_DARK_V matches the near-black guard in color_matches() on purpose.
# Below it, hue is numerically unstable and physically meaningless — the
# difference between #020100 and #000102 is one bit of sensor noise but a
# 180-degree swing in reported hue. Anything under this is shadow, whatever
# its nominal saturation says.
PALETTE_DARK_V = 0.12
PALETTE_GRAY_S = 0.16        # same cut the binning step uses
PALETTE_MERGE_HUE_TOL = 0.09  # 32 degrees — a family may span a real gradient


def _is_shadow_or_gray(s, v):
    """Is this HSV triple one whose hue carries no usable information?"""
    return s < PALETTE_GRAY_S or v < PALETTE_DARK_V


# Bumped whenever extract_palette()'s output would change for the same input.
# backfill_palettes() rebuilds anything stamped older than this, so a change
# to the algorithm can't leave the library half-old and half-new. NULL means
# "extracted before versioning existed" and is always rebuilt.
PALETTE_VERSION = 2


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

        def _dup_index(rgb, chosen):
            # Hue-aware dedupe: the brightness-weighted distance alone thinks
            # dark green == dark brown and white == pale sage. Colors from
            # different hue families never merge unless nearly identical.
            # Returns the index of the color that absorbs this one, else None.
            #
            # V33: a bin may only join a family it actually belongs to. HSV
            # saturation is meaningless once a color is nearly black — #020100
            # reports saturation 1.0 and hue 30, arithmetically indistinguish-
            # able from a vivid orange — so the old "both saturated" branch
            # happily merged pure shadow into a dark warm entry, and the
            # shadow then DONATED its share to it. Measured on Flex 3.jpg:
            # search reported 54% orange where only 9.5% of the frame was
            # orange, the gap being a #020100 bin covering 39% of the frame.
            # Classify by the same rule the binning step above uses, and never
            # merge across that divide in either direction.
            h1, s1, v1 = colorsys.rgb_to_hsv(rgb[0]/255.0, rgb[1]/255.0, rgb[2]/255.0)
            n1 = _is_shadow_or_gray(s1, v1)
            for i, c in enumerate(chosen):
                h2, s2, v2 = colorsys.rgb_to_hsv(c[0]/255.0, c[1]/255.0, c[2]/255.0)
                if _is_shadow_or_gray(s2, v2) != n1:
                    continue
                if n1:
                    if abs(v1 - v2) < 0.25:  # spread neutrals across brightness
                        return i
                    continue
                d = _dist(rgb, c)
                if d < 120:  # nearly identical regardless of hue
                    return i
                hd = abs(h1 - h2)
                hd = min(hd, 1 - hd)  # hue wraps around the color wheel
                if d < 450 and hd < PALETTE_MERGE_HUE_TOL:
                    return i
            return None

        chromatic.sort(reverse=True)
        neutrals.sort(reverse=True)

        # V24: `picked` carries [rgb, share]. A merged-away shade donates its
        # share to whichever color absorbed it, so the stored number means
        # "how much of the frame this color FAMILY covers" — a wall split into
        # dark/mid/bright red reads as one big red, which is what the eye sees
        # and what the prominence filter needs. Keep scanning after the palette
        # fills so those late shades still get counted; only new entries stop.
        picked = []

        def _absorb(share, rgb, cap):
            idx = _dup_index(rgb, [p[0] for p in picked])
            if idx is not None:
                picked[idx][1] += share
            elif len(picked) < cap:
                picked.append([rgb, share])

        for score, share, rgb in chromatic:
            if share < 0.001:  # under ~0.1% of pixels = JPEG noise, not a color
                continue
            _absorb(share, rgb, num_colors - 2)
        for score, share, rgb in neutrals:
            if share < 0.01:  # a neutral must cover at least 1% of the frame
                continue
            _absorb(share, rgb, num_colors)

        return [('#%02x%02x%02x' % tuple(rgb), round(share, 5)) for rgb, share in picked]
    except Exception:
        return []

def backfill_palettes():
    """Self-heal any palette older than PALETTE_VERSION (V24 shares, V33 merge
    fix). Rebuilding from the stored thumbnails costs no Drive or Gemini calls
    and runs at roughly 20ms an image.

    V24 gave every entry a `share`; rows from before that have share NULL.
    V33 stopped shadow bins from donating their share to a colour family, so
    every palette stored before it OVERSTATES how much of the frame its warm
    colours cover — the numbers are wrong, not merely missing, which is why
    this keys off a version stamp instead of a NULL check.

    Runs in a background thread so a large library can't delay boot, and
    self-disables: once every row is stamped current the query finds nothing
    and this returns immediately on all later boots."""
    try:
        conn = get_db()
        # `share IS NULL` is redundant against a correctly-stamped row —
        # save_palette() always writes both together — but it keeps the V24
        # guarantee that no share-less row can survive a boot, even if some
        # future code path writes one without a version.
        pending = [r['image_id'] for r in conn.execute(
            'SELECT DISTINCT image_id FROM colors'
            ' WHERE palette_version IS NULL OR palette_version < ? OR share IS NULL',
            (PALETTE_VERSION,)
        ).fetchall()]
        conn.close()
    except Exception as e:
        print(f"[palette-backfill] Could not check for pending rows: {e}")
        return

    if not pending:
        return

    def _job():
        done = failed = unrepairable = 0
        for image_id in pending:
            try:
                conn = get_db()
                row = conn.execute(
                    'SELECT id, user_id, thumbnail_blob FROM images WHERE id = ?', (image_id,)
                ).fetchone()
                conn.close()
                # No thumbnail, or one Pillow can't read, means this palette can
                # never be rebuilt — and it will be retried on every future boot
                # forever. Count it out loud: silently skipping is what made the
                # pre-V33 blind spot invisible in the logs.
                if not row or not row['thumbnail_blob']:
                    unrepairable += 1
                    continue
                entries = extract_palette(row['thumbnail_blob'])
                if not entries:
                    unrepairable += 1
                    continue
                save_palette(row['id'], row['user_id'], entries)
                done += 1
            except Exception as e:
                failed += 1
                print(f"[palette-backfill] Image {image_id} failed: {e}")
        print(f"[palette-backfill] Rebuilt {done} palette(s) to v{PALETTE_VERSION}"
              + (f", {failed} failed" if failed else "")
              + (f", {unrepairable} unrepairable (no usable thumbnail)"
                 if unrepairable else "")
              + ". Colour search is now reading corrected coverage.")

    print(f"[palette-backfill] {len(pending)} palette(s) older than "
          f"v{PALETTE_VERSION} — rebuilding in background.")
    threading.Thread(target=_job, daemon=True).start()


def backfill_phashes():
    """V30 one-time self-heal: rebuild fingerprints that predate the 16x16
    widening.

    Pre-V30 hashes are 16 hex characters (64 bits); V30 ones are 64 (256
    bits). phash_distance() reports mismatched lengths as maximally different
    rather than XOR-ing them into a meaningless number, so until a row is
    rebuilt it simply never matches anything — duplicate detection degrades
    to "finds nothing", never to "finds the wrong thing".

    Rebuilding from the stored thumbnails costs no Drive or Gemini calls.
    Runs in a background thread so a large library can't delay boot, and
    self-disables once every row is the new width."""
    try:
        conn = get_db()
        pending = [r['id'] for r in conn.execute(
            'SELECT id FROM images WHERE phash IS NOT NULL AND LENGTH(phash) != ?',
            (PHASH_HEX_LEN,)
        ).fetchall()]
        conn.close()
    except Exception as e:
        print(f"[phash-backfill] Could not check for pending rows: {e}")
        return

    if not pending:
        return

    def _job():
        done = failed = 0
        for image_id in pending:
            try:
                conn = get_db()
                row = conn.execute(
                    'SELECT id, thumbnail_blob FROM images WHERE id = ?', (image_id,)
                ).fetchone()
                conn.close()
                if not row or not row['thumbnail_blob']:
                    continue
                ph = compute_phash(row['thumbnail_blob'])
                if ph:
                    conn = get_db()
                    conn.execute('UPDATE images SET phash = ? WHERE id = ?', (ph, image_id))
                    conn.commit()
                    conn.close()
                    done += 1
            except Exception as e:
                failed += 1
                print(f"[phash-backfill] Image {image_id} failed: {e}")
        print(f"[phash-backfill] Rebuilt {done} fingerprint(s) at {PHASH_GRID}x{PHASH_GRID}"
              + (f", {failed} failed" if failed else "") + ".")

    print(f"[phash-backfill] {len(pending)} fingerprint(s) predate V30 — rebuilding in background.")
    threading.Thread(target=_job, daemon=True).start()

def merge_plural_tag_duplicates():
    """V30 one-time cleanup: collapse existing plural/singular tag drift
    (e.g. an image tagged 'cars' and another tagged 'car' for the same
    subject) now that normalize_tag_value() stops new drift at write time.
    This is what fixes what's already in the database; the write-time
    normalization only prevents new drift, it can't retroactively fix rows
    written before it existed.

    Only ever merges tags that already coexist on the same photo, in the
    same category — never touches two different photos' tags, and never
    crosses categories (an image tagged 'car' under location_type and
    'car' under subjects are two different facts and stay separate).

    Runs once at boot; self-disables once nothing's left to merge (each
    later boot's initial SELECT finds no drift and returns immediately)."""
    try:
        conn = get_db()
        rows = conn.execute('SELECT id, image_id, category, value FROM tags').fetchall()
        conn.close()
    except Exception as e:
        print(f"[tag-merge] Could not scan tags: {e}")
        return

    by_image_cat = {}
    for r in rows:
        by_image_cat.setdefault((r['image_id'], r['category']), []).append(r)

    to_delete = []      # tag ids to remove outright
    to_rename = []      # (tag id, new value)
    for group in by_image_cat.values():
        buckets = {}
        for r in group:
            buckets.setdefault(normalize_tag_value(r['value']), []).append(r)
        for normalized, variants in buckets.items():
            if len(variants) == 1:
                if variants[0]['value'] != normalized:
                    to_rename.append((variants[0]['id'], normalized))
                continue
            # Multiple rows collapse onto the same normalized value on this
            # photo (e.g. both 'car' and 'cars'). Keep one — preferring a row
            # already spelled the normalized way — delete the rest.
            keeper = next((r for r in variants if r['value'] == normalized), variants[0])
            if keeper['value'] != normalized:
                to_rename.append((keeper['id'], normalized))
            to_delete.extend(r['id'] for r in variants if r['id'] != keeper['id'])

    if not to_delete and not to_rename:
        return

    def _job():
        try:
            conn = get_db()
            for tag_id, new_value in to_rename:
                conn.execute('UPDATE tags SET value = ? WHERE id = ?', (new_value, tag_id))
            for tag_id in to_delete:
                conn.execute('DELETE FROM tags WHERE id = ?', (tag_id,))
            conn.commit()
            conn.close()
            print(f"[tag-merge] Renamed {len(to_rename)} tag(s), removed "
                  f"{len(to_delete)} duplicate(s) left behind by the merge.")
        except Exception as e:
            print(f"[tag-merge] Failed: {e}")

    print(f"[tag-merge] {len(to_rename)} tag(s) need renaming, {len(to_delete)} duplicate(s) "
          "to remove — merging in background.")
    threading.Thread(target=_job, daemon=True).start()


def save_palette(image_id, user_id, entries):
    """entries: list of (hex, share) from extract_palette. Bare hex strings are
    still accepted (share stored NULL) so any older caller keeps working."""
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM colors WHERE image_id = ?', (image_id,))
    for rank, entry in enumerate(entries):
        if isinstance(entry, (tuple, list)):
            hex_color, share = entry[0], entry[1]
        else:
            hex_color, share = entry, None
        c.execute(
            'INSERT INTO colors (image_id, user_id, hex, rank, share, palette_version)'
            ' VALUES (?, ?, ?, ?, ?, ?)',
            (image_id, user_id, hex_color, rank, share, PALETTE_VERSION)
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


# ── V24: COLOR SEARCH — PROMINENCE + HUE EXACTNESS ─────────────────────────
# Two knobs replace the old single fixed distance threshold:
#   prominence — how much of the frame the color must cover (summed share)
#   exactness  — how close in hue it has to be
#
# Hue replaces weighted-RGB for the closeness test because that metric is
# green-heavy and so reads "darker and duller" as "different color": measured
# against the red swatch, brown scored 461 and rust 406, both inside the old
# 1000 threshold, while pink (a far better red match) scored 1633. In hue
# terms the split is clean — real reds land under 14 degrees, brown/rust/
# orange sit at 22-27. Saturation guards are mandatory: gray reports hue 0.0,
# identical to pure red, and would otherwise match it perfectly.

EXACTNESS_LOOSE_DEG = 30.0   # exactness = 0
EXACTNESS_TIGHT_DEG = 6.0    # exactness = 100
DEFAULT_EXACTNESS = 60.0     # ~15 deg: keeps crimson/brick/burgundy, drops brown/rust
DEFAULT_PROMINENCE = 6.0     # percent of frame

# V33: the exactness slider also controls a BRIGHTNESS tolerance, because for
# warm colors hue alone physically cannot do the job. Brown is not its own
# hue — brown IS dark orange, sitting within a couple of degrees of it on the
# wheel (measured: picked orange #E08840 at 27 deg, mid brown #8B5A2B at 29,
# near-black brown #241205 at 25). Dragging exactness to maximum therefore
# used to discard bright amber and pale gold — colors Ryan would call orange —
# while keeping every brown down to brightness 0.14. That is backwards.
#
# This is deliberately NOT a third slider. Hue-tightness and brightness-
# tightness point the same direction for every warm color, and the control
# already reads as "be pickier", so folding them keeps two knobs instead of
# three. The V24 red calibration is unaffected: red's neighbours differ in
# hue, so the hue test was already doing the work there.
EXACTNESS_LOOSE_VAL = 0.90   # exactness = 0   -> effectively unconstrained
EXACTNESS_TIGHT_VAL = 0.22   # exactness = 100 -> must be about as bright


def _hsv(hex_color):
    import colorsys
    r, g, b = hex_to_rgb(hex_color)
    return colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)


def exactness_to_hue_tol(exactness):
    """0-100 slider -> allowed hue difference as a fraction of the wheel."""
    e = max(0.0, min(100.0, float(exactness)))
    deg = EXACTNESS_LOOSE_DEG - (e / 100.0) * (EXACTNESS_LOOSE_DEG - EXACTNESS_TIGHT_DEG)
    return deg / 360.0


def exactness_to_value_tol(exactness):
    """0-100 slider -> allowed brightness difference (HSV value, 0-1)."""
    e = max(0.0, min(100.0, float(exactness)))
    return EXACTNESS_LOOSE_VAL - (e / 100.0) * (EXACTNESS_LOOSE_VAL - EXACTNESS_TIGHT_VAL)


def color_matches(picked_hex, candidate_hex, hue_tol, value_tol=None):
    """Does one palette entry count as the picked color?

    value_tol is the V33 brightness rule. It defaults to None = no brightness
    constraint beyond the hard near-black floor, which keeps every non-search
    caller (notably the duplicate-detection color gate, calibrated in V29/V30
    to 0 false positives across 38 cases) behaving exactly as before."""
    try:
        hp, sp, vp = _hsv(picked_hex)
        hc, sc, vc = _hsv(candidate_hex)
    except Exception:
        return False

    # Picking a neutral (gray/black/cream): hue carries no information, so
    # match on "also neutral, and about as bright."
    if sp < 0.18:
        if sc >= 0.28:
            return False
        return abs(vp - vc) <= 0.22

    # Picked a real color — a washed-out or near-gray palette entry is not it,
    # however close its nominal hue reads.
    if sc < 0.22:
        return False
    # Near-black entries have unstable hue; don't let them count. This floor
    # is physical, not a preference, so it applies at every slider setting.
    if vc < PALETTE_DARK_V:
        return False
    # V33: brightness distance. Symmetric on purpose — picking a bright orange
    # rejects dark brown, and picking a dark brown rejects bright orange.
    if value_tol is not None and abs(vp - vc) > value_tol:
        return False

    hd = abs(hp - hc)
    hd = min(hd, 1.0 - hd)  # hue wraps around the wheel
    if hd > hue_tol:
        return False

    # Sanity on saturation distance so a pale pastel doesn't read as a
    # saturated hue at loose settings (and vice versa). Scaled to the picked
    # color's own saturation: picking a vivid red (sp high) should reject
    # muddy rust/brown (sc low) even though their hue is close, while picking
    # a muted/dusty color shouldn't demand the candidate be equally vivid.
    if sp >= 0.60:
        return sc >= sp - 0.20
    return abs(sp - sc) <= 0.55


def color_match_share(picked_hex, entries, hue_tol, value_tol=None):
    """Summed share of every palette entry matching the picked color.

    entries: list of (hex, share). A wall extracted as dark/mid/bright red
    counts once, at its combined size — matching how the eye reads the frame.
    Entries stored before V24 have share NULL; those fall back to counting as
    a match with no size information (see caller)."""
    total = 0.0
    for hex_color, share in entries:
        if share is None:
            continue
        if color_matches(picked_hex, hex_color, hue_tol, value_tol):
            total += share
    return total


# V27: color-overlap check for near-duplicate detection. phash only reads
# brightness LAYOUT (dark frame, one bright patch) — two completely
# different photos can share that shape (e.g. a night street and a lit
# doorway) and get flagged as duplicates even though no human would say they
# match. Requiring the actual colors to line up too, using the same
# hue-based closeness test as color search, catches that.
DUP_COLOR_HUE_TOL = exactness_to_hue_tol(DEFAULT_EXACTNESS)
DUP_COLOR_MIN_OVERLAP = 0.5  # each side's own colorful content must be at least half explained by the other's

def _chromatic_entries(entries):
    """Palette entries with enough color to carry hue information. Near-
    black/white/gray entries are excluded — otherwise two unrelated dark
    photos would "match" purely because both are mostly black background."""
    out = []
    for hex_color, share in entries:
        if share is None:
            continue
        try:
            _, s, v = _hsv(hex_color)
        except Exception:
            continue
        if s >= 0.22 and v >= 0.12:
            out.append((hex_color, share))
    return out

def palettes_overlap(entries_a, entries_b):
    """True if two palettes describe the same actual colors, not just a
    similar brightness shape. entries: list of (hex, share) from the colors
    table. If either image has no real color signal (b&w, or palette not
    extracted yet), this doesn't block a match — phash alone decides, same
    graceful-degradation rule color search already uses for pre-V24 rows."""
    chroma_a = _chromatic_entries(entries_a)
    chroma_b = _chromatic_entries(entries_b)
    if not chroma_a or not chroma_b:
        return True

    def matched_fraction(entries, other):
        total = sum(share for _, share in entries)
        if total <= 0:
            return 0.0
        matched = sum(share for hex_color, share in entries
                      if any(color_matches(hex_color, other_hex, DUP_COLOR_HUE_TOL)
                             for other_hex, _ in other))
        return matched / total

    return (matched_fraction(chroma_a, chroma_b) >= DUP_COLOR_MIN_OVERLAP
            and matched_fraction(chroma_b, chroma_a) >= DUP_COLOR_MIN_OVERLAP)

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

        # V30: sync-delete parity. A photo deleted directly in Drive (not
        # through the app) just vanishes from this listing — nothing else
        # here would ever notice, so the library would carry a dead entry
        # forever. Automatic, no confirmation (Ryan's call), but guarded
        # against the one failure mode that makes "automatic" dangerous: a
        # partial or broken Drive listing looking identical to a mass
        # deletion. If more than half the library would vanish in one pass,
        # that's almost certainly Drive/pagination trouble, not Ryan deleting
        # half his library — skip and log instead of cascading the deletes.
        current_drive_ids = {image['id'] for image in all_images}
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT id, drive_file_id, filename FROM images WHERE user_id = ?', (user_id,))
        library_rows = c.fetchall()
        conn.close()

        missing_rows = [row for row in library_rows if row['drive_file_id'] not in current_drive_ids]
        removed_count = 0
        if library_rows and len(missing_rows) > len(library_rows) / 2:
            sync_state['errors'].append(
                f"Skipped removing {len(missing_rows)} image(s) that looked deleted from Drive — "
                "that's more than half the library, which usually means Drive didn't fully list the "
                "folder rather than a real mass-deletion. Nothing was removed; try syncing again.")
        else:
            for row in missing_rows:
                conn = get_db()
                c = conn.cursor()
                for table in ('tags', 'colors', 'embeddings', 'deck_images', 'filmography',
                              'user_favorites', 'user_flags', 'image_views'):
                    c.execute(f'DELETE FROM {table} WHERE image_id = ?', (row['id'],))
                c.execute('DELETE FROM images WHERE id = ?', (row['id'],))
                conn.commit()
                conn.close()
                removed_count += 1

        conn = get_db()
        c = conn.cursor()
        c.execute('''
            UPDATE sync_settings SET last_sync = CURRENT_TIMESTAMP
            WHERE user_id = ? AND folder_id = ?
        ''', (user_id, folder_id))
        conn.commit()
        conn.close()

        print(f"Sync complete. {new_count} new images added"
              + (f", {removed_count} removed (deleted from Drive)" if removed_count else "") + ".")

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

@app.route('/api/backups/status', methods=['GET'])
@admin_required
def backups_status():
    """History of automatic monthly database backups, newest first (V27)."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT filename, created_at FROM db_backups ORDER BY created_at DESC')
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify({'backups': rows, 'keep_count': KEEP_BACKUP_COUNT})

@app.route('/api/backups/run', methods=['POST'])
@admin_required
def backups_run_now():
    """Manually trigger a database backup right now (V27) — for testing the
    monthly job without waiting a month, or forcing a fresh copy on demand."""
    ok = run_db_backup()
    if not ok:
        return jsonify({'error': 'Backup failed — check server logs for details.'}), 500
    return jsonify({'success': True})

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

    is_unfiltered = not (active_chips or nl_groups or color_raw or film_raw or ar_raw)
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
        SELECT id, filename, thumbnail_blob, caption, aspect_ratio, {fav_flag_cols(uid)}
        FROM images {where}
        ORDER BY {order_by} LIMIT ? OFFSET ?
    ''', params + order_params + [per, page * per]).fetchall()
    total = c.execute(f'SELECT COUNT(*) FROM images {where}', params).fetchone()[0]

    images_out = hydrate_image_rows(c, rows)
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

def _load_existing_phashes():
    """Every already-known image fingerprint plus palette (for the
    color-overlap check), for near-duplicate checks."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, filename, thumbnail_blob, phash FROM images WHERE phash IS NOT NULL')
    rows = [dict(r) for r in c.fetchall()]
    c.execute('SELECT image_id, hex, share FROM colors')
    palettes = {}
    for r in c.fetchall():
        palettes.setdefault(r['image_id'], []).append((r['hex'], r['share']))
    conn.close()
    for r in rows:
        r['colors'] = palettes.get(r['id'], [])
    return rows


def _ingest_image(service, folder_id, image_data, filename, mimetype, existing,
                  force=False, source_url=None):
    """Put one image into the library: duplicate check, write to Drive, store
    the row, build the thumbnail + palette.

    Shared by /api/upload and /api/clip so the browser extension can't drift
    away from the in-app uploader. `existing` is the phash+palette list from
    _load_existing_phashes(); successful ingests are appended to it so a batch
    also dedupes against itself. Returns the per-file result dict.
    """
    img_phash = compute_phash(image_data)
    thumbnail = generate_thumbnail(image_data)
    new_palette = extract_palette(thumbnail) if thumbnail else []
    new_signature = compute_signature(thumbnail) if thumbnail else None

    if not force and img_phash:
        # Same three gates as the Duplicate Review scan, cheapest first: the
        # fingerprint nominates, the signature and the palette confirm. The
        # signature is only decoded for candidates the fingerprint nominated.
        def _is_dup(r):
            if phash_distance(img_phash, r['phash']) > PHASH_NEAR_DUP_THRESHOLD:
                return False
            if 'signature' not in r:
                r['signature'] = compute_signature(r['thumbnail_blob'])
            return (signatures_match(new_signature, r['signature'])
                    and palettes_overlap(new_palette, r['colors']))

        dup = next((r for r in existing if _is_dup(r)), None)
        if dup:
            return {
                'filename': filename,
                'status': 'duplicate',
                'existing': {
                    'id': dup['id'],
                    'filename': dup['filename'],
                    'thumbnail': f"data:image/jpeg;base64,{base64.b64encode(dup['thumbnail_blob']).decode('utf-8')}"
                }
            }

    try:
        media = MediaIoBaseUpload(io.BytesIO(image_data), mimetype=mimetype or 'image/jpeg')
        drive_file = service.files().create(
            body={'name': filename, 'parents': [folder_id]},
            media_body=media, fields='id, md5Checksum'
        ).execute()
    except Exception as e:
        return {'filename': filename, 'status': 'error', 'message': str(e)}

    aspect_ratio = get_image_aspect_ratio(image_data)

    conn = get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio,
                            tagging_status, md5_checksum, phash, source_url)
        VALUES (1, ?, ?, ?, ?, 'pending', ?, ?, ?)
    ''', (drive_file['id'], filename, thumbnail, aspect_ratio,
          drive_file.get('md5Checksum'), img_phash, source_url))
    new_id = c.lastrowid
    conn.commit()
    conn.close()

    if thumbnail:
        if new_palette:
            save_palette(new_id, 1, new_palette)
        existing.append({'id': new_id, 'filename': filename,
                         'thumbnail_blob': thumbnail, 'phash': img_phash,
                         'colors': new_palette, 'signature': new_signature})

    return {'filename': filename, 'status': 'uploaded', 'image_id': new_id}


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
    existing = _load_existing_phashes()

    results = [
        _ingest_image(service, folder_id, f.read(), f.filename, f.mimetype, existing, force=force)
        for f in files
    ]

    if any(r['status'] == 'uploaded' for r in results):
        trigger_tagging()

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
        service = get_user_drive_service(user_id)
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

    existing = _load_existing_phashes()
    result = _ingest_image(
        service, get_root_folder_id(user_id), image_data,
        _clip_filename(source_url, mimetype), mimetype,
        existing, force=force, source_url=source_url,
    )

    if result['status'] == 'uploaded':
        trigger_tagging()
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
    value = normalize_tag_value(data.get('value'))
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
@admin_required
def tags_selection_summary():
    data = request.get_json(force=True) or {}
    image_ids = data.get('image_ids')
    if not isinstance(image_ids, list) or not image_ids or \
            not all(isinstance(i, int) for i in image_ids):
        return jsonify({'error': 'image_ids must be a non-empty list of ints'}), 400

    conn = get_db()
    c = conn.cursor()
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
@admin_required
def tags_suggestions():
    data = request.get_json(force=True) or {}
    image_ids = data.get('image_ids')
    if not isinstance(image_ids, list) or not image_ids or \
            not all(isinstance(i, int) for i in image_ids):
        return jsonify({'error': 'image_ids must be a non-empty list of ints'}), 400

    conn = get_db()
    c = conn.cursor()
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
        root_id = get_root_folder_id(1)
        removed_folder_id = get_or_create_removed_folder(get_drive_service(), root_id)

        # One Drive service per worker thread, not one shared across all of
        # them or one built fresh per photo — building it is cheap (no
        # network call, static discovery doc), and threading.local keeps
        # each worker's httplib2 transport from being touched by another
        # thread mid-request.
        thread_local = threading.local()
        def _thread_service():
            if not hasattr(thread_local, 'service'):
                thread_local.service = get_drive_service()
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
                    if attempt <= 2 and drive_error_reason(e) in DRIVE_RATE_LIMIT_REASONS:
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

def download_drive_file(service, file_id):
    """Download a Drive file's raw bytes through the service account."""
    req = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, req)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()

def drive_error_reason(e):
    """Google's machine-readable error reason (e.g. 'storageQuotaExceeded',
    'insufficientFilePermissions') from an HttpError, so callers don't have
    to guess what went wrong from the message text."""
    if isinstance(e, HttpError):
        try:
            errors = json.loads(e.content).get('error', {}).get('errors') or []
            if errors:
                return errors[0].get('reason')
        except Exception:
            pass
    return None

# How each format gets re-saved after cropping. Pillow can't reuse the source
# file's exact compression settings on a cropped copy, so JPEG quality 95 with
# no chroma subsampling is the closest thing to "original quality" — visually
# indistinguishable from the source.
CROP_SAVE_FORMATS = {
    'image/jpeg': ('JPEG', {'quality': 95, 'subsampling': 0, 'optimize': True}),
    'image/png': ('PNG', {'optimize': True}),
    'image/webp': ('WEBP', {'quality': 95, 'method': 6}),
    'image/gif': ('GIF', {}),
}

# ============================================================================
# V32: PERSPECTIVE CROP — de-skew a four-cornered selection into a rectangle
# ============================================================================
# Ryan photographs monitors, posters and projected images that sit on an
# angle. A rectangle can't extract those, so crop mode gained a second shape:
# four independently-dragged corners, straightened back into a rectangle.
#
# Everything below is an ADDITION alongside the rectangle path, never a
# replacement. A request with no `corners` field behaves exactly as it did
# before — same validation, same worker branch, same output.

# The quad has to cover at least this much of the frame (percent of the whole
# image area). Same spirit as the rectangle path's "w >= 1% and h >= 1%":
# a stray double-click that collapses the quad to a speck should be refused
# with a readable message, not turned into an 8-pixel file in Drive.
PERSPECTIVE_MIN_AREA_PCT = 1.0

# Two corners closer together than this (in percent-of-frame units) are
# treated as the same point — a degenerate "triangle" quad, which has no
# unique perspective solution and makes the 8x8 solve singular.
PERSPECTIVE_MIN_CORNER_GAP_PCT = 0.5


def _quad_signed_area(pts):
    """Shoelace area of a polygon. The sign tells you which way the points
    wind; the magnitude is the area in whatever units the points are in."""
    total = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def parse_perspective_corners(raw):
    """Validate the four corner points the browser sends and hand back
    [(x, y), ...] in percent (0-100), in the order the caller gave them:
    top-left, top-right, bottom-right, bottom-left.

    Raises ValueError with a message written for Ryan, not for a log file.

    Why this is stricter than the rectangle path, which just clamps: clamping
    a rectangle is harmless — it stays a rectangle in the same place. Clamping
    ONE corner of a quadrilateral silently changes the SHAPE of the correction,
    so the file Drive ends up holding would not be the one the preview showed.
    Every check below has to fail HERE, before the destructive write, because a
    crop overwrites the Drive file in place and there is no undo.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError('Perspective correction needs exactly four corner points.')

    pts = []
    for p in raw:
        if isinstance(p, dict):
            cx, cy = p.get('x'), p.get('y')
        elif isinstance(p, (list, tuple)) and len(p) == 2:
            cx, cy = p
        else:
            raise ValueError('Each corner must be a point with numeric x and y percentages.')
        try:
            cx = float(cx)
            cy = float(cy)
        except (TypeError, ValueError):
            raise ValueError('Each corner must be a point with numeric x and y percentages.')
        if not (math.isfinite(cx) and math.isfinite(cy)):
            raise ValueError('Corner points must be real numbers.')
        # Rejected, not clamped — see the docstring.
        if not (0.0 <= cx <= 100.0) or not (0.0 <= cy <= 100.0):
            raise ValueError('Corner points must sit inside the image (0-100%).')
        pts.append((cx, cy))

    # Two corners on top of each other collapse the quad to a triangle, which
    # has no unique perspective solution (the 8x8 system below goes singular).
    for i in range(4):
        for j in range(i + 1, 4):
            if math.dist(pts[i], pts[j]) < PERSPECTIVE_MIN_CORNER_GAP_PCT:
                raise ValueError('Two corner points are on top of each other — drag them apart.')

    # Convexity, checked as "every turn goes the same way". This is the
    # bow-tie guard: drag the top-left handle past the top-right one and the
    # edges cross, one cross product flips sign, and we refuse. It also
    # rejects concave quads, which is correct rather than merely convenient —
    # a rectangle photographed from ANY angle is always convex, so a dented
    # quad is not a perspective view of anything and the transform would fold
    # the picture over itself.
    signs = []
    for i in range(4):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % 4]
        cx, cy = pts[(i + 2) % 4]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) < 1e-9:
            raise ValueError('Those four corners are in a straight line — they do not enclose an area.')
        signs.append(cross > 0)
    if len(set(signs)) != 1:
        raise ValueError('Those corners cross over each other. Drag them so the outline is a simple four-sided shape.')

    # Winding direction is deliberately NOT pinned. A flipped quad produces a
    # mirrored result, which is exactly what the on-screen preview shows, so
    # it stays the user's call rather than a confusing rejection.

    if abs(_quad_signed_area(pts)) < PERSPECTIVE_MIN_AREA_PCT:
        raise ValueError('That selection is too small — it must cover at least 1% of the image.')

    return pts


def perspective_is_whole_image(pts, tolerance=0.5):
    """True when the quad is (within a hair of) the untouched image corners —
    a no-op. Mirrors the rectangle path's "covers the whole image" refusal so
    a stray Apply can't burn a destructive Drive rewrite doing nothing."""
    want = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    return all(abs(p[0] - q[0]) <= tolerance and abs(p[1] - q[1]) <= tolerance
               for p, q in zip(pts, want))


def solve_linear_system(matrix, rhs):
    """Gaussian elimination with partial pivoting. Returns the solution vector.

    Deliberately hand-written: numpy would do this in one line, but numpy is
    NOT in requirements.txt, and adding it costs every Railway deploy an extra
    few minutes forever in exchange for one 8x8 solve that fits in 20 lines.
    """
    n = len(rhs)
    # Work on copies — callers reuse their coefficient rows.
    a = [list(row) + [rhs[i]] for i, row in enumerate(matrix)]

    for col in range(n):
        # Partial pivoting: swap the largest remaining magnitude into place.
        # Without it a perfectly legitimate quad whose diagonal entry happens
        # to be zero — an exactly axis-aligned corner, which is the COMMON
        # case here since the handles are seeded from a rectangle — divides
        # by zero instead of solving.
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError('Those four corners do not describe a valid perspective.')
        a[col], a[pivot] = a[pivot], a[col]

        inv = 1.0 / a[col][col]
        for r in range(col + 1, n):
            factor = a[r][col] * inv
            if factor == 0.0:
                continue
            for k in range(col, n + 1):
                a[r][k] -= factor * a[col][k]

    out = [0.0] * n
    for row in range(n - 1, -1, -1):
        acc = a[row][n] - sum(a[row][k] * out[k] for k in range(row + 1, n))
        out[row] = acc / a[row][row]
    return out


def solve_perspective_coeffs(from_pts, to_pts):
    """The eight numbers Pillow's Image.PERSPECTIVE wants.

    A perspective map is  X = (a*x + b*y + c) / (g*x + h*y + 1)
                          Y = (d*x + e*y + f) / (g*x + h*y + 1)
    Multiplying out gives two linear equations per corner; four corners make
    an 8x8 system in (a, b, c, d, e, f, g, h).

    IMPORTANT DIRECTION NOTE: Pillow's transform walks the OUTPUT pixels and
    asks where each one came FROM, so the coefficients must map
    output -> input. Call this with from_pts = the destination rectangle and
    to_pts = the quad in the source photo. Getting this backwards warps the
    picture the wrong way instead of raising, so it is worth the paragraph.
    """
    matrix = []
    rhs = []
    for (x, y), (tx, ty) in zip(from_pts, to_pts):
        matrix.append([x, y, 1, 0, 0, 0, -tx * x, -tx * y])
        rhs.append(tx)
        matrix.append([0, 0, 0, x, y, 1, -ty * x, -ty * y])
        rhs.append(ty)
    return solve_linear_system(matrix, rhs)


def perspective_output_size(src_px):
    """Pixel size of the de-skewed rectangle, derived from the quad's own edges.

    Each side of the output is the AVERAGE of the two OPPOSITE edges of the
    quad. Picking one edge instead would bake the perspective back into the
    result: on a monitor shot from the left, the near (right) edge is much
    longer than the far one, so sizing off the near edge stretches the whole
    image and sizing off the far edge squashes it. The average lands between
    the two and keeps the subject's proportions roughly true, which is what
    "de-skew" should mean. It also can't invent detail — the output is never
    larger than the longest edge the photo actually contains.
    """
    tl, tr, br, bl = src_px
    top = math.dist(tl, tr)
    bottom = math.dist(bl, br)
    left = math.dist(tl, bl)
    right = math.dist(tr, br)
    return int(round((top + bottom) / 2.0)), int(round((left + right) / 2.0))


def perspective_correct(img, corners_pct):
    """Straighten the quadrilateral `corners_pct` (percentages, TL/TR/BR/BL)
    out of `img` into a plain rectangle. Returns a new PIL image."""
    w_px, h_px = img.width, img.height
    # Percent -> pixels happens HERE and only here, which is what lets the same
    # four numbers mean the same selection whether the browser was looking at a
    # 600px preview or the 6000px original.
    src = [(x / 100.0 * w_px, y / 100.0 * h_px) for x, y in corners_pct]

    out_w, out_h = perspective_output_size(src)
    # Same floor the rectangle path uses — below this there is no usable
    # picture left and the downstream thumbnail/palette work gets silly.
    if out_w < 8 or out_h < 8:
        raise ValueError('Perspective selection is too small to produce a usable image')

    dst = [(0, 0), (out_w, 0), (out_w, out_h), (0, out_h)]
    coeffs = solve_perspective_coeffs(dst, src)
    return img.transform((out_w, out_h), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC)

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
    global _crop_job_counter
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

    with _crop_lock:
        _crop_job_counter += 1
        job_id = _crop_job_counter
        _crop_progress['total'] += 1
        _crop_progress['in_progress'] += 1

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
    _crop_queue.put(job)

    return jsonify({
        'queued': True,
        'job_id': job_id,
        'message': 'Crop queued — check progress in the notification below.'
    })

@app.route('/api/crop-progress', methods=['GET'])
def get_crop_progress():
    """Get current crop job queue progress and failures (V27)."""
    with _crop_lock:
        return jsonify({
            'in_progress': _crop_progress['in_progress'],
            'total': _crop_progress['total'],
            'completed': _crop_progress['completed'],
            'failed': _crop_progress['failed'],
            'active_jobs': list(_crop_progress['active_jobs'].values())
        })

@app.route('/api/crop-progress/reset', methods=['POST'])
def reset_crop_progress():
    """Clear the progress state after user closes notifications (V27)."""
    with _crop_lock:
        _crop_progress['total'] = 0
        _crop_progress['completed'] = 0
        _crop_progress['failed'] = []
        _crop_progress['active_jobs'] = {}
    return jsonify({'reset': True})

# ============================================================================
# DAY 8 (V7): DUPLICATE DETECTION
# ============================================================================

def _users_with_synced_folders():
    """Every user whose photos can legitimately be reconciled against a Drive
    folder: the admin always (get_root_folder_id falls back to the hardcoded
    default folder if they've never explicitly set one), plus anyone who has
    actually connected their own folder. A friend with no sync_settings row
    has no photos of their own to reconcile — their uploads/clips land in the
    ADMIN's folder (see upload_images), not theirs — so skipping them here
    isn't a gap, it's what keeps this from comparing their nonexistent folder
    against the admin's by way of get_root_folder_id's fallback."""
    conn = get_db()
    ids = {r[0] for r in conn.execute('SELECT DISTINCT user_id FROM sync_settings').fetchall()}
    conn.close()
    ids.add(1)
    return ids

def reconcile_drive_changes():
    """Self-heals every image whose Drive file no longer matches what the
    database thinks it looks like. One folder listing per user (each user's
    own folder — a friend's photos live in THEIR folder, not the admin's, so
    this must never assume a single shared listing), then for any image
    whose stored checksum differs from Drive's current one, the thumbnail,
    aspect ratio, fingerprint and palette are rebuilt from the current file.

    That mismatch is exactly what a crop leaves behind: the V27 background
    crop worker overwrote the Drive file but crashed before updating the row,
    so the grid kept showing the pre-crop thumbnail while the full-res
    inspector (which reads straight from Drive) showed the cropped image.
    Fixed going forward (V30), but already-affected rows still need this to
    catch up — so it runs both at boot (self-heals without Ryan needing to
    remember to open Duplicate Review) and as the first step of that scan.

    Never deletes anything — a file missing from a listing here just means
    "skip it," on purpose. Deletion-on-sync is a deliberately separate,
    explicit decision that lives in sync_folder_worker() instead, tied to
    the moment Ryan (or a friend) actually triggers a sync of their folder."""
    try:
        service = get_drive_service()
    except Exception as e:
        print(f"[reconcile] Drive reconciliation skipped: {e}")
        return

    backfilled = repaired = 0
    for user_id in _users_with_synced_folders():
        try:
            files = list_images_in_folder(service, get_root_folder_id(user_id))
            md5_map = {f['id']: f.get('md5Checksum') for f in files}
        except Exception as e:
            print(f"[reconcile] Could not list Drive folder for user {user_id}: {e}")
            continue

        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT id, user_id, drive_file_id, filename, md5_checksum '
                  'FROM images WHERE user_id = ?', (user_id,))
        rows = c.fetchall()
        conn.close()

        for r in rows:
            drive_md5 = md5_map.get(r['drive_file_id'])
            if not drive_md5 or drive_md5 == r['md5_checksum']:
                continue

            if r['md5_checksum'] is None:
                # Never had a checksum — just record it. The stored thumbnail
                # is still the right one, so nothing needs rebuilding.
                conn = get_db()
                conn.execute('UPDATE images SET md5_checksum = ? WHERE id = ?',
                             (drive_md5, r['id']))
                conn.commit()
                conn.close()
                backfilled += 1
                continue

            # The file in Drive changed under us (a crop). Rebuild from it.
            try:
                current_bytes = download_drive_file(service, r['drive_file_id'])
                thumbnail = generate_thumbnail(current_bytes)
                if not thumbnail:
                    raise ValueError('thumbnail could not be generated')
                conn = get_db()
                conn.execute('''UPDATE images
                    SET thumbnail_blob = ?, aspect_ratio = ?, md5_checksum = ?, phash = ?
                    WHERE id = ?''',
                    (thumbnail, get_image_aspect_ratio(current_bytes), drive_md5,
                     compute_phash(thumbnail), r['id']))
                conn.commit()
                conn.close()
                hexes = extract_palette(thumbnail)
                if hexes:
                    save_palette(r['id'], r['user_id'], hexes)
                repaired += 1
            except Exception as e:
                print(f"[reconcile] Could not refresh {r['filename']}: {e}")

    if backfilled or repaired:
        print(f"[reconcile] Recorded {backfilled} checksum(s); "
              f"refreshed {repaired} image(s) that changed in Drive.")

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
            save_palette(r['id'], r['user_id'], hexes)

    reconcile_drive_changes()

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
        'SELECT id, name, created_at, updated_at, share_token, invite_token, user_id '
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
        # The offline cache compares this against its saved copy to decide
        # whether to offer a refresh — leaving it out of the payload made the
        # frontend's "New changes" banner permanently dead.
        'updated_at': deck_row['updated_at'],
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
    Viewers get thumbnails only (they're embedded in the payload as data URIs);
    none of the full-res, edit, or delete endpoints check tokens, so a shared
    link exposes nothing beyond what this one endpoint returns."""
    conn = get_db()
    c = conn.cursor()
    deck_row = c.execute(
        'SELECT id, name, created_at, updated_at, share_token, user_id '
        'FROM decks WHERE share_token = ?', (token,)
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
    backfill_palettes()
    backfill_phashes()
    merge_plural_tag_duplicates()
    threading.Thread(target=reconcile_drive_changes, daemon=True).start()
    start_backup_scheduler()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

init_db()
load_embeddings_seed()
backfill_palettes()
backfill_phashes()
merge_plural_tag_duplicates()
threading.Thread(target=reconcile_drive_changes, daemon=True).start()
start_backup_scheduler()

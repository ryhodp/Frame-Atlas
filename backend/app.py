import os
import json
import base64
import io
import sqlite3
import time
import threading
import queue as queue_module
from datetime import datetime
from flask import Flask, jsonify, request, send_file, send_from_directory, Response, stream_with_context, redirect, session
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
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
# Only needed to hold the OAuth CSRF state between /login and /callback — a
# fresh secret each boot is fine since that round-trip finishes in seconds.
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or os.urandom(24)

DB_PATH = '/app/data/library.db'

# Gemini model — overridable via Railway env var if Google retires this one
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')

sync_state = {
    'in_progress': False,
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
    conn.close()

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
                        c.execute(
                            "INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, ?, ?)",
                            (img_id, category, val.strip())
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

def get_user_drive_service():
    """Drive client acting as the signed-in user (for uploads). Returns None
    if nobody has signed in yet."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT google_oauth_token FROM users WHERE id = 1')
    row = c.fetchone()
    conn.close()
    if not row or not row['google_oauth_token']:
        return None

    creds = UserCredentials.from_authorized_user_info(json.loads(row['google_oauth_token']), UPLOAD_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        conn = get_db()
        c = conn.cursor()
        c.execute('UPDATE users SET google_oauth_token = ? WHERE id = 1', (creds.to_json(),))
        conn.commit()
        conn.close()
    return build('drive', 'v3', credentials=creds)

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

def get_root_folder_id():
    """The Drive folder being synced — where _Removed lives."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT folder_id FROM sync_settings ORDER BY id DESC LIMIT 1')
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
        sync_state['processed'] = 0
        sync_state['total'] = 0
        sync_state['current_file'] = ''
        sync_state['errors'] = []

        service = get_drive_service()
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
        trigger_tagging()

# ============================================================================
# API ROUTES
# ============================================================================

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

@app.route('/api/debug', methods=['GET'])
def debug():
    creds = os.environ.get('GOOGLE_DRIVE_CREDENTIALS')
    return jsonify({
        'has_creds': creds is not None,
        'creds_length': len(creds) if creds else 0,
        'env_keys': list(os.environ.keys())
    })

@app.route('/api/debug/failed-images', methods=['GET'])
def debug_failed_images():
    """Temporary: list all images that failed tagging. Remove after investigation (Day 8)."""
    conn = get_db()
    c = conn.cursor()
    rows = c.execute('''
        SELECT id, filename, date_added FROM images
        WHERE tagging_status = 'failed'
        ORDER BY id ASC
    ''').fetchall()
    conn.close()
    return jsonify([{
        'id': r['id'],
        'filename': r['filename'],
        'date_added': r['date_added']
    } for r in rows])

@app.route('/api/tag/retry-failed', methods=['POST'])
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
    return jsonify({'app_name': 'Frame Atlas', 'version': 'V5', 'gemini_model': GEMINI_MODEL})

@app.route('/api/models', methods=['GET'])
def list_models():
    """Debug: list Gemini models this API key can use. Remove before production (Day 13)."""
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
def get_folders():
    return jsonify({'folders': [
        {'id': '1LHPVyo3QjOEcizc1Io2UVjxzX4FQ7yDG', 'name': 'Inspiration Images'}
    ]})

@app.route('/api/sync/settings', methods=['GET', 'POST'])
def sync_settings():
    user_id = request.args.get('user_id', 1)

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
    user_id = request.args.get('user_id', 1)

    if sync_state['in_progress']:
        return jsonify({'error': 'Sync already in progress'}), 400

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

    conn = get_db()
    c = conn.cursor()

    if active_chips:
        placeholders = ','.join('?' * len(active_chips))
        rows = c.execute(f'''
            SELECT t.value, t.category, COUNT(*) as cnt
            FROM tags t
            WHERE t.image_id IN (
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
        ''', active_chips + [len(active_chips), f'{q}%'] + active_chips).fetchall()
    else:
        rows = c.execute('''
            SELECT value, category, COUNT(*) as cnt
            FROM tags
            WHERE LOWER(value) LIKE ?
            GROUP BY value, category
            ORDER BY cnt DESC
            LIMIT 20
        ''', (f'{q}%',)).fetchall()

    conn.close()

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

    return jsonify([{
        'value': row['value'],
        'category': row['category'],
        'catLabel': CAT_LABELS.get(row['category'], row['category']),
        'color': CAT_COLORS.get(row['category'], '#9c988d'),
        'count': row['cnt']
    } for row in rows])

@app.route('/api/search')
def search():
    chips_raw = request.args.get('chips', '').strip()
    nl_raw = request.args.get('nl', '').strip()
    color_raw = request.args.get('color', '').strip()
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

    conn = get_db()
    c = conn.cursor()

    conditions = []
    params = []

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

    where = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''

    rows = c.execute(f'''
        SELECT id, filename, thumbnail_blob, caption, aspect_ratio, is_favorite, is_flagged
        FROM images {where}
        ORDER BY date_added DESC LIMIT ? OFFSET ?
    ''', params + [per, page * per]).fetchall()
    total = c.execute(f'SELECT COUNT(*) FROM images {where}', params).fetchone()[0]

    img_ids = [r['id'] for r in rows]
    tags_map = {}
    colors_map = {}

    if img_ids:
        ph = ','.join('?' * len(img_ids))
        for tr in c.execute(f'SELECT image_id, category, value FROM tags WHERE image_id IN ({ph})', img_ids).fetchall():
            tags_map.setdefault(tr['image_id'], []).append({'category': tr['category'], 'value': tr['value']})
        for cr in c.execute(f'SELECT image_id, hex FROM colors WHERE image_id IN ({ph}) ORDER BY rank ASC', img_ids).fetchall():
            colors_map.setdefault(cr['image_id'], []).append(cr['hex'])

    conn.close()

    images_out = []
    for r in rows:
        ar_str = r['aspect_ratio'] or '16:9'
        try:
            w, h = ar_str.split(':', 1) if ':' in ar_str else (ar_str, '1')
            ar_float = float(w) / float(h)
        except Exception:
            ar_float = 16 / 9

        thumb_b64 = base64.b64encode(r['thumbnail_blob']).decode('utf-8')
        images_out.append({
            'id': r['id'],
            'filename': r['filename'],
            'thumbnail': f'data:image/jpeg;base64,{thumb_b64}',
            'caption': r['caption'] or '',
            'aspect_ratio': ar_str,
            'ar_label': normalize_ar_label(ar_float),
            'ar_float': round(ar_float, 4),
            'is_favorite': bool(r['is_favorite']),
            'is_flagged': bool(r['is_flagged']),
            'tags': tags_map.get(r['id'], []),
            'palette': colors_map.get(r['id'], [])
        })

    return jsonify({'images': images_out, 'total': total, 'page': page, 'per': per, 'has_more': (page + 1) * per < total})

@app.route('/api/bookmarks', methods=['GET', 'POST'])
def bookmarks():
    user_id = request.args.get('user_id', 1)

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
    c.execute('DELETE FROM saved_searches WHERE id = ?', (bookmark_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/images', methods=['GET'])
def get_images():
    user_id = request.args.get('user_id', 1)
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT id, filename, thumbnail_blob, aspect_ratio, date_added, is_favorite, is_flagged
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
    c.execute('SELECT drive_file_id FROM images WHERE id = ?', (image_id,))
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

@app.route('/api/regenerate-thumbnails', methods=['POST'])
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
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT google_oauth_token FROM users WHERE id = 1')
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
    c.execute('UPDATE users SET google_oauth_token = ? WHERE id = 1', (flow.credentials.to_json(),))
    conn.commit()
    conn.close()
    return redirect('/?signed_in=1')

@app.route('/api/upload', methods=['POST'])
def upload_images():
    service = get_user_drive_service()
    if not service:
        return jsonify({'error': 'not_signed_in', 'message': 'Sign in with Google first.'}), 401

    force = request.args.get('force', '').lower() == 'true'
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No files provided'}), 400

    folder_id = get_root_folder_id()
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

@app.route('/api/images/<int:image_id>/favorite', methods=['POST'])
def toggle_favorite(image_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE images SET is_favorite = 1 - is_favorite WHERE id = ?', (image_id,))
    if c.rowcount == 0:
        conn.close()
        return jsonify({'error': 'Image not found'}), 404
    conn.commit()
    c.execute('SELECT is_favorite FROM images WHERE id = ?', (image_id,))
    val = c.fetchone()[0]
    conn.close()
    return jsonify({'success': True, 'is_favorite': val})

@app.route('/api/images/<int:image_id>/flag', methods=['POST'])
def toggle_flag(image_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE images SET is_flagged = 1 - is_flagged WHERE id = ?', (image_id,))
    if c.rowcount == 0:
        conn.close()
        return jsonify({'error': 'Image not found'}), 404
    conn.commit()
    c.execute('SELECT is_flagged FROM images WHERE id = ?', (image_id,))
    val = c.fetchone()[0]
    conn.close()
    return jsonify({'success': True, 'is_flagged': val})

@app.route('/api/images/<int:image_id>/tags', methods=['POST', 'DELETE'])
def edit_tags(image_id):
    data = request.get_json(force=True) or {}
    category = (data.get('category') or '').strip()
    value = (data.get('value') or '').strip().lower()
    if not category or not value:
        return jsonify({'error': 'category and value are required'}), 400

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

@app.route('/api/images/<int:image_id>/download')
def download_image(image_id):
    import mimetypes
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT drive_file_id, filename FROM images WHERE id = ?', (image_id,))
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
        removed_id = get_or_create_removed_folder(service, get_root_folder_id())
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
            files = list_images_in_folder(service, get_root_folder_id())
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
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

init_db()

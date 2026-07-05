import os
import json
import base64
import io
import sqlite3
import time
import threading
import queue as queue_module
from datetime import datetime
from flask import Flask, jsonify, request, send_file, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from PIL import Image
import google.auth.transport.requests
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google import genai as genai_client

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)

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

ONLY use tags from these allowed lists. Leave arrays empty [] if not applicable.

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
        scopes=['https://www.googleapis.com/auth/drive.readonly']
    )
    return build('drive', 'v3', credentials=credentials)

def list_images_in_folder(service, folder_id, page_token=None):
    images = []
    query = f"'{folder_id}' in parents and trashed=false"
    results = service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name, mimeType, size), nextPageToken',
        pageSize=100,
        pageToken=page_token
    ).execute()

    items = results.get('files', [])
    for item in items:
        if item['mimeType'] == 'application/vnd.google-apps.folder':
            images.extend(list_images_in_folder(service, item['id']))
        elif item['mimeType'] in ['image/jpeg', 'image/png', 'image/webp', 'image/gif']:
            images.append(item)

    if 'nextPageToken' in results:
        images.extend(list_images_in_folder(service, folder_id, results['nextPageToken']))

    return images

def generate_thumbnail(image_data, width=600, quality=75):
    try:
        img = Image.open(io.BytesIO(image_data))
        aspect_ratio = img.width / img.height if img.height > 0 else 1
        height = int(width / aspect_ratio)
        img = img.resize((width, height), Image.Resampling.LANCZOS)
        thumb_io = io.BytesIO()
        img.save(thumb_io, format='JPEG', quality=quality)
        thumb_io.seek(0)
        return thumb_io.getvalue()
    except Exception:
        return None

def get_image_aspect_ratio(image_data):
    try:
        img = Image.open(io.BytesIO(image_data))
        width, height = img.size
        from math import gcd
        g = gcd(width, height)
        return f"{width // g}:{height // g}"
    except Exception:
        return "16:9"

def extract_palette(image_data, num_colors=5):
    try:
        img = Image.open(io.BytesIO(image_data)).convert('RGB')
        img.thumbnail((100, 100))
        quantized = img.quantize(colors=num_colors)
        palette = quantized.getpalette()
        color_counts = sorted(quantized.getcolors(), reverse=True)
        hexes = []
        for count, idx in color_counts[:num_colors]:
            r, g, b = palette[idx * 3: idx * 3 + 3]
            hexes.append(f'#{r:02x}{g:02x}{b:02x}')
        return hexes
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
                    INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status)
                    VALUES (?, ?, ?, ?, ?, 'pending')
                ''', (user_id, file_id, filename, thumbnail, aspect_ratio))
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
        # Small library — compute color matches in Python
        threshold = 2200
        matched_ids = set()
        for row in c.execute('SELECT DISTINCT image_id, hex FROM colors').fetchall():
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
    """Backfill palettes from stored thumbnails — no Drive downloads needed."""
    conn = get_db()
    c = conn.cursor()
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

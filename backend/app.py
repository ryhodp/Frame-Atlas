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
import google.generativeai as genai

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)

DB_PATH = '/app/data/library.db'

# Global state for sync progress
sync_state = {
    'in_progress': False,
    'processed': 0,
    'total': 0,
    'current_file': '',
    'errors': []
}

# Global state for tagging progress
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

    # Safe migration: add tagging_status column if it doesn't exist yet
    # (for existing databases that were created before Day 4)
    try:
        c.execute("ALTER TABLE images ADD COLUMN tagging_status TEXT DEFAULT 'pending'")
        conn.commit()
        print("[migration] Added tagging_status column")
    except Exception:
        pass  # Column already exists, that's fine

    # Mark any images that already have tags as 'done'
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

    genai.configure(api_key=gemini_api_key)
    model = genai.GenerativeModel('gemini-1.5-flash-latest')

    conn = get_db()
    c = conn.cursor()

    # Untagged first, then failed — skip done
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
            # Load image from blob
            pil_img = Image.open(io.BytesIO(thumb_blob))

            # Call Gemini with the image
            response = model.generate_content([GEMINI_TAGGING_PROMPT, pil_img])
            raw = response.text.strip()

            # Strip markdown fences if Gemini adds them
            if raw.startswith('```'):
                raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()

            data = json.loads(raw)

            conn = get_db()
            c = conn.cursor()

            # Write tags
            c.execute("DELETE FROM tags WHERE image_id = ?", (img_id,))
            for category, values in data.get('tags', {}).items():
                for val in values:
                    if val and val.strip():
                        c.execute(
                            "INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, ?, ?)",
                            (img_id, category, val.strip())
                        )

            # Write caption
            caption = data.get('caption', '')
            if caption:
                c.execute("UPDATE images SET caption = ? WHERE id = ?", (caption, img_id))

            # Write filmography if present
            film = data.get('filmography', {})
            if any(film.get(k) for k in ['title', 'director', 'dp', 'year']):
                c.execute("DELETE FROM filmography WHERE image_id = ?", (img_id,))
                c.execute(
                    "INSERT INTO filmography (image_id, title, director, dp, year) VALUES (?,?,?,?,?)",
                    (img_id, film.get('title'), film.get('director'), film.get('dp'), str(film.get('year', '')))
                )

            # Mark done
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
        time.sleep(0.05)  # small pause to avoid rate limiting

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
    """Start the tagging job in a background thread. Safe to call multiple times."""
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

def list_drive_folders():
    try:
        service = get_drive_service()
        query = "mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)',
            pageSize=100
        ).execute()
        folders = results.get('files', [])
        return sorted(folders, key=lambda x: x['name'])
    except Exception as e:
        return []

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

def generate_thumbnail(image_data, max_size=(400, 400)):
    try:
        img = Image.open(io.BytesIO(image_data))
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        thumb_io = io.BytesIO()
        img.save(thumb_io, format='JPEG', quality=85)
        thumb_io.seek(0)
        return thumb_io.getvalue()
    except Exception as e:
        return None

def get_image_aspect_ratio(image_data):
    try:
        img = Image.open(io.BytesIO(image_data))
        width, height = img.size
        # Return as a clean ratio string e.g. "16:9"
        from math import gcd
        g = gcd(width, height)
        return f"{width // g}:{height // g}"
    except Exception:
        return "16:9"

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

                # Download image
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
                conn.commit()
                conn.close()

                new_count += 1
                sync_state['processed'] += 1

            except Exception as e:
                sync_state['errors'].append(f"{filename}: {str(e)}")
                sync_state['processed'] += 1
                continue

        # Update last sync time
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
        # Auto-trigger tagging after sync finishes
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
    return jsonify({'app_name': 'Frame Atlas', 'version': 'V4'})

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

# ── Tagging progress: live stream (SSE) ──────────────────────────────────────

@app.route('/api/tag-progress/stream')
def tag_progress_stream():
    def generate():
        q = queue_module.Queue(maxsize=50)
        with _sse_lock:
            _sse_queues.append(q)
        try:
            # Send current state immediately on connect
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

# ── Tagging progress: snapshot (non-streaming) ────────────────────────────────

@app.route('/api/tag-progress')
def tag_progress_snapshot():
    with _tag_progress_lock:
        data = dict(_tag_progress)
    pct = int(data['done'] / data['total'] * 100) if data['total'] > 0 else 0
    return jsonify({**data, 'pct': pct})

# ── Autocomplete ──────────────────────────────────────────────────────────────

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
        'mood': '#8b7cf6',
        'lighting_quality': '#f59e0b',
        'lighting_color_temperature': '#f97316',
        'color_palette': '#ec4899',
        'shot_type': '#06b6d4',
        'framing_composition': '#10b981',
        'location_type': '#84cc16',
        'time_of_day_weather': '#c9a253',
        'source_type': '#6366f1',
        'subject_count': '#94a3b8',
        'subject_camera_relationship': '#a78bfa',
        'genre_aesthetic': '#f43f5e',
        'era_decade': '#fb923c',
        'camera_format': '#22d3ee',
        'performance_emotion': '#e879f9',
    }
    CAT_LABELS = {
        'mood': 'Mood',
        'lighting_quality': 'Lighting',
        'lighting_color_temperature': 'Color Temp',
        'color_palette': 'Palette',
        'shot_type': 'Shot',
        'framing_composition': 'Framing',
        'location_type': 'Location',
        'time_of_day_weather': 'Time / Weather',
        'source_type': 'Source',
        'subject_count': 'Subjects',
        'subject_camera_relationship': 'Camera Rel.',
        'genre_aesthetic': 'Genre',
        'era_decade': 'Era',
        'camera_format': 'Format',
        'performance_emotion': 'Emotion',
    }

    return jsonify([{
        'value': row['value'],
        'category': row['category'],
        'catLabel': CAT_LABELS.get(row['category'], row['category']),
        'color': CAT_COLORS.get(row['category'], '#9c988d'),
        'count': row['cnt']
    } for row in rows])

# ── Search / filter ───────────────────────────────────────────────────────────

@app.route('/api/search')
def search():
    chips_raw = request.args.get('chips', '').strip()
    page = int(request.args.get('page', 0))
    per = int(request.args.get('per', 50))

    active_chips = [t.strip() for t in chips_raw.split(',') if t.strip()] if chips_raw else []

    conn = get_db()
    c = conn.cursor()

    if not active_chips:
        rows = c.execute('''
            SELECT id, filename, thumbnail_blob, caption, aspect_ratio, is_favorite, is_flagged
            FROM images
            ORDER BY date_added DESC
            LIMIT ? OFFSET ?
        ''', (per, page * per)).fetchall()
        total = c.execute('SELECT COUNT(*) FROM images').fetchone()[0]
    else:
        placeholders = ','.join('?' * len(active_chips))
        rows = c.execute(f'''
            SELECT id, filename, thumbnail_blob, caption, aspect_ratio, is_favorite, is_flagged
            FROM images
            WHERE id IN (
                SELECT image_id FROM tags
                WHERE value IN ({placeholders})
                GROUP BY image_id
                HAVING COUNT(DISTINCT value) = ?
            )
            ORDER BY date_added DESC
            LIMIT ? OFFSET ?
        ''', active_chips + [len(active_chips), per, page * per]).fetchall()
        total = c.execute(f'''
            SELECT COUNT(*) FROM images
            WHERE id IN (
                SELECT image_id FROM tags
                WHERE value IN ({placeholders})
                GROUP BY image_id
                HAVING COUNT(DISTINCT value) = ?
            )
        ''', active_chips + [len(active_chips)]).fetchone()[0]

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
            if ':' in ar_str:
                w, h = ar_str.split(':', 1)
                ar_float = float(w) / float(h)
            else:
                ar_float = float(ar_str)
        except Exception:
            ar_float = 16 / 9

        # Convert blob to base64 for the frontend
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

    return jsonify({
        'images': images_out,
        'total': total,
        'page': page,
        'per': per,
        'has_more': (page + 1) * per < total
    })

# ── Original images endpoint (kept for backwards compat) ──────────────────────

@app.route('/api/images', methods=['GET'])
def get_images():
    user_id = request.args.get('user_id', 1)
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT id, filename, thumbnail_blob, aspect_ratio, date_added, is_favorite, is_flagged
        FROM images
        WHERE user_id = ?
        ORDER BY date_added DESC
    ''', (user_id,))

    images = []
    for row in c.fetchall():
        thumb_b64 = base64.b64encode(row[2]).decode('utf-8')
        images.append({
            'id': row[0],
            'filename': row[1],
            'thumbnail': f'data:image/jpeg;base64,{thumb_b64}',
            'aspect_ratio': row[3],
            'date_added': row[4],
            'is_favorite': row[5],
            'is_flagged': row[6]
        })

    conn.close()
    return jsonify({'images': images})

# ── Static / catch-all ────────────────────────────────────────────────────────

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

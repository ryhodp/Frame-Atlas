"""routes_sync.py — getting photos INTO a library, as a Flask Blueprint (Day 42 / Phase 3).

Folder sync (/api/sync/start, /status, /settings, /connect-folder,
/api/folders), the in-app uploader (/api/upload, admin-only, always the
admin's library) and the browser-extension clipper (/api/clip, any user,
their own library since V86). The heavy lifting lives in sync.py
(sync_folder_worker, _ingest_image, _load_existing_phashes) — these are the
thin HTTP wrappers around it.

Every route body is character-for-character what it was in app.py.
"""
import base64
import concurrent.futures
import io
import os
import re
import threading
import urllib.parse
from datetime import datetime

from flask import Blueprint, jsonify, request, session
from PIL import Image

from core import get_db, admin_required
from drive import BULK_DELETE_WORKERS
import drive
import gemini
import sync
import tagging

bp = Blueprint('sync_routes', __name__)


@bp.route('/api/folders', methods=['GET'])
@admin_required
def get_folders():
    return jsonify({'folders': [
        {'id': '1LHPVyo3QjOEcizc1Io2UVjxzX4FQ7yDG', 'name': 'Inspiration Images'}
    ]})

@bp.route('/api/sync/settings', methods=['GET', 'POST'])
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

@bp.route('/api/sync/connect-folder', methods=['POST'])
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

@bp.route('/api/sync/start', methods=['POST'])
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

@bp.route('/api/sync/status', methods=['GET'])
def sync_status():
    # One sync runs at a time app-wide. Only the person whose sync it is
    # (or the admin) sees filenames/errors — another user just learns the
    # slot is busy, not what's in someone else's Drive folder. (V17)
    uid = session['user_id']
    if sync.sync_state['user_id'] in (None, uid) or uid == 1:
        return jsonify({**sync.sync_state, 'yours': sync.sync_state['user_id'] in (None, uid)})
    return jsonify({'in_progress': sync.sync_state['in_progress'], 'yours': False,
                    'processed': 0, 'total': 0, 'current_file': '', 'errors': []})

@bp.route('/api/upload', methods=['POST'])
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
    existing = sync._load_existing_phashes(1)  # uploads always land in the admin's library

    # One Drive service per worker thread, not one shared across all workers
    # or rebuilt per photo — building it is cheap (no network call), and
    # threading.local keeps each worker's httplib2 transport from colliding.
    thread_local = threading.local()
    def _get_thread_service():
        if not hasattr(thread_local, 'service'):
            thread_local.service = drive.get_user_drive_service(1)
        return thread_local.service

    def ingest_one(f):
        """Ingest one file using a thread-local Drive service."""
        return sync._ingest_image(
            _get_thread_service(), folder_id, f.read(), f.filename, f.mimetype,
            existing, force=force
        )

    # Process files 5 at a time to parallelize Drive I/O — faster than sequential.
    # (Within-batch dedup is sacrificed for speed; the next sync will catch any
    # true duplicates, and it's rare to upload the exact same photo twice in
    # one batch.)
    results = []
    if len(files) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=BULK_DELETE_WORKERS) as pool:
            results = list(pool.map(ingest_one, files))
    else:
        # Single file: skip thread overhead
        results = [ingest_one(f) for f in files]

    if any(r['status'] == 'uploaded' for r in results):
        tagging.trigger_tagging()

    return jsonify({'results': results})

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

@bp.route('/api/clip', methods=['POST'])
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

    # V86: dedupe against — and file the clip under — the CLIPPER's own
    # library. Since V25 friends clip into their own Drive folder, but the row
    # was always written as user 1 (so a friend's clip showed up in Ryan's
    # grid, never theirs) and the duplicate check compared against every
    # library (so a friend could be shown a thumbnail of Ryan's photo).
    existing = sync._load_existing_phashes(user_id)
    result = sync._ingest_image(
        service, drive.get_root_folder_id(user_id), image_data,
        _clip_filename(source_url, mimetype), mimetype,
        existing, force=force, source_url=source_url, user_id=user_id,
    )

    if result['status'] == 'uploaded':
        # Same handoff as the folder sync (sync.py): admin rides the shared
        # key; a friend only auto-tags if they've saved their own.
        if user_id == 1:
            tagging.trigger_tagging()
        elif gemini.get_user_gemini_key(user_id):
            tagging.trigger_tagging(user_id=user_id)
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

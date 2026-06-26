import os
import json
import base64
import io
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from PIL import Image
import google.auth.transport.requests
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import google.generativeai as genai
import threading

app = Flask(__name__, static_folder='static', static_url_path='/static')
CORS(app)

# Global state for sync progress
sync_state = {
    'in_progress': False,
    'processed': 0,
    'total': 0,
    'current_file': '',
    'errors': []
}

# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def get_db():
    """Get database connection"""
    conn = sqlite3.connect('library.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database with all tables from PRD"""
    conn = get_db()
    c = conn.cursor()
    
    # Users table
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
    
    # Images table
    c.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            drive_file_id TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            thumbnail_blob BLOB NOT NULL,
            caption TEXT,
            aspect_ratio TEXT,
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_favorite INTEGER DEFAULT 0,
            is_flagged INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # Tags table
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
    
    # Colors table
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
    
    # Embeddings table (for CLIP vectors - Day 7)
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
    
    # Filmography table
    c.execute('''
        CREATE TABLE IF NOT EXISTS filmography (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER NOT NULL,
            title TEXT,
            director TEXT,
            dp TEXT,
            year INTEGER,
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')
    
    # Saved searches table
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
    
    # Decks table
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
    
    # Scenes table
    c.execute('''
        CREATE TABLE IF NOT EXISTS scenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            sort_order INTEGER,
            FOREIGN KEY (deck_id) REFERENCES decks(id)
        )
    ''')
    
    # Deck images (join table)
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
    
    # Sync settings table (stores which folder to sync)
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
    conn.close()

# ============================================================================
# GOOGLE DRIVE & SYNC FUNCTIONS
# ============================================================================

def get_drive_service():
    """Initialize Google Drive service using service account credentials"""
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
    """List all folders in user's Drive root"""
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
    """Recursively list all images in a folder and its subfolders"""
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
        # If it's a folder, recurse
        if item['mimeType'] == 'application/vnd.google-apps.folder':
            images.extend(list_images_in_folder(service, item['id']))
        # If it's an image, add it
        elif item['mimeType'] in ['image/jpeg', 'image/png', 'image/webp', 'image/gif']:
            images.append(item)
    
    # Handle pagination
    if 'nextPageToken' in results:
        images.extend(list_images_in_folder(service, folder_id, results['nextPageToken']))
    
    return images

def generate_thumbnail(image_data, max_size=(200, 200)):
    """Generate thumbnail and return as BLOB"""
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
    """Get aspect ratio from image dimensions"""
    try:
        img = Image.open(io.BytesIO(image_data))
        width, height = img.size
        return f"{width}:{height}"
    except:
        return None

def sync_folder_worker(folder_id, user_id):
    """Background worker that syncs images from a Drive folder"""
    global sync_state
    
    try:
        sync_state['in_progress'] = True
        sync_state['processed'] = 0
        sync_state['total'] = 0
        sync_state['current_file'] = ''
        sync_state['errors'] = []
        
        service = get_drive_service()
        
        # Get all images from folder
        print(f"Listing images in folder {folder_id}...")
        all_images = list_images_in_folder(service, folder_id)
        sync_state['total'] = len(all_images)
        
        # Check which ones we've already synced
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT drive_file_id FROM images WHERE user_id = ?', (user_id,))
        existing_ids = set(row[0] for row in c.fetchall())
        conn.close()
        
        # Download and process new images
        for idx, image in enumerate(all_images):
            try:
                file_id = image['id']
                filename = image['name']
                
                # Skip if already synced
                if file_id in existing_ids:
                    sync_state['processed'] += 1
                    continue
                
                sync_state['current_file'] = filename
                
                # Download image
                request = service.files().get_media(fileId=file_id)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                
                image_data = fh.getvalue()
                
                # Generate thumbnail
                thumbnail = generate_thumbnail(image_data)
                if not thumbnail:
                    sync_state['errors'].append(f"Failed to generate thumbnail for {filename}")
                    sync_state['processed'] += 1
                    continue
                
                # Get aspect ratio
                aspect_ratio = get_image_aspect_ratio(image_data)
                
                # Store in database
                conn = get_db()
                c = conn.cursor()
                c.execute('''
                    INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, file_id, filename, thumbnail, aspect_ratio))
                conn.commit()
                conn.close()
                
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
        
    except Exception as e:
        sync_state['errors'].append(f"Sync failed: {str(e)}")
    finally:
        sync_state['in_progress'] = False

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok'})

@app.route('/api/config', methods=['GET'])
def config():
    """Get app configuration"""
    return jsonify({
        'app_name': 'Frame Atlas',
        'version': 'V2'
    })

@app.route('/api/folders', methods=['GET'])
def get_folders():
    """List all folders in Drive"""
    try:
        folders = list_drive_folders()
        return jsonify({'folders': folders})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sync/settings', methods=['GET', 'POST'])
def sync_settings():
    """Get or set sync folder settings"""
    user_id = request.args.get('user_id', 1)  # For now, single user (user_id=1)
    
    if request.method == 'GET':
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT folder_id, folder_name, last_sync FROM sync_settings WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        conn.close()
        
        if row:
            return jsonify({
                'folder_id': row[0],
                'folder_name': row[1],
                'last_sync': row[2]
            })
        else:
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
            c.execute('''
                UPDATE sync_settings SET folder_id = ?, folder_name = ?
                WHERE user_id = ?
            ''', (folder_id, folder_name, user_id))
        else:
            c.execute('''
                INSERT INTO sync_settings (user_id, folder_id, folder_name)
                VALUES (?, ?, ?)
            ''', (user_id, folder_id, folder_name))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})

@app.route('/api/sync/start', methods=['POST'])
def start_sync():
    """Start a sync in background"""
    user_id = request.args.get('user_id', 1)
    
    if sync_state['in_progress']:
        return jsonify({'error': 'Sync already in progress'}), 400
    
    # Get folder ID from settings
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT folder_id FROM sync_settings WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return jsonify({'error': 'No sync folder configured'}), 400
    
    folder_id = row[0]
    
    # Start sync in background thread
    thread = threading.Thread(target=sync_folder_worker, args=(folder_id, user_id))
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'message': 'Sync started'})

@app.route('/api/sync/status', methods=['GET'])
def sync_status():
    """Get current sync status"""
    return jsonify(sync_state)

@app.route('/api/images', methods=['GET'])
def get_images():
    """Get all images for the user"""
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
        # Convert blob to base64 for JSON
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

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    # Don't intercept API calls
    if path.startswith('api/'):
        from flask import abort
        abort(404)
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    full_path = os.path.join(static_dir, path)
    if path and os.path.exists(full_path):
        return send_from_directory(static_dir, path)
    return send_from_directory(static_dir, 'index.html')

# ============================================================================
# INITIALIZATION
# ============================================================================

if __name__ == '__main__':
    init_db()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

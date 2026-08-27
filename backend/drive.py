"""Google Drive connection, auth, and folder-listing layer.

Day 29 (Phase 3): split out of app.py. This is the layer sync, the monthly
backup, the crop worker, and upload all sit on top of.

Standing rules for this phase (see CLAUDE.md "Backend module split — Phase 3"):
  1. This module imports from core.py and the Google client libraries ONLY —
     never from app.py (that would be a circular import).
  2. Call sites in app.py are qualified (`drive.get_drive_service()`), and the
     ~11 test scripts that swap these functions for fakes patch them on the
     drive module (`mod.drive.get_drive_service = fake`).

NOTE: MediaIoBaseDownload is imported here because download_drive_file() uses
it, but it is ALSO still imported in app.py — the sync worker, full-image
stream, thumbnail regen, and download-image route all use it directly and did
not move this session. MediaIoBaseUpload did not move at all (nothing here
uploads). Do not fold either into "the Drive module owns the Google upload
helpers" — they belong wherever the code using them lives.
"""

import os
import re
import io
import json

from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

from core import get_db


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
    user 1). Returns None if that user hasn't connected Google yet, OR
    (V46) if their connection has died and needs reconnecting — see below."""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT google_oauth_token FROM users WHERE id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if not row or not row['google_oauth_token']:
        return None

    creds = UserCredentials.from_authorized_user_info(json.loads(row['google_oauth_token']), UPLOAD_SCOPES)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            # invalid_grant: Token has been expired or revoked. This is
            # Google's doing, not a bug here — the most common cause is the
            # OAuth consent screen still sitting in "Testing" publishing
            # status in Google Cloud Console, which caps every refresh token
            # at 7 days no matter how often the app is used (fix: Console ->
            # OAuth consent screen -> Publishing status -> In production).
            # Before this, every caller kept retrying against the same dead
            # token forever and surfacing Google's raw JSON blob wherever it
            # happened to be caught (a crop's error toast, the monthly DB
            # backup log) — and /api/account/google-status kept reporting
            # "signed_in" since it only ever checked the column for NULL, so
            # there was no visible signal telling anyone to reconnect.
            # Clearing the token here makes every caller treat this exactly
            # like "never connected", which already degrades correctly
            # everywhere (each call site already null-checks the result) —
            # reconnecting in Settings is the fix either way.
            print(f"[auth] Google token for user {user_id} expired or was revoked — "
                  f"cleared, reconnect required: {e}")
            conn = get_db()
            c = conn.cursor()
            c.execute('UPDATE users SET google_oauth_token = NULL WHERE id = ?', (user_id,))
            conn.commit()
            conn.close()
            return None
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
        except Exception as parse_err:
            # Returning None is correct — callers already handle "reason
            # unknown" — but a Drive error whose body we couldn't even parse
            # is worth seeing, since every caller's error handling gets less
            # specific from here (V44/Day 26).
            print(f"[drive] Could not parse error reason from HttpError body: {parse_err}")
    return None

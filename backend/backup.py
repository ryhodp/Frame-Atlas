"""
Frame Atlas — monthly database-snapshot-to-Drive backup (V27).

Phase 3, Day 33: lifted verbatim out of app.py. The images themselves already
live on Drive — the SQLite database is the ONLY copy of the tags, decks,
bookmarks, and filmography built on top of them, and it lives solely on
Railway's volume. This uploads a gzipped snapshot to a `_Backups` folder once a
month and keeps only the newest KEEP_BACKUP_COUNT.

Every function body here is byte-for-byte the app.py original (diffed against
HEAD). Imports `get_db` / `db_path` from core and `import drive` for the Drive
client + root-folder lookup — nothing from app.py (that would be a circular
import). `MediaIoBaseUpload` is imported here AND still in app.py (crop worker +
upload route use it directly) — same both-files pattern as `MediaIoBaseDownload`
in drive.py (Day 29).

The two Flask routes that expose this (`/api/backups/status`, `/api/backups/run`)
stay in app.py and call `backup.run_db_backup()` / `backup.KEEP_BACKUP_COUNT`
qualified — routes don't move until the Day 36 blueprint work.
"""

import io
import gzip
import sqlite3
import time
import threading
from datetime import datetime

from googleapiclient.http import MediaIoBaseUpload

from core import get_db, db_path
import drive

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
        backup_service = drive.get_user_drive_service(1)
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
        src = sqlite3.connect(db_path())
        dst = sqlite3.connect(':memory:')
        with dst:
            src.backup(dst)
        src.close()
        db_bytes = dst.serialize()
        dst.close()

        compressed = gzip.compress(db_bytes)
        stamp = datetime.now().strftime('%Y-%m-%d')
        filename = f'library-backup-{stamp}.db.gz'

        root_id = drive.get_root_folder_id(1)
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

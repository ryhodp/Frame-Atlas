"""
Frame Atlas — Google Drive folder sync, library ingest, and Drive reconciliation.

Phase 3, Day 35: lifted verbatim out of app.py — the last big worker domain to
move. Every function body here is a byte-for-byte copy of the app.py original
(diffed against HEAD); nothing inside them changed, because each name they call
(get_db, drive.*, tagging.*, gemini.*, images_common.*, the imaging/fingerprint/
colour helpers) was already either a bare import or already qualified in the
original file, exactly as it needs to be here too.

This module comes last in Phase 3 precisely because it depends on nearly
everything else that already moved: core.py (get_db), drive.py (service +
folder listing + download), tagging.py (the post-sync auto-tag handoff),
gemini.py (does this friend have their own key?), images_common.py
(save_palette), fingerprint.py (phash + signature duplicate gates), colors.py
(palette extraction + overlap), imaging.py (thumbnail + aspect ratio). Never on
app.py, which would be a circular import.

Three separate concerns live here, related but distinct:

1. **`sync_folder_worker()`** — the folder sync a user actually triggers. Adds
   what's new in Drive, and (V30) removes rows whose Drive file is gone.
2. **`_ingest_image()` / `_load_existing_phashes()`** — putting ONE image into
   the library, shared by `/api/upload` and `/api/clip`. Note these are NOT
   called by the folder sync; they're the upload/clip path. They live here
   because they're the same family of work (get a photo into the library) and
   share nearly all of the same imports.
3. **`reconcile_drive_changes()` / `_users_with_synced_folders()`** — the
   unattended boot-time self-heal for images whose Drive file changed under us.

**The V30 half-the-library guard in `sync_folder_worker()` is load-bearing and
moved verbatim.** A partial or failed Drive listing is indistinguishable from a
real mass-deletion, so if more than half the library would vanish in one pass
the deletes are skipped and reported instead. This is the difference between
"one stale row cleaned up" and "silently wiped every tag Ryan ever wrote."
Never soften it, and never let the cascade table list drift from the tables
that actually reference images.id.

**`reconcile_drive_changes()` never deletes anything** — a file missing from
its listing is skipped on purpose. Deletion-on-sync is the deliberately
separate, explicit decision that lives in `sync_folder_worker()`, tied to the
moment someone actually triggers a sync of their own folder.

`sync_state` lives here even though the `/api/regenerate-thumbnails` route also
borrows it for its own progress reporting and its "something's already running"
lock (pre-existing behaviour, not introduced by this split). That route reads it
qualified as `sync.sync_state`. The dict is only ever mutated in place, never
rebound, so every reader — routes here and in app.py, and the test scripts —
always sees the same live object.

The Flask routes that expose all of this (`/api/sync/start`, `/api/sync/status`,
`/api/upload`, `/api/clip`, `/api/duplicates/scan`, …) stay in app.py, same as
every other Phase 3 split; routes don't move until the Day 36+ blueprint work.
"""

import base64
import io

from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from core import get_db
import drive
import gemini
import images_common
import tagging
from colors import extract_palette, palettes_overlap
from fingerprint import (
    PHASH_NEAR_DUP_THRESHOLD,
    compute_phash, phash_distance,
    compute_signature, signatures_match,
)
from imaging import generate_thumbnail, get_image_aspect_ratio


sync_state = {
    'in_progress': False,
    'user_id': None,  # whose sync is running — one sync at a time, app-wide (Day 14 Stage 2)
    'processed': 0,
    'total': 0,
    'current_file': '',
    'errors': [],
    'new_count': 0,      # V48: how many were actually new, for the completion toast
    'removed_count': 0,  # V48: how many were removed (deleted from Drive), same reason
}



def sync_folder_worker(folder_id, user_id):
    global sync_state
    try:
        sync_state['in_progress'] = True
        sync_state['user_id'] = user_id
        sync_state['processed'] = 0
        sync_state['total'] = 0
        sync_state['current_file'] = ''
        sync_state['errors'] = []
        sync_state['new_count'] = 0
        sync_state['removed_count'] = 0

        # EVERYONE syncs through the shared service account (V17). Friends
        # share their folder with the service account's email (same as Ryan
        # did on Day 2) — their own Google sign-in stays drive.file-scoped,
        # which can only see files the app itself created, so it could never
        # read a pre-existing folder. Verified against Google's docs before
        # abandoning the OAuth read path: picking a folder in the Google
        # Picker grants access to the folder itself, NOT the files inside it.
        service = drive.get_drive_service()
        print(f"Listing images in folder {folder_id}...")
        try:
            all_images = drive.list_images_in_folder(service, folder_id)
        except Exception as e:
            msg = str(e)
            if '404' in msg or 'notFound' in msg or '403' in msg or 'insufficient' in msg.lower():
                sync_state['errors'].append(
                    'Frame Atlas can\'t see that folder — make sure it\'s shared with '
                    f'{drive.get_service_account_email() or "the Frame Atlas robot email"} (Share → Viewer), then try again.')
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
            if user_id != 1 and library_count + new_count >= drive.PERSONAL_LIBRARY_CAP:
                sync_state['errors'].append(
                    f'Stopped at the {drive.PERSONAL_LIBRARY_CAP}-image limit — the rest of the '
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
                    images_common.save_palette(new_image_id, user_id, hexes)

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

        sync_state['new_count'] = new_count
        sync_state['removed_count'] = removed_count

        print(f"Sync complete. {new_count} new images added"
              + (f", {removed_count} removed (deleted from Drive)" if removed_count else "") + ".")

    except Exception as e:
        sync_state['errors'].append(f"Sync failed: {str(e)}")
    finally:
        # Auto-tagging after sync: admin rides the shared key (Day 5). A
        # friend's photos auto-tag too, but ONLY if they've saved their own
        # Gemini key (V16) — scoped to just their images, on their key, so
        # it can never spend the admin's budget. Keyless friends' photos sit
        # untagged (searchable by filename, zero cost) until they add one.
        #
        # V48: called BEFORE sync_state['in_progress'] flips false, not
        # after. trigger_tagging() now resolves "is there anything to tag"
        # synchronously (see its own docstring), so by the time in_progress
        # goes false, _tag_progress already reflects the real answer — the
        # Home page's background sync-then-tag toast watches exactly that
        # flip and needs it to be trustworthy the instant it sees it,
        # without guessing via a timing delay.
        if user_id == 1:
            tagging.trigger_tagging()
        elif gemini.get_user_gemini_key(user_id):
            tagging.trigger_tagging(user_id=user_id)
        sync_state['in_progress'] = False


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
            images_common.save_palette(new_id, 1, new_palette)
        existing.append({'id': new_id, 'filename': filename,
                         'thumbnail_blob': thumbnail, 'phash': img_phash,
                         'colors': new_palette, 'signature': new_signature})

    return {'filename': filename, 'status': 'uploaded', 'image_id': new_id}


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
        service = drive.get_drive_service()
    except Exception as e:
        print(f"[reconcile] Drive reconciliation skipped: {e}")
        return

    backfilled = repaired = 0
    for user_id in _users_with_synced_folders():
        try:
            files = drive.list_images_in_folder(service, drive.get_root_folder_id(user_id))
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
                current_bytes = drive.download_drive_file(service, r['drive_file_id'])
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
                    images_common.save_palette(r['id'], r['user_id'], hexes)
                repaired += 1
            except Exception as e:
                print(f"[reconcile] Could not refresh {r['filename']}: {e}")

    if backfilled or repaired:
        print(f"[reconcile] Recorded {backfilled} checksum(s); "
              f"refreshed {repaired} image(s) that changed in Drive.")

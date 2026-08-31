"""
Frame Atlas — background crop-job queue and worker (V27; perspective V32).

Phase 3, Day 34: lifted verbatim out of app.py. `_process_crop_jobs()` is a
byte-for-byte copy of the app.py original (diffed against HEAD) — nothing in
its body changed, because every name it calls (get_db, drive.*, the imaging/
fingerprint/colour helpers, images_common.save_palette) was already either a
bare import or already qualified in the original file, exactly as it needs to
be here too.

Multiple users can have crops running "in parallel" in the sense that jobs
queue up instantly and the UI never blocks — there is still only ONE worker
thread pulling one job at a time off `_crop_queue`, on purpose: Drive's own
per-file `files().update()` is not something you'd want two threads racing on
the same image anyway, and a single worker keeps the destructive-write tail
below dead simple to reason about.

**The destructive-write tail (download → crop → back up original to `_Removed`
FIRST → overwrite the live file → refresh thumbnail/aspect_ratio/md5/phash/
palette) moved as ONE inseparable block.** This is deliberate and load-bearing:
the V27 disaster was a crop worker that overwrote Drive and only THEN tried to
refresh the DB, using column names that never existed — so the write threw
after Drive already had the cropped pixels, leaving Drive right and the DB
stale. Never split the backup step from the overwrite step, and never let the
DB refresh diverge from what actually got written to Drive.

Depends on `core.py` (get_db), `drive.py` (Drive service + folder helpers),
`perspective.py` (V32 quad-to-rectangle correction), `imaging.py` (thumbnail +
aspect ratio), `fingerprint.py` (phash), `colors.py` (palette extraction), and
`images_common.py` (save_palette) — never on app.py, which would be a circular
import.

The two Flask routes that expose progress (`GET /api/crop-progress`,
`POST /api/crop-progress/reset`) stay in app.py, same as every other Phase 3
split — they're read/reset wrappers over `crop._crop_progress` /
`crop._crop_lock`, qualified. `crop_image()` (the route that queues a job)
also stays in app.py; only its `_crop_queue.put(...)` / counter-increment call
sites are now qualified against this module.
"""

import io
import threading
import queue as queue_module
from datetime import datetime

from PIL import Image, ImageOps
from googleapiclient.http import MediaIoBaseUpload

from core import get_db
import drive
import images_common
from colors import extract_palette
from fingerprint import compute_phash
from imaging import generate_thumbnail, get_image_aspect_ratio
from perspective import parse_perspective_corners, perspective_is_whole_image, perspective_correct

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

                service = drive.get_drive_service()

                # Download original
                original_bytes = drive.download_drive_file(service, old_file_id)
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
                backup_service = drive.get_user_drive_service(owner_id) or drive.get_user_drive_service(1)
                if backup_service is None:
                    raise ValueError('No connected Google account for backup')

                removed_id = drive.get_or_create_removed_folder(service, drive.get_root_folder_id(owner_id))
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
                    images_common.save_palette(image_id, owner_id, hexes)

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

def start_crop_worker():
    """Start the background crop worker thread (daemon, dies with the app)."""
    threading.Thread(target=_process_crop_jobs, daemon=True).start()

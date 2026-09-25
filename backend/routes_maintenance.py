"""routes_maintenance.py — admin library-maintenance routes as a Flask Blueprint (Day 40 / Phase 3).

Admin-only tools that sweep the whole library rather than act on one photo:
Duplicate Review (/api/duplicates, the background /api/duplicates/scan and
its /scan-progress), /api/regenerate-thumbnails and /api/extract-colors.
Every route body is character-for-character what it was in app.py.

Duplicate Review compares ONE library at a time (V86): the scanning user's.
/api/regenerate-thumbnails borrows sync.sync_state for its progress and its
"something's already running" lock (pre-existing, documented in sync.py).
"""
import base64
import io
import threading

from flask import Blueprint, jsonify, request
from googleapiclient.http import MediaIoBaseDownload

from core import get_db, admin_required, current_user_id
from colors import extract_palette, palettes_overlap
from fingerprint import (
    PHASH_HEX_LEN, PHASH_NEAR_DUP_THRESHOLD, compute_phash, phash_distance,
    compute_signature, signatures_match,
)
from imaging import generate_thumbnail
import drive
import images_common
import sync

bp = Blueprint('maintenance', __name__)


@bp.route('/api/regenerate-thumbnails', methods=['POST'])
@admin_required
def regenerate_thumbnails():
    def _regenerate_job():
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute('SELECT id, user_id, drive_file_id FROM images ORDER BY id DESC')
            images = c.fetchall()
            conn.close()

            sync.sync_state['total'] = len(images)
            sync.sync_state['processed'] = 0

            service = drive.get_drive_service()
            for img in images:
                try:
                    sync.sync_state['current_file'] = f"regenerating #{img['id']}"
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
                            images_common.save_palette(img['id'], img['user_id'], hexes)
                except Exception as e:
                    print(f"[regenerate] Failed {img['id']}: {e}")
                sync.sync_state['processed'] += 1
            print("[regenerate] All thumbnails updated")
        finally:
            sync.sync_state['in_progress'] = False

    if sync.sync_state['in_progress']:
        return jsonify({'error': 'Sync already in progress'}), 400

    sync.sync_state['in_progress'] = True
    thread = threading.Thread(target=_regenerate_job, daemon=True)
    thread.start()

    return jsonify({'success': True, 'message': 'Thumbnail regeneration started'})

@bp.route('/api/extract-colors', methods=['POST'])
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
            images_common.save_palette(img['id'], img['user_id'], hexes)
            count += 1

    return jsonify({'success': True, 'extracted': count, 'skipped': len(images) - count})

def _compute_duplicate_groups(rows, palette_map, on_progress=None):
    """The actual O(images²) comparison. Pulled out of find_duplicates() so
    the background scan job (which reports progress) and the plain
    synchronous GET route (which doesn't need to) share one implementation —
    two copies of this logic WILL drift the moment one changes.

    `on_progress(processed, total)`, if given, is called once per outer-loop
    index — cheap enough to call unconditionally (an image's inner loop
    against every later image is the expensive part, not this one callback),
    and fine-grained enough that a progress bar watching it climbs steadily
    rather than jumping in big chunks.
    """
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
        if on_progress:
            on_progress(i + 1, n)

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

    return groups


# Background duplicate-scan progress — same shape/spirit as _crop_progress /
# _tag_progress / sync_state: one shared dict, one lock, a plain polling GET
# route rather than SSE (matching crop/sync, the simpler of the two existing
# patterns — tagging's SSE stream is the outlier and isn't needed here).
_dup_scan_lock = threading.Lock()
_dup_scan_progress = {
    'active': False,
    'phase': None,       # 'fingerprints' | 'palettes' | 'reconcile' | 'comparing' | 'done' | None
    'processed': 0,
    'total': 0,
    'groups': None,       # populated once phase == 'done'
    'error': None,
}


def _set_dup_progress(**kwargs):
    with _dup_scan_lock:
        _dup_scan_progress.update(kwargs)


def _run_duplicate_scan_job(owner_id):
    """Runs in a background thread. Mirrors duplicates_scan()'s old
    self-heal-then-compare sequence exactly — only the moment each step
    reports progress is new, not what any step actually does.

    V86: `owner_id` is whoever clicked the scan. Steps 1-3 (fingerprint /
    palette backfill, Drive reconcile) stay library-wide on purpose — they
    only repair stored data and show nobody anything. Step 4, the comparison
    itself, looks at ONLY that person's photos."""
    try:
        # 1. Fingerprints — missing ones, and (V30) ones still at the old 8x8
        #    width, rebuilt from the stored thumbnail.
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT id, thumbnail_blob FROM images WHERE phash IS NULL OR LENGTH(phash) != ?',
                  (PHASH_HEX_LEN,))
        missing_phash = c.fetchall()
        _set_dup_progress(phase='fingerprints', processed=0, total=len(missing_phash))
        for idx, r in enumerate(missing_phash):
            ph = compute_phash(r['thumbnail_blob'])
            if ph:
                c.execute('UPDATE images SET phash = ? WHERE id = ?', (ph, r['id']))
            _set_dup_progress(processed=idx + 1)
        conn.commit()

        # 2. Colour palettes — missing ones, likewise from the thumbnail.
        c.execute('''
            SELECT id, user_id, thumbnail_blob FROM images
            WHERE id NOT IN (SELECT DISTINCT image_id FROM colors)
        ''')
        missing_palette = c.fetchall()
        conn.close()
        _set_dup_progress(phase='palettes', processed=0, total=len(missing_palette))
        for idx, r in enumerate(missing_palette):
            hexes = extract_palette(r['thumbnail_blob'])
            if hexes:
                images_common.save_palette(r['id'], r['user_id'], hexes)
            _set_dup_progress(processed=idx + 1)

        # 3. Drive reconciliation — also runs at boot, but re-running it here
        #    means a click on this scan always reflects Drive's CURRENT
        #    state. No fine-grained progress inside it (out of scope here),
        #    so this phase is reported as one lump step.
        _set_dup_progress(phase='reconcile', processed=0, total=1)
        sync.reconcile_drive_changes()
        _set_dup_progress(processed=1)

        # 4. The actual comparison — the dominant cost for a large library,
        #    and the one phase worth a real per-image percentage.
        #
        #    V86: scoped to the scanning user's own library. It used to compare
        #    every library, so a friend's copy of an image Ryan also had could
        #    land in his Duplicate Review group — pre-ticked for deletion
        #    (every photo but the first is), one click from being deleted out
        #    of the friend's library.
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            SELECT id, filename, thumbnail_blob, md5_checksum, phash, date_added, aspect_ratio
            FROM images WHERE user_id = ? ORDER BY date_added ASC
        ''', (owner_id,))
        rows = c.fetchall()
        c.execute('SELECT image_id, hex, share FROM colors WHERE user_id = ?', (owner_id,))
        palette_map = {}
        for r in c.fetchall():
            palette_map.setdefault(r['image_id'], []).append((r['hex'], r['share']))
        conn.close()

        _set_dup_progress(phase='comparing', processed=0, total=len(rows))
        groups = _compute_duplicate_groups(
            rows, palette_map,
            on_progress=lambda done, total: _set_dup_progress(processed=done, total=total)
        )

        _set_dup_progress(phase='done', active=False, groups=groups, error=None)
    except Exception as e:
        print(f"[duplicates] background scan failed: {e}")
        _set_dup_progress(phase='done', active=False, groups=None, error=str(e))


@bp.route('/api/duplicates/scan', methods=['POST'])
@admin_required
def duplicates_scan():
    """Starts the self-heal-then-compare duplicate scan in the background
    and returns immediately — poll GET /api/duplicates/scan-progress for
    live progress and the final groups. Replaces the old synchronous
    version, which could leave the caller waiting with zero feedback for
    however long a full library comparison took."""
    with _dup_scan_lock:
        if _dup_scan_progress['active']:
            return jsonify({'already_running': True})
        _dup_scan_progress.update({
            'active': True, 'phase': None, 'processed': 0, 'total': 0,
            'groups': None, 'error': None,
        })
    threading.Thread(target=_run_duplicate_scan_job, args=(current_user_id(),), daemon=True).start()
    return jsonify({'started': True})


@bp.route('/api/duplicates/scan-progress', methods=['GET'])
@admin_required
def duplicates_scan_progress():
    with _dup_scan_lock:
        return jsonify(dict(_dup_scan_progress))


@bp.route('/api/duplicates', methods=['GET'])
@admin_required
def find_duplicates():
    """Plain synchronous duplicate check — no self-heal, no progress
    reporting. Kept for any direct caller that wants results in one request
    against whatever fingerprints/palettes already exist; the Find
    Duplicates button uses the background /api/duplicates/scan instead.

    V86: scoped to the caller's own library, same as the background scan."""
    uid = current_user_id()
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT id, filename, thumbnail_blob, md5_checksum, phash, date_added, aspect_ratio
        FROM images WHERE user_id = ? ORDER BY date_added ASC
    ''', (uid,))
    rows = c.fetchall()
    c.execute('SELECT image_id, hex, share FROM colors WHERE user_id = ?', (uid,))
    palette_map = {}
    for r in c.fetchall():
        palette_map.setdefault(r['image_id'], []).append((r['hex'], r['share']))
    conn.close()

    groups = _compute_duplicate_groups(rows, palette_map)
    return jsonify({'groups': groups, 'count': len(groups)})

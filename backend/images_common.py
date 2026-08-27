"""images_common.py — image hydration + boot-time self-heal (Day 31 / Phase 3).

The helpers that turn a raw `images` row into the rich JSON object the frontend
receives, plus the palette writer and the four boot-time backfills that keep old
rows consistent with newer algorithms. Shared today by search, decks,
similar-images, the utility views, sync, upload, clip and the crop worker — which
is exactly why it lives in one place: an image object that drifted between two
routes would be worse than a missing feature.

Every function here is character-for-character what it was in app.py — only its
home changed (favorite_col, which _fetch_image_dict leans on, moved to core.py in
the same session).

Phase 3 rules: imports only from core.py and the already-pure V45 maths modules
(colors / fingerprint / imaging), never from app.py. app.py does
`import images_common` and qualifies every call site.
"""
import base64
import threading

from core import get_db, favorite_col, normalize_tag_value
from colors import PALETTE_VERSION, extract_palette
from fingerprint import PHASH_GRID, PHASH_HEX_LEN, compute_phash
from imaging import normalize_ar_label, ar_float_from_str


def build_image_dict(row, tags, palette, filmography, public=False):
    """Turns one `images` row (must include id, filename, thumbnail_blob,
    caption, aspect_ratio, is_favorite, md5_checksum, and — V39 —
    camera_rig, lens, lens_filter, stop, onset_notes) into the JSON shape
    used by /api/search, /api/images/<id>/similar, and the decks endpoints.
    Keep everything on this single helper so image objects can never drift
    apart between routes.

    V39's 5 fields are plain `images` columns (unlike tags/palette/
    filmography, which come from joined tables and need their own queries),
    so `notes` is read straight off `row` here instead of being threaded
    through as a fifth function argument across every call site.

    V43 (Day 25): `public=True` is the one exception that still embeds the
    thumbnail as base64 — used only for the anonymous /api/share/<token>
    view, which has no login to gate a cacheable URL behind. Every other
    caller gets a small `/api/images/<id>/thumb` URL instead of a base64
    blob buried in JSON, which the browser can actually cache. The `?v=`
    is the image's own checksum, so a crop (which changes it) forces a
    fresh fetch and nothing else does."""
    ar_str = row['aspect_ratio'] or '16:9'
    ar_float = ar_float_from_str(ar_str)

    if public:
        thumb_b64 = base64.b64encode(row['thumbnail_blob']).decode('utf-8')
        thumbnail = f'data:image/jpeg;base64,{thumb_b64}'
    else:
        version = row['md5_checksum'] or row['id']
        thumbnail = f"/api/images/{row['id']}/thumb?v={version}"

    return {
        'id': row['id'],
        'filename': row['filename'],
        'thumbnail': thumbnail,
        'caption': row['caption'] or '',
        'aspect_ratio': ar_str,
        'ar_label': normalize_ar_label(ar_float),
        'ar_float': round(ar_float, 4),
        'is_favorite': bool(row['is_favorite']),
        'tags': tags,
        'palette': palette,
        'filmography': filmography,
        'notes': {
            'camera_rig': row['camera_rig'],
            'lens': row['lens'],
            'lens_filter': row['lens_filter'],
            'stop': row['stop'],
            'onset_notes': row['onset_notes'],
        }
    }

def hydrate_image_rows(c, rows):
    """Given a list of `images` rows (each must include the columns
    build_image_dict needs), bulk-fetch their tags, palettes, and filmography
    in three queries and return finished image dicts. Shared by /api/search
    and the Day 13 utility views so their payloads can never drift apart."""
    img_ids = [r['id'] for r in rows]
    tags_map = {}
    colors_map = {}
    film_map = {}

    if img_ids:
        ph = ','.join('?' * len(img_ids))
        for tr in c.execute(f'SELECT image_id, category, value FROM tags WHERE image_id IN ({ph})', img_ids).fetchall():
            tags_map.setdefault(tr['image_id'], []).append({'category': tr['category'], 'value': tr['value']})
        for cr in c.execute(f'SELECT image_id, hex FROM colors WHERE image_id IN ({ph}) ORDER BY rank ASC', img_ids).fetchall():
            colors_map.setdefault(cr['image_id'], []).append(cr['hex'])
        for fr in c.execute(f'SELECT image_id, title, director, dp, year FROM filmography WHERE image_id IN ({ph})', img_ids).fetchall():
            film_map[fr['image_id']] = {
                'title': fr['title'], 'director': fr['director'],
                'dp': fr['dp'], 'year': fr['year']
            }

    return [
        build_image_dict(r, tags_map.get(r['id'], []), colors_map.get(r['id'], []), film_map.get(r['id']))
        for r in rows
    ]

def _fetch_image_dict(c, image_id, owner_user_id, public=False):
    """Loads one images row plus its tags/palette/filmography and runs it
    through build_image_dict(). Used by the decks endpoints, which need the
    same image JSON shape as /api/search and /api/images/<id>/similar but
    are fetching images one at a time (via deck_images), not in bulk.
    is_favorite reflects the deck OWNER (not the viewer — the public share
    view has no logged-in viewer at all).

    `public` passes straight through to build_image_dict() — see its
    docstring (V43/Day 25)."""
    row = c.execute(f'''
        SELECT id, filename, thumbnail_blob, caption, aspect_ratio, md5_checksum,
               camera_rig, lens, lens_filter, stop, onset_notes, {favorite_col(owner_user_id)}
        FROM images WHERE id = ?
    ''', (image_id,)).fetchone()
    if not row:
        return None

    tags = [
        {'category': tr['category'], 'value': tr['value']}
        for tr in c.execute('SELECT category, value FROM tags WHERE image_id = ?', (image_id,)).fetchall()
    ]
    palette = [
        cr['hex'] for cr in
        c.execute('SELECT hex FROM colors WHERE image_id = ? ORDER BY rank ASC', (image_id,)).fetchall()
    ]
    fr = c.execute(
        'SELECT title, director, dp, year FROM filmography WHERE image_id = ?', (image_id,)
    ).fetchone()
    filmography = {'title': fr['title'], 'director': fr['director'], 'dp': fr['dp'], 'year': fr['year']} if fr else None

    return build_image_dict(row, tags, palette, filmography, public=public)


def save_palette(image_id, user_id, entries):
    """entries: list of (hex, share) from extract_palette. Bare hex strings are
    still accepted (share stored NULL) so any older caller keeps working."""
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM colors WHERE image_id = ?', (image_id,))
    for rank, entry in enumerate(entries):
        if isinstance(entry, (tuple, list)):
            hex_color, share = entry[0], entry[1]
        else:
            hex_color, share = entry, None
        c.execute(
            'INSERT INTO colors (image_id, user_id, hex, rank, share, palette_version)'
            ' VALUES (?, ?, ?, ?, ?, ?)',
            (image_id, user_id, hex_color, rank, share, PALETTE_VERSION)
        )
    conn.commit()
    conn.close()


def backfill_palettes():
    """Self-heal any palette older than PALETTE_VERSION (V24 shares, V33 merge
    fix). Rebuilding from the stored thumbnails costs no Drive or Gemini calls
    and runs at roughly 20ms an image.

    V24 gave every entry a `share`; rows from before that have share NULL.
    V33 stopped shadow bins from donating their share to a colour family, so
    every palette stored before it OVERSTATES how much of the frame its warm
    colours cover — the numbers are wrong, not merely missing, which is why
    this keys off a version stamp instead of a NULL check.

    Runs in a background thread so a large library can't delay boot, and
    self-disables: once every row is stamped current the query finds nothing
    and this returns immediately on all later boots."""
    try:
        conn = get_db()
        # `share IS NULL` is redundant against a correctly-stamped row —
        # save_palette() always writes both together — but it keeps the V24
        # guarantee that no share-less row can survive a boot, even if some
        # future code path writes one without a version.
        pending = [r['image_id'] for r in conn.execute(
            'SELECT DISTINCT image_id FROM colors'
            ' WHERE palette_version IS NULL OR palette_version < ? OR share IS NULL',
            (PALETTE_VERSION,)
        ).fetchall()]
        conn.close()
    except Exception as e:
        print(f"[palette-backfill] Could not check for pending rows: {e}")
        return

    if not pending:
        return

    def _job():
        done = failed = unrepairable = 0
        for image_id in pending:
            try:
                conn = get_db()
                row = conn.execute(
                    'SELECT id, user_id, thumbnail_blob FROM images WHERE id = ?', (image_id,)
                ).fetchone()
                conn.close()
                # No thumbnail, or one Pillow can't read, means this palette can
                # never be rebuilt — and it will be retried on every future boot
                # forever. Count it out loud: silently skipping is what made the
                # pre-V33 blind spot invisible in the logs.
                if not row or not row['thumbnail_blob']:
                    unrepairable += 1
                    continue
                entries = extract_palette(row['thumbnail_blob'])
                if not entries:
                    unrepairable += 1
                    continue
                save_palette(row['id'], row['user_id'], entries)
                done += 1
            except Exception as e:
                failed += 1
                print(f"[palette-backfill] Image {image_id} failed: {e}")
        print(f"[palette-backfill] Rebuilt {done} palette(s) to v{PALETTE_VERSION}"
              + (f", {failed} failed" if failed else "")
              + (f", {unrepairable} unrepairable (no usable thumbnail)"
                 if unrepairable else "")
              + ". Colour search is now reading corrected coverage.")

    print(f"[palette-backfill] {len(pending)} palette(s) older than "
          f"v{PALETTE_VERSION} — rebuilding in background.")
    threading.Thread(target=_job, daemon=True).start()


def backfill_phashes():
    """V30 one-time self-heal: rebuild fingerprints that predate the 16x16
    widening.

    Pre-V30 hashes are 16 hex characters (64 bits); V30 ones are 64 (256
    bits). phash_distance() reports mismatched lengths as maximally different
    rather than XOR-ing them into a meaningless number, so until a row is
    rebuilt it simply never matches anything — duplicate detection degrades
    to "finds nothing", never to "finds the wrong thing".

    Rebuilding from the stored thumbnails costs no Drive or Gemini calls.
    Runs in a background thread so a large library can't delay boot, and
    self-disables once every row is the new width."""
    try:
        conn = get_db()
        pending = [r['id'] for r in conn.execute(
            'SELECT id FROM images WHERE phash IS NOT NULL AND LENGTH(phash) != ?',
            (PHASH_HEX_LEN,)
        ).fetchall()]
        conn.close()
    except Exception as e:
        print(f"[phash-backfill] Could not check for pending rows: {e}")
        return

    if not pending:
        return

    def _job():
        done = failed = 0
        for image_id in pending:
            try:
                conn = get_db()
                row = conn.execute(
                    'SELECT id, thumbnail_blob FROM images WHERE id = ?', (image_id,)
                ).fetchone()
                conn.close()
                if not row or not row['thumbnail_blob']:
                    continue
                ph = compute_phash(row['thumbnail_blob'])
                if ph:
                    conn = get_db()
                    conn.execute('UPDATE images SET phash = ? WHERE id = ?', (ph, image_id))
                    conn.commit()
                    conn.close()
                    done += 1
            except Exception as e:
                failed += 1
                print(f"[phash-backfill] Image {image_id} failed: {e}")
        print(f"[phash-backfill] Rebuilt {done} fingerprint(s) at {PHASH_GRID}x{PHASH_GRID}"
              + (f", {failed} failed" if failed else "") + ".")

    print(f"[phash-backfill] {len(pending)} fingerprint(s) predate V30 — rebuilding in background.")
    threading.Thread(target=_job, daemon=True).start()

def backfill_notes_fts():
    """V39 one-time self-heal: seed notes_fts for every image that predates
    the AFTER INSERT trigger (every photo synced/uploaded/clipped before this
    shipped). Unlike the palette/phash backfills above, there's no Pillow
    work per image — just copying 5 text columns — so this is one set-based
    SQL statement run inline at boot, not a per-image Python loop in a
    background thread. Self-disables: once every images.id has a matching
    notes_fts rowid, the INSERT affects zero rows on every later boot."""
    try:
        conn = get_db()
        cur = conn.execute('''
            INSERT INTO notes_fts (rowid, camera_rig, lens, lens_filter, stop, onset_notes)
            SELECT id, camera_rig, lens, lens_filter, stop, onset_notes FROM images
            WHERE id NOT IN (SELECT rowid FROM notes_fts)
        ''')
        seeded = cur.rowcount
        conn.commit()
        conn.close()
        if seeded:
            print(f"[notes-fts-backfill] Seeded {seeded} image(s) into notes_fts.")
    except Exception as e:
        print(f"[notes-fts-backfill] Failed: {e}")

def merge_plural_tag_duplicates():
    """V30 one-time cleanup: collapse existing plural/singular tag drift
    (e.g. an image tagged 'cars' and another tagged 'car' for the same
    subject) now that normalize_tag_value() stops new drift at write time.
    This is what fixes what's already in the database; the write-time
    normalization only prevents new drift, it can't retroactively fix rows
    written before it existed.

    Only ever merges tags that already coexist on the same photo, in the
    same category — never touches two different photos' tags, and never
    crosses categories (an image tagged 'car' under location_type and
    'car' under subjects are two different facts and stay separate).

    Runs once at boot; self-disables once nothing's left to merge (each
    later boot's initial SELECT finds no drift and returns immediately)."""
    try:
        conn = get_db()
        rows = conn.execute('SELECT id, image_id, category, value FROM tags').fetchall()
        conn.close()
    except Exception as e:
        print(f"[tag-merge] Could not scan tags: {e}")
        return

    by_image_cat = {}
    for r in rows:
        by_image_cat.setdefault((r['image_id'], r['category']), []).append(r)

    to_delete = []      # tag ids to remove outright
    to_rename = []      # (tag id, new value)
    for group in by_image_cat.values():
        buckets = {}
        for r in group:
            buckets.setdefault(normalize_tag_value(r['value']), []).append(r)
        for normalized, variants in buckets.items():
            if len(variants) == 1:
                if variants[0]['value'] != normalized:
                    to_rename.append((variants[0]['id'], normalized))
                continue
            # Multiple rows collapse onto the same normalized value on this
            # photo (e.g. both 'car' and 'cars'). Keep one — preferring a row
            # already spelled the normalized way — delete the rest.
            keeper = next((r for r in variants if r['value'] == normalized), variants[0])
            if keeper['value'] != normalized:
                to_rename.append((keeper['id'], normalized))
            to_delete.extend(r['id'] for r in variants if r['id'] != keeper['id'])

    if not to_delete and not to_rename:
        return

    def _job():
        try:
            conn = get_db()
            for tag_id, new_value in to_rename:
                conn.execute('UPDATE tags SET value = ? WHERE id = ?', (new_value, tag_id))
            for tag_id in to_delete:
                conn.execute('DELETE FROM tags WHERE id = ?', (tag_id,))
            conn.commit()
            conn.close()
            print(f"[tag-merge] Renamed {len(to_rename)} tag(s), removed "
                  f"{len(to_delete)} duplicate(s) left behind by the merge.")
        except Exception as e:
            print(f"[tag-merge] Failed: {e}")

    print(f"[tag-merge] {len(to_rename)} tag(s) need renaming, {len(to_delete)} duplicate(s) "
          "to remove — merging in background.")
    threading.Thread(target=_job, daemon=True).start()

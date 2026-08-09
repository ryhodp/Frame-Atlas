#!/usr/bin/env python3
"""Day 22 (V40): local checks for the PDF lookbook export.

Exercises backend/pdf_export.py directly (no Flask, no Drive, no network):
builds a throwaway SQLite DB with the real deck/scene/deck_images/images
schema, fills it with REAL JPEG thumbnails at genuinely different aspect
ratios so letterboxing is tested in both orientations, buckets the rows the
same way the endpoint does, and renders both layouts.

Run:  scripts/.venv/bin/python scripts/test_pdf_export_locally.py
"""

import io
import os
import re
import sys
import sqlite3
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend'))

from PIL import Image, ImageDraw  # noqa: E402

from pdf_export import (  # noqa: E402
    build_deck_pdf,
    pdf_download_name,
    sanitize_filename,
    PAGE_W,
    PAGE_H,
    GRID_COLS,
    GRID_ROWS,
)

# Where the two eyeball-me sample PDFs land. Defaults to the system temp dir so
# this runs anywhere; override with PDF_SAMPLE_DIR to drop them somewhere handy.
OUT_DIR = os.environ.get('PDF_SAMPLE_DIR', tempfile.gettempdir())

passed = 0
failed = 0


def check(label, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print(f'  PASS  {label}')
    else:
        failed += 1
        print(f'  FAIL  {label}  {detail}')


# ---------------------------------------------------------------------------
# Fixtures: real JPEGs at real aspect ratios
# ---------------------------------------------------------------------------

RATIOS = {
    'scope':    (956, 400),   # 2.39:1
    'widescreen': (800, 450),  # 16:9
    'vertical': (450, 800),   # 9:16
    'square':   (600, 600),   # 1:1
}


def make_jpeg(kind, tone):
    """A real, decodable JPEG with low-frequency shading (never flat)."""
    w, h = RATIOS[kind]
    img = Image.new('RGB', (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        f = y / float(max(h - 1, 1))
        d.line([(0, y), (w, y)],
               fill=(int(tone[0] * (0.35 + 0.65 * f)),
                     int(tone[1] * (0.35 + 0.65 * f)),
                     int(tone[2] * (0.35 + 0.65 * f))))
    d.ellipse([w * 0.3, h * 0.3, w * 0.7, h * 0.7], outline=(255, 255, 255), width=4)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=85)
    return buf.getvalue()


REAL_SCHEMA = '''
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user'
);
CREATE TABLE images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    filename TEXT,
    thumbnail_blob BLOB,
    caption TEXT,
    aspect_ratio REAL,
    camera_rig TEXT,
    lens TEXT,
    lens_filter TEXT,
    stop TEXT,
    onset_notes TEXT
);
CREATE TABLE decks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    share_token TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    invite_token TEXT,
    updated_at TIMESTAMP
);
CREATE TABLE scenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    sort_order INTEGER
);
CREATE TABLE deck_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL,
    scene_id INTEGER,
    image_id INTEGER NOT NULL,
    storyboard_order INTEGER,
    storyboard_note TEXT
);
'''


def build_fixture_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(REAL_SCHEMA)
    c = conn.cursor()
    c.execute("INSERT INTO users (id, username, password_hash, role) VALUES (1, 'ryan', 'x', 'admin')")

    specs = [
        ('scope_night.jpg', make_jpeg('scope', (90, 110, 160))),
        ('wide_dusk.jpg', make_jpeg('widescreen', (190, 130, 70))),
        ('vertical_alley.jpg', make_jpeg('vertical', (70, 150, 120))),
        ('square_portrait.jpg', make_jpeg('square', (200, 90, 90))),
        ('scope_second.jpg', make_jpeg('scope', (140, 140, 140))),
        ('wide_second.jpg', make_jpeg('widescreen', (110, 90, 170))),
        ('vertical_second.jpg', make_jpeg('vertical', (180, 180, 90))),
        ('corrupt.jpg', b'\xff\xd8\xff\xe0 this is not a jpeg at all'),
    ]
    for name, blob in specs:
        c.execute('INSERT INTO images (user_id, filename, thumbnail_blob) VALUES (1, ?, ?)', (name, blob))

    c.execute("INSERT INTO decks (id, user_id, name) VALUES (1, 1, 'Night Market / Ep 2')")
    # sort_order deliberately out of insertion order — export must honour it.
    c.execute("INSERT INTO scenes (id, deck_id, name, sort_order) VALUES (10, 1, 'INT. Diner - Night', 1)")
    c.execute("INSERT INTO scenes (id, deck_id, name, sort_order) VALUES (11, 1, 'EXT. Rooftop - Dawn', 0)")
    c.execute("INSERT INTO scenes (id, deck_id, name, sort_order) VALUES (12, 1, 'EMPTY SCENE', 2)")

    rows = [
        # (scene_id, image_id, storyboard_order, note)
        (11, 1, 0, 'Wide establishing — 2.39, hard backlight from camera left.'),
        (11, 2, 1, None),                       # no note -> no caption band
        (11, 3, 2, 'Vertical insert for socials.'),
        (10, 4, None, 'Unordered row, sorts last within the scene.'),
        (10, 5, 0, 'Push in on the counter.'),
        (10, 6, 1, None),
        (10, 8, 2, 'This one has a corrupt thumbnail and must be skipped.'),
        (None, 7, 0, 'Unsorted staging photo.'),
        (None, 1, 1, None),                     # same image twice in one deck
    ]
    for scene_id, image_id, order, note in rows:
        c.execute(
            'INSERT INTO deck_images (deck_id, scene_id, image_id, storyboard_order, storyboard_note) '
            'VALUES (1, ?, ?, ?, ?)', (scene_id, image_id, order, note)
        )
    # Points at a deleted image row: the endpoint's JOIN drops it.
    c.execute('INSERT INTO deck_images (deck_id, scene_id, image_id, storyboard_order) VALUES (1, 10, 999, 9)')
    conn.commit()
    return conn


def load_sections(conn, deck_id=1, include_unsorted=True):
    """Same bucketing the endpoint does."""
    c = conn.cursor()
    scene_rows = c.execute(
        'SELECT id, name FROM scenes WHERE deck_id = ? ORDER BY sort_order ASC, id ASC', (deck_id,)
    ).fetchall()
    photo_rows = c.execute('''
        SELECT di.id AS deck_image_id, di.scene_id, di.storyboard_note,
               i.filename, i.thumbnail_blob
        FROM deck_images di
        JOIN images i ON i.id = di.image_id
        WHERE di.deck_id = ?
        ORDER BY CASE WHEN di.storyboard_order IS NULL THEN 1 ELSE 0 END,
                 di.storyboard_order ASC, di.id ASC
    ''', (deck_id,)).fetchall()
    buckets = {}
    for row in photo_rows:
        buckets.setdefault(row['scene_id'], []).append(dict(row))
    sections = [{'name': s['name'], 'images': buckets.get(s['id'], [])} for s in scene_rows]
    if include_unsorted and buckets.get(None):
        sections.append({'name': None, 'images': buckets[None]})
    return sections


PAGE_RE = re.compile(rb'/Type\s*/Page[^s]')


def page_count(data):
    return len(PAGE_RE.findall(data))


def pdf_text(data):
    """Uncompressed strings reportlab wrote — enough to spot a title card."""
    return data


# ---------------------------------------------------------------------------

def main():
    tmp = tempfile.mkdtemp(prefix='fa_pdf_')
    db_path = os.path.join(tmp, 'library.db')
    conn = build_fixture_db(db_path)
    os.makedirs(OUT_DIR, exist_ok=True)

    print('\n--- section bucketing (mirrors the endpoint) ---')
    sections = load_sections(conn)
    names = [s['name'] for s in sections]
    check('scenes come back in sort_order, Unsorted last',
          names == ['EXT. Rooftop - Dawn', 'INT. Diner - Night', 'EMPTY SCENE', None], names)
    check('empty scene has zero photos', sections[2]['images'] == [])
    diner = [r['filename'] for r in sections[1]['images']]
    check('NULL storyboard_order sorts last within a scene',
          diner[-1] == 'square_portrait.jpg', diner)
    check('missing image row dropped by the JOIN',
          all(r['filename'] for r in sections[1]['images']) and len(diner) == 4, diner)
    check('same image can appear twice in one deck',
          [r['filename'] for r in sections[3]['images']] == ['vertical_second.jpg', 'scope_night.jpg'])

    print('\n--- layout=full ---')
    full = build_deck_pdf({'id': 1, 'name': 'Night Market / Ep 2'}, sections, layout='full').read()
    check('non-empty output', len(full) > 5000, f'{len(full)} bytes')
    check("starts with the %PDF- magic bytes", full.startswith(b'%PDF-'), full[:8])
    check('ends with EOF marker', b'%%EOF' in full[-64:])
    # Usable photos: Rooftop 3 + Diner 3 (corrupt skipped, dead FK dropped)
    # + Unsorted 2 = 8, across 3 non-empty sections (EMPTY SCENE emits nothing).
    # 1 title page + 3 scene cards + 8 photo pages = 12.
    check('page count = title + 3 scene cards + 8 photos = 12',
          page_count(full) == 12, page_count(full))
    check('landscape letter page geometry', round(PAGE_W) == 792 and round(PAGE_H) == 612,
          (PAGE_W, PAGE_H))

    print('\n--- layout=grid ---')
    grid = build_deck_pdf({'id': 1, 'name': 'Night Market / Ep 2'}, sections, layout='grid').read()
    check('non-empty output', len(grid) > 5000, f'{len(grid)} bytes')
    check("starts with the %PDF- magic bytes", grid.startswith(b'%PDF-'), grid[:8])
    # 6 per page: Rooftop 3 -> 1pg, Diner 3 -> 1pg, Unsorted 2 -> 1pg, + title
    check('page count = title + 3 section pages = 4', page_count(grid) == 4, page_count(grid))
    check('grid is fewer pages than full for the same deck', page_count(grid) < page_count(full))

    print('\n--- grid continuation across pages ---')
    big = [{'name': 'BIG SCENE', 'images': sections[0]['images'] * 5}]   # 15 photos
    big_pdf = build_deck_pdf({'id': 1, 'name': 'Big'}, big, layout='grid').read()
    expected = 1 + -(-15 // (GRID_COLS * GRID_ROWS))                      # title + ceil(15/6)
    check(f'15 photos spill onto {expected - 1} grid pages',
          page_count(big_pdf) == expected, page_count(big_pdf))

    print('\n--- photo with no storyboard note ---')
    no_note = [{'name': 'NO NOTES', 'images': [
        {'deck_image_id': 1, 'filename': 'a.jpg', 'storyboard_note': None,
         'thumbnail_blob': make_jpeg('scope', (120, 120, 120))},
        {'deck_image_id': 2, 'filename': 'b.jpg', 'storyboard_note': '   ',
         'thumbnail_blob': make_jpeg('vertical', (120, 120, 120))},
    ]}]
    nn_full = build_deck_pdf({'id': 2, 'name': 'No Notes'}, no_note, layout='full').read()
    check('note-less photos render in full layout (title + card + 2)',
          page_count(nn_full) == 4, page_count(nn_full))
    nn_grid = build_deck_pdf({'id': 2, 'name': 'No Notes'}, no_note, layout='grid').read()
    check('note-less photos render in grid layout', page_count(nn_grid) == 2, page_count(nn_grid))

    print('\n--- corrupt thumbnail is skipped, not raised ---')
    only_bad = [{'name': 'ALL BAD', 'images': [
        {'deck_image_id': 9, 'filename': 'corrupt.jpg', 'thumbnail_blob': b'not a jpeg'},
        {'deck_image_id': 10, 'filename': 'empty.jpg', 'thumbnail_blob': None},
    ]}]
    try:
        bad_pdf = build_deck_pdf({'id': 3, 'name': 'Bad'}, only_bad, layout='full').read()
        raised = None
    except Exception as exc:  # noqa: BLE001
        bad_pdf, raised = b'', exc
    check('undecodable thumbnails do not raise', raised is None, repr(raised))
    check('a section of only-bad photos yields title page alone',
          page_count(bad_pdf) == 1, page_count(bad_pdf))
    mixed = [{'name': 'MIXED', 'images': [
        {'deck_image_id': 11, 'filename': 'bad.jpg', 'thumbnail_blob': b'\xff\xd8 junk'},
        {'deck_image_id': 12, 'filename': 'good.jpg', 'storyboard_note': 'survivor',
         'thumbnail_blob': make_jpeg('square', (150, 150, 150))},
    ]}]
    mixed_pdf = build_deck_pdf({'id': 4, 'name': 'Mixed'}, mixed, layout='full').read()
    check('one bad photo does not take the good one down',
          page_count(mixed_pdf) == 3, page_count(mixed_pdf))

    print('\n--- empty section produces no stranded title card ---')
    empties = [
        {'name': 'GHOST A', 'images': []},
        {'name': 'REAL', 'images': [
            {'deck_image_id': 20, 'filename': 'g.jpg', 'thumbnail_blob': make_jpeg('widescreen', (99, 99, 99))}]},
        {'name': 'GHOST B', 'images': []},
    ]
    e_full = build_deck_pdf({'id': 5, 'name': 'Ghosts'}, empties, layout='full').read()
    check('full layout: title + 1 card + 1 photo (ghost scenes gone)',
          page_count(e_full) == 3, page_count(e_full))
    e_grid = build_deck_pdf({'id': 5, 'name': 'Ghosts'}, empties, layout='grid').read()
    check('grid layout: title + 1 section page', page_count(e_grid) == 2, page_count(e_grid))
    all_empty = build_deck_pdf({'id': 6, 'name': 'Nothing'},
                               [{'name': 'X', 'images': []}], layout='grid').read()
    check('a wholly empty deck still yields a valid 1-page PDF',
          all_empty.startswith(b'%PDF-') and page_count(all_empty) == 1, page_count(all_empty))

    print('\n--- include_unsorted toggle ---')
    without = load_sections(conn, include_unsorted=False)
    check('include_unsorted=0 drops the Unsorted bucket',
          all(s['name'] is not None for s in without) and len(without) == 3, [s['name'] for s in without])
    wo_pdf = build_deck_pdf({'id': 1, 'name': 'Night Market'}, without, layout='full').read()
    check('dropping Unsorted removes its card + its 2 photos (12 -> 9)',
          page_count(wo_pdf) == 9, page_count(wo_pdf))

    print('\n--- filename sanitizer ---')
    check('forward slash stripped', '/' not in sanitize_filename('My/Deck'), sanitize_filename('My/Deck'))
    check('backslash stripped', '\\' not in sanitize_filename('My\\Deck'))
    traversal = sanitize_filename('../../etc/passwd')
    check('traversal defused: no separators survive, nothing starts with a dot',
          '/' not in traversal and '\\' not in traversal
          and '..' not in traversal and not traversal.startswith('.'), traversal)
    check('control characters removed',
          sanitize_filename('bad\nname\x00here') == 'badnamehere', repr(sanitize_filename('bad\nname\x00here')))
    check('empty name falls back', sanitize_filename('   ') == 'Deck', sanitize_filename('   '))
    check('download name shape', pdf_download_name('My Lookbook') == 'My Lookbook - Lookbook.pdf',
          pdf_download_name('My Lookbook'))
    check('download name of a hostile deck name is safe',
          pdf_download_name('a/b\\c') == 'a-b-c - Lookbook.pdf', pdf_download_name('a/b\\c'))

    print('\n--- bad layout value ---')
    try:
        build_deck_pdf({'id': 1, 'name': 'x'}, sections, layout='mosaic')
        rejected = False
    except ValueError:
        rejected = True
    check('unknown layout raises ValueError', rejected)

    full_path = os.path.join(OUT_DIR, 'sample_full.pdf')
    grid_path = os.path.join(OUT_DIR, 'sample_grid.pdf')
    with open(full_path, 'wb') as fh:
        fh.write(full)
    with open(grid_path, 'wb') as fh:
        fh.write(grid)
    conn.close()

    print(f'\nSamples written for eyeballing:\n  {full_path}\n  {grid_path}')
    print(f'\n{passed} passed, {failed} failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

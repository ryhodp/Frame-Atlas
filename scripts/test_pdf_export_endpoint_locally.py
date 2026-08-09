#!/usr/bin/env python3
"""Day 22 (V40): local checks for the /api/decks/<id>/export.pdf ENDPOINT.

`scripts/test_pdf_export_locally.py` covers the layout engine and is
deliberately Flask-free. This file covers the half that file can't reach: the
route's permission model, its parameter validation, and its promise not to
write anything.

That split matters because V39 shipped a permission bug that only surfaced
when someone actually read `admin_required` — "the owner check is obviously
right" is exactly the assumption worth pinning with a test. Here the risk is
the mirror image: this route is owner-scoped rather than admin-only, so the
test that earns its keep is the one proving another logged-in user gets
nothing back.

Run:  scripts/.venv/bin/python scripts/test_pdf_export_endpoint_locally.py
"""

import io
import os
import sys
import sqlite3
import tempfile
import importlib.util

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(REPO, 'backend'))

from PIL import Image  # noqa: E402

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


def make_jpeg(size=(800, 450), color=(180, 90, 40)):
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='JPEG', quality=85)
    return buf.getvalue()


def page_count(data):
    """Count pages without a PDF library — /Type /Page (not /Pages)."""
    return data.count(b'/Type /Page') - data.count(b'/Type /Pages')


def main():
    workdir = tempfile.mkdtemp(prefix='frame_atlas_pdf_endpoint_test_')
    db_path = os.path.join(workdir, 'library.db')

    src = open(os.path.join(REPO, 'backend', 'app.py')).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f'DB_PATH = {db_path!r}')
    assert patched != src, 'Could not find DB_PATH line to patch'
    open(os.path.join(workdir, 'app.py'), 'w').write(patched)

    os.environ.setdefault('GOOGLE_OAUTH_CLIENT_ID', 'dummy')
    os.environ.setdefault('GOOGLE_OAUTH_CLIENT_SECRET', 'dummy')
    os.environ.setdefault('GEMINI_API_KEY', 'dummy')

    spec = importlib.util.spec_from_file_location('test_app_pdf', os.path.join(workdir, 'app.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print('App imported OK.\n')

    client = mod.app.test_client()

    # ── Unauthenticated first, BEFORE /api/setup creates the admin ──────────
    print('--- login gate ---')
    r = client.get('/api/decks/1/export.pdf')
    check('anonymous request does not get a PDF', r.status_code != 200 or
          not r.data.startswith(b'%PDF-'), f'status={r.status_code}')

    setup_r = client.post('/api/setup', json={'email': 'admin@test.com', 'password': 'testpass123'})
    assert setup_r.status_code == 200, setup_r.get_json()

    # ── Seed: 2 scenes + an unsorted bucket, 5 photos, owned by user 1 ──────
    conn = sqlite3.connect(mod.DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    image_ids = []
    for i in range(5):
        c.execute(
            'INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio)'
            ' VALUES (1, ?, ?, ?, ?)',
            (f'pdf-file-{i}', f'frame_{i}.jpg', make_jpeg(), '16:9'),
        )
        image_ids.append(c.lastrowid)

    c.execute("INSERT INTO decks (user_id, name) VALUES (1, 'Endpoint Test Deck')")
    deck_id = c.lastrowid
    c.execute('INSERT INTO scenes (deck_id, name, sort_order) VALUES (?, ?, ?)', (deck_id, 'Scene A', 0))
    scene_a = c.lastrowid
    c.execute('INSERT INTO scenes (deck_id, name, sort_order) VALUES (?, ?, ?)', (deck_id, 'Scene B', 1))
    scene_b = c.lastrowid

    for idx, img_id in enumerate(image_ids[:2]):
        c.execute('INSERT INTO deck_images (deck_id, scene_id, image_id, storyboard_order, storyboard_note)'
                  ' VALUES (?, ?, ?, ?, ?)', (deck_id, scene_a, img_id, idx, f'Note {idx}'))
    for idx, img_id in enumerate(image_ids[2:4]):
        c.execute('INSERT INTO deck_images (deck_id, scene_id, image_id, storyboard_order, storyboard_note)'
                  ' VALUES (?, ?, ?, ?, ?)', (deck_id, scene_b, img_id, idx, None))
    # One photo left unsorted (scene_id NULL).
    c.execute('INSERT INTO deck_images (deck_id, scene_id, image_id, storyboard_order, storyboard_note)'
              ' VALUES (?, NULL, ?, 0, ?)', (deck_id, image_ids[4], 'Loose frame'))
    conn.commit()
    print(f'Seeded deck {deck_id}: 2 scenes + 1 unsorted, 5 photos.\n')

    # ── Owner, default layout ───────────────────────────────────────────────
    print('--- owner export, layout=full (default) ---')
    r = client.get(f'/api/decks/{deck_id}/export.pdf')
    check('200 OK', r.status_code == 200, f'status={r.status_code} body={r.data[:200]}')
    check('content type is application/pdf', r.mimetype == 'application/pdf', r.mimetype)
    check('body is a real PDF', r.data.startswith(b'%PDF-'))
    disposition = r.headers.get('Content-Disposition', '')
    check('sent as an attachment', 'attachment' in disposition, disposition)
    check('filename is the sanitized deck name', 'Endpoint Test Deck - Lookbook.pdf' in disposition, disposition)
    full_pages = page_count(r.data)
    # title(1) + [card + 2] + [card + 2] + [card + 1] = 9
    check('page count = title + 3 sections with their cards + 5 photos', full_pages == 9, f'got {full_pages}')

    # ── Grid layout ─────────────────────────────────────────────────────────
    print('\n--- owner export, layout=grid ---')
    r = client.get(f'/api/decks/{deck_id}/export.pdf?layout=grid')
    check('200 OK', r.status_code == 200, f'status={r.status_code}')
    grid_pages = page_count(r.data)
    check('grid is fewer pages than full', 0 < grid_pages < full_pages, f'grid={grid_pages} full={full_pages}')

    # ── include_unsorted ────────────────────────────────────────────────────
    print('\n--- include_unsorted toggle ---')
    r = client.get(f'/api/decks/{deck_id}/export.pdf?include_unsorted=0')
    no_unsorted = page_count(r.data)
    check('dropping Unsorted removes its card + its photo', no_unsorted == full_pages - 2,
          f'{no_unsorted} vs {full_pages}')
    r = client.get(f'/api/decks/{deck_id}/export.pdf?include_unsorted=false')
    check('include_unsorted=false is honoured too', page_count(r.data) == no_unsorted)

    # ── Bad layout ──────────────────────────────────────────────────────────
    print('\n--- parameter validation ---')
    r = client.get(f'/api/decks/{deck_id}/export.pdf?layout=banana')
    check('unknown layout is a 400, not a 500', r.status_code == 400, f'status={r.status_code}')
    check('400 names the valid layouts', 'full' in (r.get_json() or {}).get('error', ''))

    # ── Missing deck ────────────────────────────────────────────────────────
    r = client.get('/api/decks/99999/export.pdf')
    check('nonexistent deck is a 404', r.status_code == 404, f'status={r.status_code}')

    # ── THE ONE THAT MATTERS: another logged-in user ─────────────────────────
    print("\n--- another user's deck ---")
    code = 'PDFTEST01'
    c.execute("INSERT INTO invite_codes (code, created_by) VALUES (?, 1)", (code,))
    conn.commit()
    other = mod.app.test_client()
    reg = other.post('/api/auth/register', json={
        'username': 'friend', 'email': 'friend@test.com',
        'password': 'friendpass123', 'invite_code': code})
    assert reg.status_code == 200, reg.get_json()
    r = other.get(f'/api/decks/{deck_id}/export.pdf')
    check("a different logged-in user gets 404, not the owner's PDF", r.status_code == 404,
          f'status={r.status_code}')
    check('and definitely no PDF bytes', not r.data.startswith(b'%PDF-'))

    # ── Export must not mutate the deck ─────────────────────────────────────
    print('\n--- export is read-only ---')
    cols = {row['name'] for row in c.execute('PRAGMA table_info(decks)').fetchall()}
    if 'updated_at' in cols:
        before = c.execute('SELECT updated_at FROM decks WHERE id = ?', (deck_id,)).fetchone()[0]
        client.get(f'/api/decks/{deck_id}/export.pdf')
        after = c.execute('SELECT updated_at FROM decks WHERE id = ?', (deck_id,)).fetchone()[0]
        check('exporting does not bump decks.updated_at', before == after, f'{before} -> {after}')
    else:
        check('decks.updated_at not present in this schema (nothing to bump)', True)

    activity_before = c.execute(
        'SELECT COUNT(*) FROM deck_activity WHERE deck_id = ?', (deck_id,)
    ).fetchone()[0] if c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='deck_activity'"
    ).fetchone() else None
    if activity_before is not None:
        client.get(f'/api/decks/{deck_id}/export.pdf?layout=grid')
        activity_after = c.execute(
            'SELECT COUNT(*) FROM deck_activity WHERE deck_id = ?', (deck_id,)).fetchone()[0]
        check('exporting writes no activity-feed entry', activity_before == activity_after,
              f'{activity_before} -> {activity_after}')

    conn.close()
    print(f'\n{passed} passed, {failed} failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

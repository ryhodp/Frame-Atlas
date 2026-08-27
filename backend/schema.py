"""schema.py — Frame Atlas database build + migrate + boot checks
(Day 28 / Phase 3).

Everything that runs once at startup to get the database into the shape the
rest of the code expects: create every table, apply every ALTER-TABLE
migration, seed the placeholder admin row, build the FTS index and its
triggers, then verify the result (check_schema) — all lifted verbatim from
app.py.

run_self_test() deliberately stayed in app.py: it calls deck helpers
(_deck_access, touch_deck) that live there, and this module is not allowed to
import from app.py. app.py passes it in:  init_db(run_self_test=run_self_test).
"""
import gzip
import json
import os
import sqlite3
from array import array

from core import get_db


def _is_duplicate_column_error(e):
    """V44 (Day 26): every ALTER TABLE ADD COLUMN migration below is wrapped
    in a try/except that's silent on the routine case — the column already
    existing, which is true on every boot after the first. But a genuinely
    unexpected error (disk full, DB locked, corrupted schema) getting
    swallowed the same way is exactly the pattern that hid the V27 crop bug
    for weeks. This tells the two apart so only the unexpected case logs."""
    return 'duplicate column' in str(e).lower()

# V49 (Day 48): every column added by an ALTER TABLE in init_db(), by table.
#
# This list exists because a migration can fail SILENTLY AND PERMANENTLY on
# production while passing everywhere it's tested. decks.updated_at did
# exactly that for three weeks: its ALTER used a non-constant DEFAULT, which
# SQLite allows on an empty table and refuses on one that already has rows.
# Every test builds a fresh, empty database, so the migration always worked
# under test and never worked on the one database with real decks in it.
#
# The lesson generalises past this column: a test suite that always starts
# from an empty database cannot see any migration bug that only bites a
# populated one. So the guard can't be another test. It has to run where the
# real data is, at boot, and check the schema that ACTUALLY exists rather
# than the one the migrations above intended to create.
#
# When adding a migration, add its column here too.
EXPECTED_COLUMNS = {
    'images': (
        'tagging_status', 'md5_checksum', 'phash', 'source_url',
        'camera_rig', 'lens', 'lens_filter', 'stop', 'onset_notes',
    ),
    'users': (
        'google_oauth_token', 'email', 'last_login_at',
        'failed_login_count', 'login_locked_until',
    ),
    'decks': ('invite_token', 'updated_at', 'feedback_enabled'),
    'deck_members': ('permission',),
    'colors': ('share', 'palette_version'),
}


def missing_columns(conn):
    """Return [(table, column), ...] for every expected column that isn't
    actually in the database. Pure read — PRAGMA only, no writes — so it's
    safe to call at boot and straightforward to assert on in a test.

    A table that doesn't exist at all is reported as missing every one of its
    columns rather than being skipped: 'the table is gone' is strictly worse
    than 'a column is gone', and silently passing that case would defeat the
    point of the check."""
    missing = []
    for table, columns in EXPECTED_COLUMNS.items():
        try:
            present = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
        except Exception as e:
            print(f'[schema] WARNING: could not inspect table {table}: {e}')
            present = set()
        for col in columns:
            if col not in present:
                missing.append((table, col))
    return missing


def check_schema(conn):
    """Verify the live database really has every column the code expects, and
    say so LOUDLY if it doesn't.

    Deliberately does not raise or exit (Ryan's call): a missing column breaks
    the features that touch it, but search, tagging and the rest of the app
    keep working, and taking the whole site down over one column would turn a
    partial outage into a total one. The logging is the product here — this
    only helps if the line is impossible to scroll past, since the failure it
    describes is otherwise completely invisible until someone clicks the one
    broken button."""
    missing = missing_columns(conn)
    if not missing:
        print('[schema] OK — all expected columns present.')
        return missing

    print('[schema] ' + '=' * 62)
    print(f'[schema] CRITICAL: {len(missing)} expected column(s) MISSING from the database.')
    for table, col in missing:
        print(f'[schema]     {table}.{col}')
    print('[schema] Any feature touching these will fail with HTTP 500.')
    print('[schema] A migration above did not apply. Most likely cause: an')
    print('[schema] ALTER TABLE with a non-constant DEFAULT, which SQLite')
    print('[schema] refuses once the table has rows (it allows it when empty,')
    print('[schema] which is why the tests would not have caught it).')
    print(f'[schema] sqlite version here: {sqlite3.sqlite_version}')
    print('[schema] ' + '=' * 62)
    return missing


def init_db(run_self_test=None):
    conn = get_db()
    c = conn.cursor()

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

    c.execute('''
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            drive_file_id TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            thumbnail_blob BLOB NOT NULL,
            caption TEXT,
            aspect_ratio TEXT,
            tagging_status TEXT DEFAULT 'pending',
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_favorite INTEGER DEFAULT 0,
            is_flagged INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

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

    c.execute('''
        CREATE TABLE IF NOT EXISTS filmography (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER NOT NULL,
            title TEXT,
            director TEXT,
            dp TEXT,
            year TEXT,
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')

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

    c.execute('''
        CREATE TABLE IF NOT EXISTS scenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            sort_order INTEGER,
            FOREIGN KEY (deck_id) REFERENCES decks(id)
        )
    ''')

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

    # V42 (Day 24): anonymous client feedback on a shared lookbook. Both
    # tables key off deck_image_id (the deck-specific instance of a photo),
    # same as storyboard_note already does — an image can appear in more
    # than one deck, and feedback belongs to the one it was left on.
    #
    # A "pick" is a toggle, one per (frame, browser) — UNIQUE enforces that
    # server-side so a double-click or a retry can't inflate the count.
    # viewer_token is a random id the viewer's OWN browser generates and
    # stores in localStorage (never a login), kept separate from the display
    # name they type so retyping their name slightly differently doesn't
    # fork their identity or let two people who both type "Sarah" share a
    # pick slot.
    c.execute('''
        CREATE TABLE IF NOT EXISTS deck_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_image_id INTEGER NOT NULL,
            viewer_token TEXT NOT NULL,
            viewer_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(deck_image_id, viewer_token),
            FOREIGN KEY (deck_image_id) REFERENCES deck_images(id)
        )
    ''')

    # Comments are never deduped or toggled — every submission is its own
    # row. viewer_token still rides along (not used for uniqueness here) so
    # a future "edit your own comment" feature would have something to key
    # on without a schema change.
    c.execute('''
        CREATE TABLE IF NOT EXISTS deck_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_image_id INTEGER NOT NULL,
            viewer_token TEXT NOT NULL,
            viewer_name TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (deck_image_id) REFERENCES deck_images(id)
        )
    ''')

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

    # DAY 14 (V13): invite-only accounts + per-user favorites/flags.
    c.execute('''
        CREATE TABLE IF NOT EXISTS invite_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            created_by INTEGER NOT NULL,
            used_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            used_at TIMESTAMP,
            FOREIGN KEY (created_by) REFERENCES users(id),
            FOREIGN KEY (used_by) REFERENCES users(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS user_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            image_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, image_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS user_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            image_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, image_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')

    # V14: which images each user has actually scrolled past, and when.
    # The shuffled home feed uses last_seen_at to demote images seen in the
    # last 7 days so fresh inspiration surfaces first.
    c.execute('''
        CREATE TABLE IF NOT EXISTS image_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            image_id INTEGER NOT NULL,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            seen_count INTEGER DEFAULT 1,
            UNIQUE(user_id, image_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (image_id) REFERENCES images(id)
        )
    ''')

    # Per-user Gemini spend: one running-total row per (user, calendar month),
    # updated in place every time that user's key gets a response back
    # (tagging or NL search). Powers the "Gemini spend" number on Settings.
    c.execute('''
        CREATE TABLE IF NOT EXISTS gemini_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            month TEXT NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            UNIQUE(user_id, month),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # V17: Drive files a friend deleted from their library. We can't move
    # files in a folder we only have Viewer access to, so sync skips these
    # instead — otherwise every deleted image would return on the next sync.
    c.execute('''
        CREATE TABLE IF NOT EXISTS sync_exclusions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            drive_file_id TEXT NOT NULL,
            UNIQUE(user_id, drive_file_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # V18: view-only crew members on a deck (distinct from the anonymous,
    # loginless /share/<token> link — a deck_members row is a real account
    # with permanent access, tracked so the owner can see and revoke it).
    c.execute('''
        CREATE TABLE IF NOT EXISTS deck_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(deck_id, user_id),
            FOREIGN KEY (deck_id) REFERENCES decks(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # V18: activity feed for a deck (who did what, when). Only the owner can
    # write to a deck, so user_id here is always the owner — except for the
    # 'invited'/'joined' rows, which record the two sides of a member joining.
    c.execute('''
        CREATE TABLE IF NOT EXISTS deck_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (deck_id) REFERENCES decks(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')

    # V27: history of automatic monthly database backups uploaded to Drive.
    # Lets the backup job know when it last ran (so it fires once a month,
    # not on every boot) and which Drive file to delete once more than
    # KEEP_BACKUP_COUNT copies exist.
    c.execute('''
        CREATE TABLE IF NOT EXISTS db_backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drive_file_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Day 28 (Phase 3): one row per hit on a rate-limited public endpoint
    # (/api/auth/register, /api/auth/forgot-password). Hand-rolled rather than
    # Flask-Limiter — same spirit as the V44 login lockout: a tiny table, no
    # new pip dependency, no added Railway deploy time. _rate_limited() in
    # app.py reads and prunes this; rows older than the window are dead weight
    # and get deleted opportunistically on the next check.
    c.execute('''
        CREATE TABLE IF NOT EXISTS rate_limit_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            client_ip TEXT NOT NULL,
            hit_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_rate_limit_scope_ip_time '
              'ON rate_limit_hits(scope, client_ip, hit_at)')

    conn.commit()

    try:
        c.execute("ALTER TABLE images ADD COLUMN tagging_status TEXT DEFAULT 'pending'")
        conn.commit()
        print("[migration] Added tagging_status column")
    except Exception as e:
        if not _is_duplicate_column_error(e):
            print(f"[migration] WARNING: unexpected error adding tagging_status column: {e}")

    # V7: fingerprints for duplicate detection.
    # md5_checksum = exact-file fingerprint (comes free from Drive metadata)
    # phash        = perceptual hash, a visual fingerprint that survives resizing/re-saving
    for _col in ('md5_checksum', 'phash'):
        try:
            c.execute(f"ALTER TABLE images ADD COLUMN {_col} TEXT")
            conn.commit()
            print(f"[migration] Added {_col} column")
        except Exception as e:
            if not _is_duplicate_column_error(e):
                print(f"[migration] WARNING: unexpected error adding {_col} column: {e}")

    # V7 part 2: holds the signed-in user's Google OAuth token (for uploads),
    # separate from the read-only service account used for sync/download.
    try:
        c.execute("ALTER TABLE users ADD COLUMN google_oauth_token TEXT")
        conn.commit()
        print("[migration] Added google_oauth_token column")
    except Exception as e:
        if not _is_duplicate_column_error(e):
            print(f"[migration] WARNING: unexpected error adding google_oauth_token column: {e}")

    # V13 (Day 14): admin's login email.
    try:
        c.execute("ALTER TABLE users ADD COLUMN email TEXT")
        conn.commit()
        print("[migration] Added email column")
    except Exception as e:
        if not _is_duplicate_column_error(e):
            print(f"[migration] WARNING: unexpected error adding email column: {e}")

    # V18: reusable "join this deck as a viewer" link, separate from the
    # anonymous share_token — accepting it requires login and creates a
    # deck_members row.
    try:
        c.execute("ALTER TABLE decks ADD COLUMN invite_token TEXT")
        conn.commit()
        print("[migration] Added invite_token column to decks")
    except Exception as e:
        if not _is_duplicate_column_error(e):
            print(f"[migration] WARNING: unexpected error adding invite_token column to decks: {e}")

    # V19: last login timestamp, powers the admin per-user analytics view.
    try:
        c.execute("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP")
        conn.commit()
        print("[migration] Added last_login_at column to users")
    except Exception as e:
        if not _is_duplicate_column_error(e):
            print(f"[migration] WARNING: unexpected error adding last_login_at column to users: {e}")

    # V44 (Day 26): escalating per-account login lockout. Counts consecutive
    # wrong passwords and, past LOGIN_LOCK_THRESHOLD, sets a lockout window
    # that doubles with each further failure (capped at LOGIN_LOCK_MAX_SECONDS)
    # — see login() below. Deliberately keyed on the ACCOUNT, not the caller's
    # IP: a shared network (hotel wifi, a set) must never let one guesser lock
    # out everyone else on it, and an attacker rotating IPs must not be able
    # to dodge the throttle.
    for _col, _type in (('failed_login_count', 'INTEGER DEFAULT 0'), ('login_locked_until', 'TIMESTAMP')):
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {_col} {_type}")
            conn.commit()
            print(f"[migration] Added {_col} column to users")
        except Exception as e:
            if not _is_duplicate_column_error(e):
                print(f"[migration] WARNING: unexpected error adding {_col} column to users: {e}")

    # V23: crew collaboration — permission levels on deck_members (viewer/editor)
    try:
        c.execute("ALTER TABLE deck_members ADD COLUMN permission TEXT DEFAULT 'viewer'")
        conn.commit()
        print("[migration] Added permission column to deck_members")
    except Exception as e:
        if not _is_duplicate_column_error(e):
            print(f"[migration] WARNING: unexpected error adding permission column to deck_members: {e}")

    # V23: track when a deck was last modified for the "new changes" banner.
    #
    # V49 (Day 48): this shipped as `... TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
    # and NEVER RAN ON PRODUCTION. SQLite refuses a non-constant DEFAULT in
    # ALTER TABLE ADD COLUMN when the table HAS ROWS to fill in ("Cannot add a
    # column with non-constant default") — but allows it on an EMPTY table,
    # where there is nothing to materialise.
    #
    # That distinction is the whole bug, and it is about DATA, not about
    # SQLite versions or environments (verified directly: same failure on
    # 3.50.4 and on Railway, decided purely by whether rows exist). Every
    # test script builds a fresh database, so `decks` is always empty when
    # init_db() runs and the ALTER always succeeds. Ryan's production database
    # already held real decks when this migration first shipped in V23, so it
    # failed there — and kept failing on every boot afterwards, because those
    # rows never went away.
    #
    # Consequence: opening a deck, the public /api/share/<token> view, and
    # every deck mutation returned 500 from V25 (2026-07-26) until this fix —
    # three weeks — while V38/V40/V41/V42 all shipped "verified" on top of a
    # feature that was dead on the live site. No test could have caught it;
    # the test suite's own fresh-database setup is what hid it.
    #
    # No DEFAULT is the fix: that form is always legal, rows or not. The value
    # is then seeded per-row from created_at rather than "now", so a deck's
    # history stays honest and restoring this column can't light up a false
    # "New changes" banner on every deck a collaborator has open.
    #
    # DO NOT put a non-constant DEFAULT (CURRENT_TIMESTAMP, CURRENT_DATE,
    # CURRENT_TIME, or any parenthesised expression) in an ALTER TABLE here.
    # A constant — 0, 'pending', 'viewer' — is always fine; every other
    # migration in this function already uses one. It will pass every test and
    # then fail on the one database that has data in it. `scripts/
    # test_schema_guard_locally.py` greps for this, and check_schema() below
    # reports it loudly at boot if one ever slips through.
    try:
        c.execute("ALTER TABLE decks ADD COLUMN updated_at TIMESTAMP")
        conn.commit()
        print("[migration] Added updated_at column to decks")
    except Exception as e:
        if not _is_duplicate_column_error(e):
            print(f"[migration] WARNING: unexpected error adding updated_at column to decks: {e}")

    # Seed the column for any deck that predates it (and repair every row on
    # the production DB, where the column has been missing entirely). Runs on
    # every boot but only ever touches NULL rows, so it self-disables once
    # they're filled — no separate "did this run?" flag needed. Guarded
    # because it must not take the app down if `decks` is somehow absent.
    try:
        seeded = c.execute(
            "UPDATE decks SET updated_at = COALESCE(created_at, CURRENT_TIMESTAMP) "
            "WHERE updated_at IS NULL"
        ).rowcount
        conn.commit()
        if seeded:
            print(f"[migration] Seeded updated_at from created_at on {seeded} deck(s)")
    except Exception as e:
        print(f"[migration] WARNING: could not seed updated_at on decks: {e}")

    # V25: where a web-clipped image came from, so a still pulled off a blog
    # can be traced back to its page later. NULL for Drive syncs and uploads.
    try:
        c.execute("ALTER TABLE images ADD COLUMN source_url TEXT")
        conn.commit()
        print("[migration] Added source_url column to images")
    except Exception as e:
        if not _is_duplicate_column_error(e):
            print(f"[migration] WARNING: unexpected error adding source_url column to images: {e}")

    # V24: how much of the frame each palette color actually covers (0.0-1.0).
    # extract_palette() always computed this and threw it away; color search
    # ranked by vibrance alone, so a lipstick-sized patch of red scored the
    # same as a red wall. NULL = extracted before V24; the backfill
    # (/api/extract-colors?force=true) fills those in.
    try:
        c.execute("ALTER TABLE colors ADD COLUMN share REAL")
        conn.commit()
        print("[migration] Added share column to colors")
    except Exception as e:
        if not _is_duplicate_column_error(e):
            print(f"[migration] WARNING: unexpected error adding share column to colors: {e}")

    # V33: which build of extract_palette() produced this row. NULL = before
    # versioning existed. backfill_palettes() rebuilds anything older than
    # PALETTE_VERSION, so an algorithm change can't leave the library half-old
    # and half-new — which would make colour search silently inconsistent
    # between two photos that look the same.
    try:
        c.execute("ALTER TABLE colors ADD COLUMN palette_version INTEGER")
        conn.commit()
        print("[migration] Added palette_version column to colors")
    except Exception as e:
        if not _is_duplicate_column_error(e):
            print(f"[migration] WARNING: unexpected error adding palette_version column to colors: {e}")

    # V39: DP technical notes — camera/rig, lens, lens filter, stop (T-stop,
    # kept as TEXT since values like "T2.8" don't fit a numeric column), and
    # a freeform on-set notes box. Any photo can carry these, not just
    # my_work — Ryan's call, and the first metadata field in this app that's
    # owner-editable rather than admin-only (see the /notes endpoint below).
    for _col in ('camera_rig', 'lens', 'lens_filter', 'stop', 'onset_notes'):
        try:
            c.execute(f"ALTER TABLE images ADD COLUMN {_col} TEXT")
            conn.commit()
            print(f"[migration] Added {_col} column to images")
        except Exception as e:
            if not _is_duplicate_column_error(e):
                print(f"[migration] WARNING: unexpected error adding {_col} column to images: {e}")

    # V42: whether a deck accepts anonymous picks/comments on its share link.
    # DEFAULT 0 means every deck that already existed before this migration
    # ran comes back OFF — a deliberate choice (confirmed with Ryan): links
    # already sitting in an agency inbox must not suddenly start accepting
    # public comments the moment this ships. Brand new decks get feedback ON
    # by INSERTing the value explicitly in create_deck() below, overriding
    # this column default for every row created from here on.
    try:
        c.execute("ALTER TABLE decks ADD COLUMN feedback_enabled INTEGER DEFAULT 0")
        conn.commit()
        print("[migration] Added feedback_enabled column to decks")
    except Exception as e:
        if not _is_duplicate_column_error(e):
            print(f"[migration] WARNING: unexpected error adding feedback_enabled column to decks: {e}")

    # V39: full-text search over the 5 columns above. FTS5 is SQLite's own
    # built-in index (tokenizes on word boundaries, ranks by BM25) — no new
    # pip dependency, ships inside Python's sqlite3 module. `images.id` IS
    # this table's rowid directly (no separate id column), so a MATCH query
    # yields image ids with no extra join needed.
    c.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
            camera_rig, lens, lens_filter, stop, onset_notes
        )
    ''')
    conn.commit()

    # Triggers keep notes_fts in sync — not from every write callsite in this
    # file by hand, which is exactly how build_search_filters() (V32) came to
    # exist after two hand-copied filter functions drifted apart. The UPDATE
    # trigger is scoped with `OF col, col, ...` so it fires ONLY when one of
    # these 5 columns actually changes, not on every unrelated images write
    # (tag edits go through a different table, but crop/thumbnail/view-log
    # writes touch images itself and must not trigger a pointless FTS rebuild).
    c.execute('''
        CREATE TRIGGER IF NOT EXISTS notes_fts_ai AFTER INSERT ON images BEGIN
            INSERT INTO notes_fts(rowid, camera_rig, lens, lens_filter, stop, onset_notes)
            VALUES (new.id, new.camera_rig, new.lens, new.lens_filter, new.stop, new.onset_notes);
        END
    ''')
    c.execute('''
        CREATE TRIGGER IF NOT EXISTS notes_fts_ad AFTER DELETE ON images BEGIN
            DELETE FROM notes_fts WHERE rowid = old.id;
        END
    ''')
    c.execute('''
        CREATE TRIGGER IF NOT EXISTS notes_fts_au
        AFTER UPDATE OF camera_rig, lens, lens_filter, stop, onset_notes ON images
        BEGIN
            UPDATE notes_fts SET
                camera_rig = new.camera_rig, lens = new.lens, lens_filter = new.lens_filter,
                stop = new.stop, onset_notes = new.onset_notes
            WHERE rowid = new.id;
        END
    ''')
    conn.commit()

    c.execute("""
        INSERT INTO users (id, username, password_hash)
        SELECT 1, 'ryan', ''
        WHERE NOT EXISTS (SELECT 1 FROM users WHERE id = 1)
    """)
    conn.commit()

    c.execute("""
        UPDATE images SET tagging_status = 'done'
        WHERE id IN (SELECT DISTINCT image_id FROM tags)
        AND tagging_status = 'pending'
    """)
    conn.commit()

    # V13 (Day 14): the old is_favorite/is_flagged columns on `images` were a
    # single shared on/off switch — replaced by per-user user_favorites/
    # user_flags tables. One-time backfill: whatever was starred/flagged
    # before logins existed becomes user 1's (the admin's) favorites/flags.
    # INSERT OR IGNORE makes this safe to run on every boot.
    c.execute("""
        INSERT OR IGNORE INTO user_favorites (user_id, image_id)
        SELECT 1, id FROM images WHERE is_favorite = 1
    """)
    c.execute("""
        INSERT OR IGNORE INTO user_flags (user_id, image_id)
        SELECT 1, id FROM images WHERE is_flagged = 1
    """)
    conn.commit()

    # One-time cleanup: the AI tagging pipeline wasn't lowercasing Gemini's
    # output (fixed above), so a tag like "Tense" from one run and "tense"
    # from another could sit as two case-different rows on the same image —
    # invisible as a real duplicate anywhere tags get grouped (autocomplete,
    # search dropdown), since SQLite groups strings case-sensitively.
    # Idempotent: a no-op once everything's already lowercase and deduped.
    c.execute("UPDATE tags SET value = LOWER(value) WHERE value != LOWER(value)")
    c.execute("""
        DELETE FROM tags WHERE id NOT IN (
            SELECT MIN(id) FROM tags GROUP BY image_id, category, value
        )
    """)
    conn.commit()

    # V43 (Day 25): there were zero indexes anywhere in this database until
    # now — every search, autocomplete keystroke and tag lookup was a full
    # table scan. Invisible at a few thousand images (a scan is still only a
    # handful of milliseconds) but it grows in a straight line, and
    # autocomplete fires on every keystroke. CREATE INDEX IF NOT EXISTS is
    # trivially idempotent, so this just runs on every boot rather than
    # needing a self-disabling backfill flag like the phash/palette ones.
    for _idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_images_user_id ON images(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_tags_image_id ON tags(image_id)",
        # Covers the AND-filter subquery every search/select-all/removal-
        # preview runs: WHERE value IN (...) GROUP BY image_id — value leads
        # so SQLite can seek straight to matching rows, image_id trails so
        # the GROUP BY is satisfied from the index alone.
        "CREATE INDEX IF NOT EXISTS idx_tags_value_image_id ON tags(value, image_id)",
        # Covers autocomplete's WHERE user_id = ? AND LOWER(value) LIKE 'x%'
        # and the co-occurrence suggestions query's WHERE t.user_id = ?.
        "CREATE INDEX IF NOT EXISTS idx_tags_user_value ON tags(user_id, value)",
        # Covers tag-removal preview and bulk-remove, both scoped by category.
        "CREATE INDEX IF NOT EXISTS idx_tags_category_value ON tags(category, value)",
    ):
        c.execute(_idx_sql)
    conn.commit()

    # Last thing before the connection closes: confirm the migrations above
    # actually produced the schema the rest of the app is written against.
    # Runs after every migration has had its turn, so anything reported here
    # genuinely did not apply rather than merely not having run yet.
    missing = check_schema(conn)

    # V50: then confirm the QUERIES built on that schema actually work,
    # against a disposable row in THIS real database. Skipped, not run, when
    # a column is already known missing — check_schema() already reported
    # it, and every query here would just fail the same way with less detail.
    if not missing and run_self_test is not None:
        run_self_test(conn)

    conn.close()

def load_embeddings_seed():
    """Loads pre-computed CLIP vectors (backend/embeddings_seed.json.gz) into
    the `embeddings` table, so the visual-similarity feature works without
    running CLIP on the server itself (Pillow/torch don't build here anyway —
    see Day 9 notes). The seed file is generated by a separate offline script
    and shipped in the repo. Safe to call on every boot: it's a no-op once the
    DB already matches the seed."""
    seed_path = os.path.join(os.path.dirname(__file__), 'embeddings_seed.json.gz')
    if not os.path.exists(seed_path):
        print("Embeddings seed: no embeddings_seed.json.gz found, skipping")
        return

    try:
        with gzip.open(seed_path, 'rt', encoding='utf-8') as f:
            seed = json.load(f)
        vectors = seed.get('vectors', {})
    except Exception as e:
        print(f"Embeddings seed: failed to read/parse file ({e}), skipping")
        return

    if not vectors:
        print("Embeddings seed: file has no vectors, skipping")
        return

    conn = get_db()
    c = conn.cursor()

    # Fast-path: if the table already has exactly as many rows as the seed
    # has vectors, and every seeded image_id already has a row, there's
    # nothing to do — skip the rewrite so boots stay quick.
    existing_count = c.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    if existing_count == len(vectors):
        seed_ids = set(int(k) for k in vectors.keys())
        existing_ids = set(r[0] for r in c.execute("SELECT image_id FROM embeddings").fetchall())
        if seed_ids == existing_ids:
            print(f"Embeddings seed: already up to date ({existing_count} vectors), skipping")
            conn.close()
            return

    valid_ids = set(r[0] for r in c.execute("SELECT id FROM images").fetchall())

    loaded = 0
    skipped = 0
    try:
        for image_id_str, vec in vectors.items():
            image_id = int(image_id_str)
            if image_id not in valid_ids:
                skipped += 1
                continue
            blob = array('f', vec).tobytes()
            c.execute("DELETE FROM embeddings WHERE image_id = ?", (image_id,))
            c.execute(
                "INSERT INTO embeddings (image_id, user_id, clip_vector) VALUES (?, 1, ?)",
                (image_id, blob)
            )
            loaded += 1

        conn.commit()
        print(f"Embeddings seed: loaded {loaded} vectors ({skipped} skipped)")
    except sqlite3.OperationalError as e:
        if 'disk' in str(e).lower() or 'full' in str(e).lower():
            print(f"⚠️  Embeddings seed skipped: storage full — app running without updated embeddings. Free up space in /app/data and redeploy to fix.")
            conn.rollback()
        else:
            raise
    finally:
        conn.close()

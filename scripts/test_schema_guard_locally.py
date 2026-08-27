"""
Frame Atlas — local test for the V49 schema repair + boot-time schema guard.

WHY THIS FILE EXISTS
--------------------
decks.updated_at was missing from the PRODUCTION database for three weeks
(2026-07-26 → 2026-08-16) while being present in every environment anyone
tested in. Its migration read:

    ALTER TABLE decks ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

SQLite refuses a non-constant DEFAULT in ALTER TABLE ADD COLUMN when the table
HAS ROWS ("Cannot add a column with non-constant default"), and permits it
when the table is EMPTY. Verified directly on 3.50.4: empty succeeds, two rows
fails. It is a data condition, not a version or platform one.

That single distinction is why the bug was invisible. Every test script here
builds a throwaway database from scratch, so `decks` is empty when init_db()
runs and the ALTER always succeeds. Ryan's production database already held
real decks, so it always failed — on every boot, permanently. Opening a deck,
the public share link, and every deck mutation returned 500 the whole time.

The general lesson, worth more than this one column: **a suite that always
starts from an empty database cannot detect a migration that only breaks on a
populated one.** Any test that merely "checks decks work" is worthless here —
those passed continuously while the live site was broken. This file instead:

  1. Reproduces the PRODUCTION state directly, by building a database whose
     decks table genuinely has no updated_at column AND HAS ROWS IN IT, then
     asserting init_db() repairs it. Confirmed to go red against the pre-fix
     code on this machine — a reproduction that only passes is worthless.

  2. Greps app.py for the BUG CLASS — any ALTER TABLE with a non-constant
     DEFAULT — so the next one is caught at source, before it ever meets a
     database with rows in it. This check needs no database at all, which is
     what makes it immune to the empty-database blind spot above.

  3. Covers missing_columns()/check_schema() themselves, including that they
     report a dropped column rather than passing quietly.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python3 scripts/test_schema_guard_locally.py
"""

import importlib.util
import io
import os
import re
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))

passed = 0
failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}")


def load_app(db_path, workdir):
    """Import a copy of app.py wired to a throwaway database."""
    os.environ["FA_DB_PATH"] = db_path

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")

    name = "test_app_schema_" + os.path.basename(workdir)
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def columns_of(db_path, table):
    conn = sqlite3.connect(db_path)
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()
    return cols


# ---------------------------------------------------------------------------
# 1. The source-level guard: no ALTER TABLE may use a non-constant DEFAULT.
#    Version-independent, so unlike everything else here it would have caught
#    the original bug on any machine, including the ones where it "worked".
# ---------------------------------------------------------------------------
def test_no_non_constant_defaults():
    print("\n--- no ALTER TABLE uses a non-constant DEFAULT ---")
    src = open(os.path.join(REPO, "backend", "app.py")).read()

    alters = re.findall(r'ALTER TABLE[^"\']*', src)
    check("found the ALTER TABLE migrations to inspect", len(alters) > 5)

    # CURRENT_TIMESTAMP / CURRENT_DATE / CURRENT_TIME, or a parenthesised
    # expression — SQLite rejects all of these in ALTER TABLE ADD COLUMN.
    bad = [a for a in alters
           if re.search(r'DEFAULT\s+(CURRENT_TIMESTAMP|CURRENT_DATE|CURRENT_TIME|\()', a, re.I)]
    for a in bad:
        print(f"        offending migration: {a.strip()}")
    check("no ALTER TABLE has a non-constant DEFAULT", not bad)

    # The specific line that caused the outage, pinned so it can't regress.
    check(
        "decks.updated_at migration has no DEFAULT at all",
        re.search(r'ALTER TABLE decks ADD COLUMN updated_at TIMESTAMP\s*["\']', src) is not None,
    )


# ---------------------------------------------------------------------------
# 2. The production repair: a database that genuinely lacks the column.
# ---------------------------------------------------------------------------
def test_repairs_missing_column():
    print("\n--- a database missing decks.updated_at is repaired at boot ---")
    workdir = tempfile.mkdtemp(prefix="frame_atlas_schema_repair_")
    db_path = os.path.join(workdir, "library.db")

    # Build the pre-fix production schema by hand: decks WITHOUT updated_at,
    # holding rows with real created_at values to seed from.
    conn = sqlite3.connect(db_path)
    conn.execute('''CREATE TABLE decks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        share_token TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute("INSERT INTO decks (id, user_id, name, created_at) VALUES (1, 1, 'Good Boy', '2026-07-01 10:00:00')")
    conn.execute("INSERT INTO decks (id, user_id, name, created_at) VALUES (2, 1, 'Dennis', '2026-07-15 18:30:00')")
    conn.commit()
    conn.close()

    check("precondition: the column really is absent", "updated_at" not in columns_of(db_path, "decks"))

    load_app(db_path, workdir)  # importing runs init_db()

    cols = columns_of(db_path, "decks")
    check("init_db() added the missing column", "updated_at" in cols)

    conn = sqlite3.connect(db_path)
    rows = dict(conn.execute("SELECT id, updated_at FROM decks").fetchall())
    conn.close()

    check("no deck was left with an empty timestamp", all(v is not None for v in rows.values()))
    # Ryan's call: seed from created_at, not "now", so restoring the column
    # can't make every deck look freshly edited to a collaborator.
    check("deck 1 seeded from its own created_at", rows.get(1) == '2026-07-01 10:00:00')
    check("deck 2 seeded from its own created_at", rows.get(2) == '2026-07-15 18:30:00')
    check("the two decks kept DIFFERENT timestamps (not all stamped 'now')",
          rows.get(1) != rows.get(2))


# ---------------------------------------------------------------------------
# 3. A second boot must not re-stamp rows that already have a value.
# ---------------------------------------------------------------------------
def test_seed_is_idempotent():
    print("\n--- re-running the seed leaves real edit times alone ---")
    workdir = tempfile.mkdtemp(prefix="frame_atlas_schema_idem_")
    db_path = os.path.join(workdir, "library.db")

    mod = load_app(db_path, workdir)

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO decks (id, user_id, name, created_at, updated_at) "
                 "VALUES (1, 1, 'Edited', '2026-07-01 10:00:00', '2026-08-14 09:00:00')")
    conn.commit()
    conn.close()

    mod.init_db()  # boot again

    conn = sqlite3.connect(db_path)
    got = conn.execute("SELECT updated_at FROM decks WHERE id = 1").fetchone()[0]
    conn.close()

    check("a deck's genuine last-edited time survives another boot", got == '2026-08-14 09:00:00')


# ---------------------------------------------------------------------------
# 4. The guard itself.
# ---------------------------------------------------------------------------
def test_schema_guard():
    print("\n--- missing_columns() / check_schema() ---")
    workdir = tempfile.mkdtemp(prefix="frame_atlas_schema_guard_")
    db_path = os.path.join(workdir, "library.db")
    mod = load_app(db_path, workdir)

    conn = sqlite3.connect(db_path)
    check("a freshly migrated database reports nothing missing", mod.missing_columns(conn) == [])
    check("check_schema agrees on a healthy database", mod.check_schema(conn) == [])

    # Every column the guard watches must be one the migrations really create,
    # or the guard would cry wolf on a perfectly good database forever.
    for table, cols in mod.EXPECTED_COLUMNS.items():
        present = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        missing = [c for c in cols if c not in present]
        check(f"every expected {table} column actually exists after migration", not missing)

    check("decks.updated_at is one of the watched columns",
          'updated_at' in mod.EXPECTED_COLUMNS['decks'])
    conn.close()

    # Drop a column and confirm the guard notices. SQLite can DROP COLUMN in
    # 3.35+; if this machine is older, skip rather than fail the suite for an
    # unrelated reason.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE decks DROP COLUMN updated_at")
        conn.commit()
        droppable = True
    except Exception as e:
        print(f"  SKIP  DROP COLUMN unavailable on sqlite {sqlite3.sqlite_version} ({e})")
        droppable = False

    if droppable:
        missing = mod.missing_columns(conn)
        check("the guard reports a column that went missing", ('decks', 'updated_at') in missing)
        check("check_schema returns the same finding", ('decks', 'updated_at') in mod.check_schema(conn))
        check("the guard does NOT raise — the app stays up", True)
    conn.close()

    # A table vanishing entirely is reported, not silently skipped.
    workdir2 = tempfile.mkdtemp(prefix="frame_atlas_schema_notable_")
    db2 = os.path.join(workdir2, "library.db")
    mod2 = load_app(db2, workdir2)
    conn = sqlite3.connect(db2)
    conn.execute("DROP TABLE colors")
    conn.commit()
    missing = mod2.missing_columns(conn)
    check("a dropped TABLE is reported as all its columns missing",
          ('colors', 'share') in missing and ('colors', 'palette_version') in missing)
    conn.close()


def main():
    test_no_non_constant_defaults()
    test_repairs_missing_column()
    test_seed_is_idempotent()
    test_schema_guard()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

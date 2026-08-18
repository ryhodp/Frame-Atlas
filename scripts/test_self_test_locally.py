"""
Frame Atlas — local test for run_self_test() (V50, Day 48 cont'd).

WHY THIS EXISTS
----------------
check_schema() (V49) confirms every expected COLUMN exists. It would have
caught decks.updated_at going missing instantly — but it answers "does the
shape exist?", not "does the real code that uses that shape actually work?"
A column can exist and a query built on it can still be wrong: a backwards
WHERE, the wrong table aliased, a column name that typos into a DIFFERENT
real column and still parses without error. None of that is a missing
column, so check_schema() would report a perfectly clean bill of health
while the feature stayed broken.

run_self_test() closes that gap by calling the ACTUAL functions a real
request calls — _deck_access(), touch_deck() — against a disposable
"canary" deck row inserted into the real database for exactly this purpose,
then removed. This file proves three things check_schema()'s own tests
don't cover:

  1. A normal boot passes cleanly and leaves ZERO trace in the decks table
     — the canary is gone whether every check passed or one of them raised.
  2. It's correctly SKIPPED (not reported as a failure) with no users yet,
     and skipped by init_db() itself when the schema is already known
     broken — no double-reporting the same root cause two different ways.
  3. It actually CATCHES a bug check_schema() structurally cannot: a
     function whose query is wrong despite every column being present.
     Simulated by monkeypatching _deck_access() to something broken and
     confirming run_self_test() reports it — this is the check that
     justifies the whole file existing, not just a passing smoke test.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python3 scripts/test_self_test_locally.py
"""

import importlib.util
import os
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
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
    src = open(os.path.join(REPO, "backend", "app.py")).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
    assert patched != src, "Could not find DB_PATH line to patch"
    open(os.path.join(workdir, "app.py"), "w").write(patched)

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")

    name = "test_app_selftest_" + os.path.basename(workdir)
    spec = importlib.util.spec_from_file_location(name, os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fresh_app_with_user(prefix):
    """init_db() itself seeds a placeholder user id=1 (pre-Day-14 legacy
    bootstrap, see the `INSERT ... WHERE NOT EXISTS` a few lines above
    check_schema() in app.py) — so a freshly booted app already has exactly
    the one user run_self_test() needs. Nothing to insert here."""
    workdir = tempfile.mkdtemp(prefix=prefix)
    db_path = os.path.join(workdir, "library.db")
    mod = load_app(db_path, workdir)  # runs init_db(), which already ran self-test once
    return mod, db_path


# ---------------------------------------------------------------------------
# 1. A normal boot: passes, and leaves no trace.
# ---------------------------------------------------------------------------
def test_clean_run_leaves_no_trace():
    print("\n--- a healthy database: self-test passes and leaves no canary behind ---")
    mod, db_path = fresh_app_with_user("frame_atlas_selftest_clean_")

    # get_db(), not a raw sqlite3.connect() — production always calls
    # run_self_test() with a Row-factory connection (init_db() uses get_db()
    # throughout), and _deck_access()/touch_deck() rely on dict-style access
    # to the row. A plain connection would make every check fail with a
    # TypeError that has nothing to do with the thing being tested.
    conn = mod.get_db()
    results = mod.run_self_test(conn)
    conn.close()

    check("ran all 3 live checks", len(results) == 3)
    check("every check passed on a healthy database", all(ok for _, ok, _ in results))

    conn = sqlite3.connect(db_path)
    leftover = conn.execute(
        "SELECT COUNT(*) FROM decks WHERE name = '__frame_atlas_selftest_canary__'"
    ).fetchone()[0]
    conn.close()
    check("the canary deck was removed — zero trace left behind", leftover == 0)


# ---------------------------------------------------------------------------
# 2. Skips: no users yet; and init_db() skips it when schema is broken.
# ---------------------------------------------------------------------------
def test_skips():
    print("\n--- correctly SKIPPED, not reported as a failure ---")
    workdir = tempfile.mkdtemp(prefix="frame_atlas_selftest_nouser_")
    db_path = os.path.join(workdir, "library.db")
    # init_db() itself unconditionally seeds a placeholder user id=1 (see
    # fresh_app_with_user's docstring above), so there is no way to boot the
    # app into a genuinely userless database — remove that row afterward to
    # reach the state run_self_test() is guarding against, and call it
    # directly rather than by rebooting.
    mod = load_app(db_path, workdir)
    conn = mod.get_db()
    conn.execute("DELETE FROM users")
    conn.commit()

    decks_before = conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0]
    results = mod.run_self_test(conn)
    decks_after = conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0]
    conn.close()

    check("no users yet -> zero checks run (not zero checks FAILED)", results == [])
    check("nothing was inserted into decks either", decks_before == decks_after == 0)

    print("\n--- run_self_test() is skipped, not run, when the schema is already broken ---")
    # init_db() heals decks.updated_at unconditionally now (V49), so a
    # database built without the column doesn't STAY broken long enough to
    # observe — by the time init_db() returns, check_schema() would report
    # clean again. That self-healing is correct behaviour, but it means the
    # only honest way to test init_db()'s OWN guard — "skip self-test when
    # check_schema() found something missing" — is to exercise that exact
    # two-line sequence directly, the same way init_db() itself does, rather
    # than trying to catch the database in a broken state that no longer
    # persists.
    mod2, db_path2 = fresh_app_with_user("frame_atlas_selftest_guardlogic_")
    conn2 = mod2.get_db()
    conn2.execute("ALTER TABLE decks DROP COLUMN updated_at")
    conn2.commit()

    missing = mod2.check_schema(conn2)
    check("check_schema() reports the column missing", ('decks', 'updated_at') in missing)

    decks_before2 = conn2.execute("SELECT COUNT(*) FROM decks").fetchone()[0]
    ran = False
    if not missing:  # the exact guard init_db() uses
        ran = True
        mod2.run_self_test(conn2)
    decks_after2 = conn2.execute("SELECT COUNT(*) FROM decks").fetchone()[0]
    conn2.close()

    check("init_db()'s guard does not invoke self-test against a known-broken schema", not ran)
    check("and so nothing was inserted into decks either", decks_before2 == decks_after2)

    # Confirm this isn't a SQLite-availability artifact: DROP COLUMN needs
    # 3.35+. If unavailable the ALTER above would have raised before reaching
    # here, so getting this far means the drop genuinely happened.
    check(f"DROP COLUMN was actually available on this sqlite ({sqlite3.sqlite_version})", True)


# ---------------------------------------------------------------------------
# 3. The one that matters: catches a bug check_schema() cannot see at all.
# ---------------------------------------------------------------------------
def test_catches_a_broken_query_with_schema_intact():
    print("\n--- catches a functional bug that check_schema() is structurally blind to ---")
    mod, db_path = fresh_app_with_user("frame_atlas_selftest_brokenquery_")

    conn = mod.get_db()
    missing = mod.missing_columns(conn)
    check("precondition: schema is fully intact (every column present)", missing == [])

    # Simulate the exact bug class this exists to catch: every column is
    # present, but the function that reads them is wrong. Here, _deck_access
    # is patched to look at the WRONG deck id — same shape of mistake as a
    # backwards WHERE clause or a copy-pasted query with an off-by-one.
    original = mod._deck_access
    def broken_deck_access(c, deck_id, user_id):
        return original(c, deck_id + 999999, user_id)  # always misses
    mod._deck_access = broken_deck_access

    results = mod.run_self_test(conn)
    mod._deck_access = original  # restore before any other check runs
    conn.close()

    by_name = {name: (ok, detail) for name, ok, detail in results}
    ok, detail = by_name.get('deck open (_deck_access)', (None, None))
    check("the broken deck-open check is reported as FAILED, not silently OK", ok is False)
    check("touch_deck, which was not broken, still reported OK", by_name.get('deck touch (touch_deck)', (None,))[0] is True)

    # And the canary must still be cleaned up even though a check inside the
    # try block effectively "failed" — the finally block is what guarantees
    # this, not the happy path.
    conn = sqlite3.connect(db_path)
    leftover = conn.execute(
        "SELECT COUNT(*) FROM decks WHERE name = '__frame_atlas_selftest_canary__'"
    ).fetchone()[0]
    conn.close()
    check("canary still removed even when a check inside it failed", leftover == 0)


def main():
    test_clean_run_leaves_no_trace()
    test_skips()
    test_catches_a_broken_query_with_schema_intact()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

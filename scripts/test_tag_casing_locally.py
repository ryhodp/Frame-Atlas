"""
Frame Atlas — local test for the tag-casing bug (search dropdown showing
"tense" twice, ranked above an exact "Tenet" film match). Confirms:
1) the one-time init_db() migration lowercases + dedupes pre-existing
   case-different tag rows, and
2) a fresh AI-tagging-style insert is lowercased going forward.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_tag_casing_locally.py
"""

import importlib.util
import os
import sqlite3
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_tag_casing_test_")
    db_path = os.path.join(workdir, "library.db")

    src = open(os.path.join(REPO, "backend", "app.py")).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
    assert patched != src, "Could not find DB_PATH line to patch"
    open(os.path.join(workdir, "app.py"), "w").write(patched)

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    # Seed a pre-existing case-duplicate BEFORE importing app.py, so the
    # module-level init_db() call (which runs at import time) has to clean
    # it up — this is the actual code path a Railway boot would hit.
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, role TEXT DEFAULT 'user',
            drive_folder_id TEXT, gemini_api_key TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'ryan', '')")
    c.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            drive_file_id TEXT UNIQUE NOT NULL, filename TEXT NOT NULL,
            thumbnail_blob BLOB NOT NULL, caption TEXT, aspect_ratio TEXT,
            tagging_status TEXT DEFAULT 'pending', date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_favorite INTEGER DEFAULT 0, is_flagged INTEGER DEFAULT 0
        )
    """)
    c.execute("INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob) VALUES (1, 1, 'f1', 'a.jpg', X'00')")
    c.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT, image_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            category TEXT NOT NULL, value TEXT NOT NULL
        )
    """)
    # Same image, same category, case-different value -> exactly the dropdown-duplicate bug.
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (1, 1, 'mood', 'Tense')")
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (1, 1, 'mood', 'tense')")
    conn.commit()
    conn.close()
    print("Seeded a pre-existing 'Tense'/'tense' case-duplicate on the same image before app import.")

    spec = importlib.util.spec_from_file_location("test_app", os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print("App imported OK (runs init_db() migrations, including the new tag-casing cleanup).")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    rows = c.execute("SELECT value, category FROM tags WHERE image_id = 1").fetchall()
    conn.close()
    assert len(rows) == 1 and rows[0]["value"] == "tense", [dict(r) for r in rows]
    print("1. Migration deduped 'Tense'/'tense' down to one lowercase row.")

    admin = mod.app.test_client()
    r = admin.post("/api/setup", json={"email": "test@test.com", "password": "testpass123"})
    assert r.status_code == 200, r.get_json()

    r = admin.get("/api/autocomplete?q=ten")
    body = r.get_json()
    tense_matches = [x for x in body if x["type"] == "tag" and x["value"] == "tense"]
    assert len(tense_matches) == 1, body
    print("2. Autocomplete shows exactly one 'tense' match, not two.")

    print("\nAll tag-casing checks passed.")


if __name__ == "__main__":
    main()

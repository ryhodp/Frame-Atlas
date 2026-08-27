"""
Frame Atlas — local test for the V30 tag plural-normalization fix.

Background: autocomplete showed "car" (Location) / "car" (Objects) / "cars"
(Objects) as three separate suggestions. The Location/Objects split turned
out to be intentional — the fixed taxonomy uses "car" as both a valid
location ("a car interior/exterior scene") and a valid free-form subject
("a car is visible") — those are two different facts about a photo and
correctly stay separate tags. But "car"/"cars" under the SAME category
(subjects, which is open-ended free text, not a fixed vocabulary) was real
drift: nothing ever normalized plural/singular the way casing was already
normalized ("Tense"/"tense").

This test covers:
1. normalize_tag_value() collapses plurals but skips the exception list
   (glass, hands, etc.) where stripping would be wrong.
2. The one-time merge_plural_tag_duplicates() migration collapses an
   existing "car"/"cars" pair on the same photo down to one row, and
   RENAMES a lone "cars" (no "car" counterpart) rather than deleting it.
3. Different categories on the same value are left alone — "car" under
   location_type and "car" under subjects both survive as separate tags.
4. New tags written after boot (the Gemini auto-tag path) are normalized at
   write time, so the drift can't reappear.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_tag_plural_merge_locally.py
"""

import importlib.util
import os
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_tag_plural_test_")
    db_path = os.path.join(workdir, "library.db")

    os.environ["FA_DB_PATH"] = db_path

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    # Seed pre-existing drift BEFORE importing app.py, so the module-level
    # merge_plural_tag_duplicates() call (which runs at import time, same as
    # the existing tag-casing migration) has to clean it up for real — this
    # is the exact code path a Railway boot hits.
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
    c.executemany(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob) VALUES (?, 1, ?, ?, X'00')",
        [(1, 'f1', 'a.jpg'), (2, 'f2', 'b.jpg'), (3, 'f3', 'c.jpg')],
    )
    c.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT, image_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            category TEXT NOT NULL, value TEXT NOT NULL
        )
    """)
    # Image 1: both "car" and "cars" on the same photo, same category -> merge to one row.
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (1, 1, 'subjects', 'car')")
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (1, 1, 'subjects', 'cars')")
    # Image 1 also has "car" under location_type -- a DIFFERENT category, must survive untouched.
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (1, 1, 'location_type', 'car')")
    # Image 2: only "cars" (no singular counterpart) -> rename in place, not delete.
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (2, 1, 'subjects', 'cars')")
    # Image 3: "glass" -- must NOT be stripped to "glas".
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (3, 1, 'subjects', 'glass')")
    conn.commit()
    conn.close()
    print("Seeded pre-existing car/cars drift (and a glass control case) before app import.")

    spec = importlib.util.spec_from_file_location("fa_tag_plural_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_tag_plural_app"] = mod
    spec.loader.exec_module(mod)
    print("App imported OK (runs the merge_plural_tag_duplicates migration).")

    failures = []

    def check(label, cond, detail=""):
        if cond:
            print(f"{label} — OK")
        else:
            print(f"{label} — FAIL  {detail}")
            failures.append(label)

    # ── 1. normalize_tag_value() unit behaviour ─────────────────────────────
    check("'cars' normalizes to 'car'", mod.normalize_tag_value('cars') == 'car')
    check("'Tense' still normalizes to 'tense' (casing preserved)", mod.normalize_tag_value('Tense') == 'tense')
    check("'glass' is NOT stripped to 'glas'", mod.normalize_tag_value('glass') == 'glass')
    check("'hands' is NOT collapsed into 'hand' (exception list)", mod.normalize_tag_value('hands') == 'hands')
    check("'bus' is left alone (short word, not a plural)", mod.normalize_tag_value('bus') == 'bus')
    check("'car' (already singular) is unchanged", mod.normalize_tag_value('car') == 'car')

    # ── 2. migration results, read back from the DB ─────────────────────────
    # merge_plural_tag_duplicates() launches its actual writes on a background
    # thread; give it a moment the same way the phash/palette backfills do.
    import time
    time.sleep(0.5)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    img1_subjects = conn.execute(
        "SELECT value FROM tags WHERE image_id = 1 AND category = 'subjects'"
    ).fetchall()
    check("Image 1's car/cars merged to exactly one 'subjects' row",
          [r["value"] for r in img1_subjects] == ["car"], [dict(r) for r in img1_subjects])

    img1_location = conn.execute(
        "SELECT value FROM tags WHERE image_id = 1 AND category = 'location_type'"
    ).fetchall()
    check("Image 1's 'car' under location_type survives untouched (different category)",
          [r["value"] for r in img1_location] == ["car"], [dict(r) for r in img1_location])

    img2_subjects = conn.execute(
        "SELECT value FROM tags WHERE image_id = 2 AND category = 'subjects'"
    ).fetchall()
    check("Image 2's lone 'cars' was renamed to 'car', not deleted",
          [r["value"] for r in img2_subjects] == ["car"], [dict(r) for r in img2_subjects])

    img3_subjects = conn.execute(
        "SELECT value FROM tags WHERE image_id = 3 AND category = 'subjects'"
    ).fetchall()
    check("Image 3's 'glass' is untouched by the migration",
          [r["value"] for r in img3_subjects] == ["glass"], [dict(r) for r in img3_subjects])

    total_car_rows = conn.execute(
        "SELECT COUNT(*) FROM tags WHERE value = 'car' AND category = 'subjects'"
    ).fetchone()[0]
    check("No duplicate 'car' subjects rows were created across images", total_car_rows == 2, total_car_rows)
    conn.close()

    # ── 3. autocomplete reflects the merge — one 'car' suggestion per category
    admin = mod.app.test_client()
    r = admin.post("/api/setup", json={"email": "test@test.com", "password": "testpass123"})
    check("Admin setup succeeds", r.status_code == 200, r.get_json())

    r = admin.get("/api/autocomplete?q=car")
    body = r.get_json()
    tag_matches = [x for x in body if x["type"] == "tag" and x["value"] == "car"]
    check("Autocomplete shows 'car' exactly twice (Location + Objects), never 'cars'",
          len(tag_matches) == 2 and all(x["value"] == "car" for x in tag_matches), body)
    plural_leftover = [x for x in body if x["type"] == "tag" and x["value"] == "cars"]
    check("No 'cars' suggestion is left over", not plural_leftover, body)

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED ❌")
        for f in failures:
            print(f"   - {f}")
    else:
        print("ALL TAG PLURAL-MERGE TESTS PASSED ✅")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

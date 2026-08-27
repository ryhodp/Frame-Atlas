"""
Frame Atlas — local test for Day 31 (V73): image hydration + palette +
boot-time backfills split into backend/images_common.py.

Before this these helpers lived in app.py and were only exercised indirectly
(test_decks, test_analytics, test_thumbnail_caching, test_dp_notes_search,
test_v24/v33_color). This gives the module its own direct coverage:
  - the split wiring: app.py exposes `images_common`; the moved names are NOT
    bare globals on app.py anymore; favorite_col moved to core.py and is
    re-imported so app.py's other SELECTs are unchanged
  - images_common imports only from core / colors / fingerprint / imaging
  - build_image_dict: private path -> a /api/images/<id>/thumb?v= URL;
    public=True -> embedded base64 (the V43 share-link exception must survive)
  - hydrate_image_rows: bulk hydrate -> tags/palette/filmography populated
  - _fetch_image_dict: single hydrate, is_favorite reflects the OWNER, a
    missing id -> None
  - save_palette: round-trips, stamps PALETTE_VERSION, replaces on rewrite
  - backfill_palettes: rebuilds a stale row and self-disables
  - backfill_notes_fts: seeds a missing row and is a no-op next call
  - merge_plural_tag_duplicates: collapses same-image/same-category drift only
  - end to end: /api/search returns a URL, /api/share returns base64

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_images_common_locally.py
"""

import importlib.util
import io
import os
import re
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, "backend"))

THUMB_URL_RE = re.compile(r"^/api/images/(\d+)/thumb\?v=(.+)$")

PASS = 0
FAIL = 0


def check(label, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f"FAIL  {label}" + (f"  ({extra!r})" if extra is not None else ""))


def make_jpeg(mod, color=(200, 60, 40), size=(160, 90)):
    buf = io.BytesIO()
    mod.Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


IMG_COLS = ("id, filename, thumbnail_blob, caption, aspect_ratio, is_favorite, "
            "md5_checksum, camera_rig, lens, lens_filter, stop, onset_notes")


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_imgcommon_test_")
    os.environ["FA_DB_PATH"] = os.path.join(workdir, "library.db")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    spec = importlib.util.spec_from_file_location(
        "fa_imgcommon_test_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_imgcommon_test_app"] = mod
    spec.loader.exec_module(mod)
    print("App imported OK.")

    ic = mod.images_common

    # ── 1. split wiring ────────────────────────────────────────────────────
    print("\n1. Split wiring")
    moved = ["build_image_dict", "hydrate_image_rows", "_fetch_image_dict",
             "save_palette", "backfill_palettes", "backfill_phashes",
             "backfill_notes_fts", "merge_plural_tag_duplicates"]
    for name in moved:
        check(f"images_common.{name} exists", hasattr(ic, name))
    leaked = [n for n in moved if n in vars(mod)]
    check("no moved name left as a bare global on app.py", leaked == [], leaked)
    check("favorite_col now lives in core.py (re-imported onto app.py unchanged)",
          getattr(mod.favorite_col, "__module__", "?") == "core",
          getattr(mod.favorite_col, "__module__", "?"))
    check("images_common shares core.get_db", ic.get_db is mod.get_db)
    check("images_common shares colors.extract_palette", ic.extract_palette is mod.extract_palette)
    check("images_common shares fingerprint.compute_phash", ic.compute_phash is mod.compute_phash)
    check("images_common shares imaging.ar_float_from_str", ic.ar_float_from_str is mod.ar_float_from_str)

    # ── 2. build_image_dict — private vs public ────────────────────────────
    print("\n2. build_image_dict")
    conn = sqlite3.connect(mod.DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    blob = make_jpeg(mod)
    c.execute(
        "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, caption,"
        " aspect_ratio, md5_checksum, camera_rig, lens, stop)"
        " VALUES (1,?,?,?,?,?,?,?,?,?)",
        ("f-a", "frame_a.jpg", blob, "Frame A", "1920:800", "ck-v1",
         "Alexa Mini LF", "probe", "T12"),
    )
    img_a = c.lastrowid
    conn.commit()
    row = c.execute(f"SELECT {IMG_COLS} FROM images WHERE id=?", (img_a,)).fetchone()

    d = ic.build_image_dict(row, [{"category": "mood", "value": "tense"}], ["#aabbcc"], None)
    m = THUMB_URL_RE.match(d["thumbnail"])
    check("private path returns a /api/images/<id>/thumb?v= URL", bool(m), d["thumbnail"])
    check("?v= is the image's md5_checksum", m and m.group(2) == "ck-v1", m and m.group(2))
    check("notes dict carries the 5 DP fields", d["notes"] == {
        "camera_rig": "Alexa Mini LF", "lens": "probe", "lens_filter": None,
        "stop": "T12", "onset_notes": None}, d["notes"])
    check("aspect_ratio kept raw, ar_label snapped to a standard format",
          d["aspect_ratio"] == "1920:800" and d["ar_label"] == "2.39:1", (d["aspect_ratio"], d["ar_label"]))
    check("tags / palette / filmography pass straight through",
          d["tags"] == [{"category": "mood", "value": "tense"}] and d["palette"] == ["#aabbcc"] and d["filmography"] is None)

    dp = ic.build_image_dict(row, [], [], None, public=True)
    check("public=True embeds base64 (the V43 share-link exception)",
          dp["thumbnail"].startswith("data:image/jpeg;base64,"), dp["thumbnail"][:30])
    import base64 as _b64
    check("the embedded base64 decodes to the stored thumbnail bytes",
          _b64.b64decode(dp["thumbnail"].split(",", 1)[1]) == blob)

    # ── 3. hydrate_image_rows ─────────────────────────────────────────────
    print("\n3. hydrate_image_rows")
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?,1,'mood','calm')", (img_a,))
    c.execute("INSERT INTO colors (image_id, user_id, hex, rank) VALUES (?,1,'#112233',0)", (img_a,))
    c.execute("INSERT INTO filmography (image_id, title, director, dp, year) VALUES (?,?,?,?,?)",
              (img_a, "Her", "Spike Jonze", "Hoyte van Hoytema", 2013))
    conn.commit()
    rows = c.execute(f"SELECT {IMG_COLS} FROM images WHERE id=?", (img_a,)).fetchall()
    out = ic.hydrate_image_rows(c, rows)
    check("hydrate returns one dict", len(out) == 1)
    check("bulk-fetched tags land on the dict", out[0]["tags"] == [{"category": "mood", "value": "calm"}], out[0]["tags"])
    check("bulk-fetched palette lands on the dict", out[0]["palette"] == ["#112233"], out[0]["palette"])
    check("bulk-fetched filmography lands on the dict",
          out[0]["filmography"] and out[0]["filmography"]["director"] == "Spike Jonze", out[0]["filmography"])
    check("hydrate of an empty row list is []", ic.hydrate_image_rows(c, []) == [])

    # ── 4. _fetch_image_dict ─────────────────────────────────────────────
    print("\n4. _fetch_image_dict")
    client = mod.app.test_client()
    assert client.post("/api/setup", json={"email": "a@a.com", "password": "testpass123"}).status_code == 200
    fd = ic._fetch_image_dict(c, img_a, 1)
    check("_fetch_image_dict hydrates the same shape", fd and fd["id"] == img_a and "notes" in fd)
    check("is_favorite False before the owner favourites it", fd["is_favorite"] is False)
    c.execute("INSERT INTO user_favorites (user_id, image_id) VALUES (1, ?)", (img_a,))
    conn.commit()
    check("is_favorite reflects the OWNER once favourited",
          ic._fetch_image_dict(c, img_a, 1)["is_favorite"] is True)
    check("_fetch_image_dict of a missing id is None", ic._fetch_image_dict(c, 999999, 1) is None)

    # ── 5. save_palette ──────────────────────────────────────────────────
    print("\n5. save_palette")
    ic.save_palette(img_a, 1, [("#010101", 0.6), ("#020202", 0.4)])
    got = c.execute("SELECT hex, share, palette_version FROM colors WHERE image_id=? ORDER BY rank", (img_a,)).fetchall()
    check("save_palette replaced the old single-colour palette", [r["hex"] for r in got] == ["#010101", "#020202"], [r["hex"] for r in got])
    check("share is stored per entry", [r["share"] for r in got] == [0.6, 0.4])
    check("every row stamped with the current PALETTE_VERSION",
          all(r["palette_version"] == mod.PALETTE_VERSION for r in got))
    ic.save_palette(img_a, 1, ["#030303"])
    got2 = c.execute("SELECT hex, share FROM colors WHERE image_id=?", (img_a,)).fetchall()
    check("bare hex string still accepted (share NULL)", [(r["hex"], r["share"]) for r in got2] == [("#030303", None)])

    # ── 6. backfill_palettes rebuilds stale rows, then self-disables ──────
    print("\n6. backfill_palettes")
    c.execute("UPDATE colors SET palette_version = 1, share = NULL WHERE image_id = ?", (img_a,))
    conn.commit()
    conn.close()
    ic.backfill_palettes()
    import time
    time.sleep(1.0)
    conn = sqlite3.connect(mod.DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    stale = c.execute(
        "SELECT COUNT(*) n FROM colors WHERE palette_version IS NULL OR palette_version < ? OR share IS NULL",
        (mod.PALETTE_VERSION,)).fetchone()["n"]
    check("backfill_palettes rebuilt the stale row to the current version", stale == 0, stale)

    # ── 7. backfill_notes_fts seeds a missing row, no-op next call ────────
    print("\n7. backfill_notes_fts")
    c.execute("INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio)"
              " VALUES (1,'f-b','frame_b.jpg',?, '16:9')", (make_jpeg(mod, (10, 20, 30)),))
    img_b = c.lastrowid
    c.execute("DELETE FROM notes_fts WHERE rowid = ?", (img_b,))
    conn.commit()
    conn.close()
    ic.backfill_notes_fts()
    conn = sqlite3.connect(mod.DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    check("backfill_notes_fts seeded the missing notes_fts row",
          c.execute("SELECT COUNT(*) n FROM notes_fts WHERE rowid = ?", (img_b,)).fetchone()["n"] == 1)
    before = c.execute("SELECT COUNT(*) n FROM notes_fts").fetchone()["n"]
    ic.backfill_notes_fts()
    after = c.execute("SELECT COUNT(*) n FROM notes_fts").fetchone()["n"]
    check("second call is a no-op", before == after, (before, after))

    # ── 8. merge_plural_tag_duplicates ──────────────────────────────────
    print("\n8. merge_plural_tag_duplicates")
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?,1,'subjects','cars')", (img_b,))
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?,1,'subjects','car')", (img_b,))
    c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?,1,'location_type','car')", (img_b,))
    conn.commit()
    conn.close()
    ic.merge_plural_tag_duplicates()
    import time as _t
    _t.sleep(0.8)
    conn = sqlite3.connect(mod.DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    subj = sorted(r["value"] for r in c.execute(
        "SELECT value FROM tags WHERE image_id=? AND category='subjects'", (img_b,)).fetchall())
    loc = [r["value"] for r in c.execute(
        "SELECT value FROM tags WHERE image_id=? AND category='location_type'", (img_b,)).fetchall()]
    check("'cars' + 'car' in the same category collapse to one 'car'", subj == ["car"], subj)
    check("'car' under a different category is left alone (two different facts)", loc == ["car"], loc)

    # ── 9. end to end: URL for authed search, base64 for public share ────
    print("\n9. End to end through the app")
    r = client.get("/api/search").get_json()
    match = next((i for i in r["images"] if i["id"] == img_a), None)
    check("/api/search returns a cacheable thumb URL", match and THUMB_URL_RE.match(match["thumbnail"]), match and match["thumbnail"])
    deck_id = client.post("/api/decks", json={"name": "d"}).get_json()["id"]
    client.post(f"/api/decks/{deck_id}/images", json={"image_ids": [img_a]})
    token = client.post(f"/api/decks/{deck_id}/share").get_json()["share_token"]
    pub = client.get(f"/api/share/{token}").get_json()
    check("public /api/share/<token> still embeds base64 (public flag survived the move)",
          pub["images"][0]["thumbnail"].startswith("data:image/jpeg;base64,"),
          pub["images"][0]["thumbnail"][:30])

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

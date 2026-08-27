"""
Frame Atlas — local test for V33: the two colour-search fixes.

Boots the server against a throwaway database (same trick as
test_v24_color_locally.py) and pins down both bugs that made a maxed-out
orange search return warm-but-not-orange frames:

  1. SHADOW DONATION. extract_palette() merges similar bins into one family and
     the merged bin donates its share. HSV saturation is meaningless once a
     colour is nearly black — #020100 reports saturation 1.0 and hue 30, which
     is arithmetically indistinguishable from a vivid orange — so pure shadow
     was being absorbed into dark warm entries and inflating their coverage.
     Measured on a real photo: search reported 54% orange where 9.5% was.

  2. BROWN IS DARK ORANGE. Hue is blind to brightness, and brown sits within a
     couple of degrees of orange on the wheel. No setting of a hue-only slider
     can separate them, so exactness now carries a brightness tolerance too.

Plus the plumbing that keeps the library consistent: palette_version stamps and
the backfill that rebuilds anything older.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_v33_color_fix_locally.py
"""

import importlib.util
import io
import os
import sys
import sqlite3
import tempfile
import time

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))

W, H = 320, 180
ORANGE = "#E08840"          # the preset orange swatch the UI offers
checks = 0


def ok(msg):
    global checks
    checks += 1
    print(f"  {msg} — OK")


def make_image(mod, paint):
    img = mod.Image.new("RGB", (W, H), (0, 0, 0))
    paint(mod.ImageDraw.Draw(img), W, H)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def paint_tungsten_room(d, w, h):
    """The false positive that started this. A dim warm interior: a small
    genuinely-orange lamp, and a huge field of near-black shadow that carries
    a warm cast. Under the old merge the shadow donated its share to the lamp's
    family and the frame reported as overwhelmingly orange."""
    d.rectangle([0, 0, w, h], fill=(9, 5, 2))        # near-black, warm-tinted
    d.rectangle([0, 0, w, 18], fill=(14, 8, 3))      # a second shadow shade
    d.ellipse([250, 20, 300, 70], fill=(224, 136, 64))   # the lamp, ~4% of frame


def paint_orange_wall(d, w, h):
    """A genuinely orange frame in three shades — extraction splits it, and
    search must still re-add it. This is the case merging exists to serve, so
    it must survive the fix."""
    d.rectangle([0, 0, w, h], fill=(224, 136, 64))
    d.rectangle([0, 0, w, 60], fill=(238, 150, 78))
    d.rectangle([0, 120, w, h], fill=(206, 122, 52))


def paint_orange_and_green(d, w, h):
    """~40% orange, ~52% green. Legitimately passes a 40% dominance setting and
    legitimately fails a 60% one — nothing else has room above 50%."""
    d.rectangle([0, 0, w, h], fill=(47, 149, 61))
    d.rectangle([0, 0, w, 72], fill=(224, 136, 64))


CASES = {
    1: ("tungsten_room.jpg", paint_tungsten_room),
    2: ("orange_wall.jpg", paint_orange_wall),
    3: ("orange_and_green.jpg", paint_orange_and_green),
}


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_v33_test_")
    db_path = os.path.join(workdir, "library.db")

    os.environ["FA_DB_PATH"] = db_path

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")

    spec = importlib.util.spec_from_file_location("test_app_v33", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from PIL import ImageDraw
    mod.ImageDraw = ImageDraw
    print("App imported OK.\n")

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    print("1. Migration")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(colors)").fetchall()]
    assert "palette_version" in cols, cols
    ok("colors.palette_version column present")

    blobs = {}
    for i, (name, paint) in CASES.items():
        blobs[i] = make_image(mod, paint)
        c.execute(
            "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob,"
            " aspect_ratio, date_added) VALUES (?, 1, ?, ?, ?, '16:9', datetime('now', ?))",
            (i, f"fake-{i}", name, blobs[i], f"-{i} minutes"),
        )
    c.execute("INSERT OR IGNORE INTO users (id, username, password_hash, role)"
              " VALUES (1, 'admin', 'x', 'admin')")
    conn.commit()
    for i in CASES:
        mod.images_common.save_palette(i, 1, mod.extract_palette(blobs[i]))
    conn.commit()

    print("\n2. Shadow no longer donates its share to a colour family")
    entries = [(r[0], r[1]) for r in conn.execute(
        "SELECT hex, share FROM colors WHERE image_id = 1 ORDER BY rank").fetchall()]
    dark_share = sum(s for h, s in entries
                     if mod._hsv(h)[2] < mod.PALETTE_DARK_V)
    assert dark_share > 0.5, \
        f"the shadow should be its own entry covering most of the frame, got {dark_share:.1%}"
    ok(f"near-black is stored separately, holding {dark_share:.0%} of the frame")

    for hexv, share in entries:
        _h, _s, v = mod._hsv(hexv)
        if v >= mod.PALETTE_DARK_V:
            assert share < 0.20, (
                f"bright entry {hexv} holds {share:.1%} — shadow is still donating")
    ok("no lit entry absorbed the shadow's share")

    print("\n3. A dim warm room is no longer 'mostly orange'")
    hue_tol = mod.exactness_to_hue_tol(100)
    val_tol = mod.exactness_to_value_tol(100)
    room = mod.color_match_share(ORANGE, entries, hue_tol, val_tol)
    assert room < 0.10, f"tungsten room still reports {room:.1%} orange"
    ok(f"tungsten room reports {room:.1%} orange (was over 50% pre-V33)")

    wall_entries = [(r[0], r[1]) for r in conn.execute(
        "SELECT hex, share FROM colors WHERE image_id = 2").fetchall()]
    wall = mod.color_match_share(ORANGE, wall_entries, hue_tol, val_tol)
    assert wall > 0.90, f"a genuinely orange wall only reports {wall:.1%}"
    ok(f"a genuinely orange wall still reports {wall:.0%} — merging still works")

    print("\n4. Brightness rule: brown stops counting as orange")
    tight_h, tight_v = mod.exactness_to_hue_tol(100), mod.exactness_to_value_tol(100)
    loose_h, loose_v = mod.exactness_to_hue_tol(0), mod.exactness_to_value_tol(0)
    for label, hexv in [("mid brown", "#8B5A2B"), ("dark brown", "#5F3A1C"),
                        ("near-black brown", "#241205")]:
        assert not mod.color_matches(ORANGE, hexv, tight_h, tight_v), \
            f"{label} still counts as orange at the tightest setting"
    ok("mid / dark / near-black brown all rejected at tightest")

    assert mod.color_matches(ORANGE, "#8B5A2B", loose_h, loose_v), \
        "the loosest setting should still be permissive"
    ok("the loosest setting still accepts brown — the slider still spans a range")

    assert mod.color_matches(ORANGE, ORANGE, tight_h, tight_v)
    assert mod.color_matches(ORANGE, "#C97B33", tight_h, tight_v)
    ok("the picked orange and burnt orange survive the tightest setting")

    print("\n5. Brightness is symmetric — a dark pick rejects a bright candidate")
    deep = "#5F3A1C"
    assert not mod.color_matches(deep, ORANGE, tight_h, tight_v), \
        "picking a deep brown should not match a bright orange at tightest"
    ok("picking dark brown rejects bright orange (not just the reverse)")

    print("\n6. Non-search callers are unaffected (duplicate-detection gate)")
    # value_tol defaults to None, so every caller that didn't opt in keeps the
    # V29/V30 calibration that scored 0 false positives across 38 cases.
    assert mod.color_matches(ORANGE, "#8B5A2B", tight_h), \
        "omitting value_tol must behave exactly as pre-V33"
    ok("color_matches() without value_tol is unchanged")

    print("\n7. Dominance above 50% is inherently exclusive")
    client = mod.app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = 1
        s["role"] = "admin"

    def ids(q):
        r = client.get(f"/api/search?{q}")
        assert r.status_code == 200, r.get_json()
        return {i["id"] for i in r.get_json()["images"]}

    q = ORANGE.replace("#", "%23")
    at40 = ids(f"color={q}&prom=40&exact=60")
    at60 = ids(f"color={q}&prom=60&exact=60")
    assert 3 in at40, "the 40%-orange frame should pass a 40% setting"
    assert 3 not in at60, "a 40%-orange frame cannot pass a 60% setting"
    ok("40% orange / 52% green passes at 40, fails at 60 — no toggle needed")

    assert 2 in at60, "the orange wall should still pass a 60% setting"
    ok("the orange wall still passes at 60%")

    print("\n8. The slider's new ceiling is reachable")
    assert 2 in ids(f"color={q}&prom=95&exact=60"), \
        "a nearly-all-orange frame should pass even at 95%"
    assert 1 not in ids(f"color={q}&prom=95&exact=60")
    ok("95% is a usable setting, not a dead end")

    print("\n9. The tungsten room is gone from a maxed-out search")
    maxed = ids(f"color={q}&prom=40&exact=100")
    assert 1 not in maxed, "the false positive survived a maxed-out search"
    ok("dim warm room excluded at 40% / exact — the original bug")

    print("\n10. Palette versioning + backfill")
    stamps = [r[0] for r in conn.execute(
        "SELECT DISTINCT palette_version FROM colors").fetchall()]
    assert stamps == [mod.PALETTE_VERSION], stamps
    ok(f"new palettes are stamped v{mod.PALETTE_VERSION}")

    c.execute("UPDATE colors SET palette_version = 1 WHERE image_id = 1")
    conn.commit()

    def stale():
        cn = sqlite3.connect(db_path)
        n = cn.execute(
            "SELECT COUNT(*) FROM colors WHERE palette_version IS NULL"
            " OR palette_version < ?", (mod.PALETTE_VERSION,)).fetchone()[0]
        cn.close()
        return n

    assert stale() > 0
    mod.images_common.backfill_palettes()
    for _ in range(100):
        time.sleep(0.05)
        if stale() == 0:
            break
    assert stale() == 0, "backfill left rows stamped with an old version"
    ok("backfill rebuilds anything older than the current version")

    cn = sqlite3.connect(db_path)
    before = cn.execute("SELECT id, image_id, hex, share, palette_version"
                        " FROM colors ORDER BY id").fetchall()
    cn.close()
    mod.images_common.backfill_palettes()
    time.sleep(0.4)
    cn = sqlite3.connect(db_path)
    after = cn.execute("SELECT id, image_id, hex, share, palette_version"
                       " FROM colors ORDER BY id").fetchall()
    cn.close()
    assert before == after, "backfill re-ran on an already-current library"
    ok("backfill self-disables — a second boot touches nothing")

    print("\n11. Palette still accounts for the whole frame")
    for i in CASES:
        rows = conn.execute("SELECT share FROM colors WHERE image_id = ?", (i,)).fetchall()
        total = sum(r[0] for r in rows)
        assert total > 0.95, f"image {i} palette only covers {total:.1%} of the frame"
    ok("every palette still covers >95% of its frame — the fix loses nothing")

    conn.close()
    print(f"\nAll {checks} V33 colour checks passed.")


if __name__ == "__main__":
    main()

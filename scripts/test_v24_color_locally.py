"""
Frame Atlas — local test for V24: color search by coverage + hue exactness.

Boots a patched copy of the server against a throwaway database (same trick as
test_v15_locally.py) and exercises the two new knobs on /api/search:

  prom  — how much of the frame the picked color must cover (summed share)
  exact — how close in hue a palette entry has to be to count

The headline case is the one that started this work: a blue frame with a small
red accent (lipstick) used to come back for a "red" search, because the palette
ranks by vibrance and a tiny vivid patch outranks a big dull wall. It must now
stay out at the default coverage and only appear if you deliberately loosen it.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_v24_color_locally.py
"""

import importlib.util
import io
import os
import sqlite3
import tempfile
import time

REPO = os.path.join(os.path.dirname(__file__), "..")

W, H = 320, 180
RED = "#b33a3a"      # the preset red swatch the UI offers
BLUE = (32, 64, 160)


def make_image(mod, paint):
    """paint(draw, w, h) -> None. Returns JPEG bytes."""
    img = mod.Image.new("RGB", (W, H), (0, 0, 0))
    paint(mod.ImageDraw.Draw(img), W, H)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def paint_lipstick(d, w, h):
    """Almost entirely blue, with a small vivid red patch (~3% of frame)."""
    d.rectangle([0, 0, w, h], fill=BLUE)
    d.rectangle([140, 70, 182, 111], fill=(220, 30, 40))


def paint_red_wall(d, w, h):
    """A red wall in three shades — extraction splits it, search must re-add it."""
    d.rectangle([0, 0, w, h], fill=(20, 20, 26))
    d.rectangle([0, 0, w, 45], fill=(138, 16, 16))
    d.rectangle([0, 45, w, 90], fill=(192, 32, 32))
    d.rectangle([0, 90, w, 135], fill=(232, 48, 48))


def paint_solid(rgb):
    def _p(d, w, h):
        d.rectangle([0, 0, w, h], fill=rgb)
    return _p


CASES = {
    1: ("lipstick.jpg", paint_lipstick),
    2: ("red_wall.jpg", paint_red_wall),
    3: ("brown.jpg", paint_solid((122, 80, 56))),
    4: ("gray.jpg", paint_solid((128, 128, 128))),
    5: ("blue.jpg", paint_solid(BLUE)),
}


def search_ids(client, query):
    r = client.get(f"/api/search?{query}")
    data = r.get_json()
    assert r.status_code == 200, data
    return {img["id"] for img in data["images"]}


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_v24_test_")
    db_path = os.path.join(workdir, "library.db")

    src = open(os.path.join(REPO, "backend", "app.py")).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
    assert patched != src, "Could not find DB_PATH line to patch"
    open(os.path.join(workdir, "app.py"), "w").write(patched)

    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")

    spec = importlib.util.spec_from_file_location("test_app", os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from PIL import ImageDraw
    mod.ImageDraw = ImageDraw
    print("App imported OK.")

    # 1. Migration put the share column on `colors`
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(colors)").fetchall()]
    assert "share" in cols, cols
    print("1. Migration: colors.share column present.")

    # Seed images + palettes through the REAL extraction path
    c = conn.cursor()
    blobs = {}
    for i, (name, paint) in CASES.items():
        blobs[i] = make_image(mod, paint)
        c.execute(
            "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob,"
            " aspect_ratio, date_added) VALUES (?, 1, ?, ?, ?, '16:9', datetime('now', ?))",
            (i, f"fake-{i}", name, blobs[i], f"-{i} minutes"),
        )
    conn.commit()
    conn.close()

    palettes = {}
    for i in CASES:
        entries = mod.extract_palette(blobs[i])
        palettes[i] = entries
        mod.save_palette(i, 1, entries)

    # 2. extract_palette now returns (hex, share) and shares are sane
    for i, entries in palettes.items():
        assert entries, f"image {i} produced an empty palette"
        for e in entries:
            assert isinstance(e, tuple) and len(e) == 2, e
            assert 0.0 < e[1] <= 1.0, e
        total = sum(s for _, s in entries)
        assert total <= 1.02, f"image {i} shares sum to {total}"
    print("2. extract_palette: returns (hex, share); shares in range and never over 100%.")

    print("\n   --- measured palettes (calibration data) ---")
    for i, entries in palettes.items():
        top = ", ".join(f"{h} {s*100:.1f}%" for h, s in entries[:4])
        print(f"   {CASES[i][0]:14s} {top}")
    print()

    # 3. Family-summing: the three-shade red wall reads as one big red
    tol = mod.exactness_to_hue_tol(mod.DEFAULT_EXACTNESS)
    wall = mod.color_match_share(RED, palettes[2], tol)
    biggest_single = max(
        (s for h, s in palettes[2] if mod.color_matches(RED, h, tol)), default=0
    )
    assert wall > 0.5, f"red wall only scored {wall:.3f}"
    assert wall > biggest_single * 1.5, (
        f"shades did not combine: total {wall:.3f} vs biggest single {biggest_single:.3f}"
    )
    print(f"3. Family-summing: wall reads {wall*100:.0f}% combined "
          f"(biggest single shade only {biggest_single*100:.0f}%).")

    # 4. The lipstick frame is a real red, but a tiny one
    lip = mod.color_match_share(RED, palettes[1], tol)
    assert 0 < lip < 0.06, f"lipstick share {lip:.4f} — expected a small non-zero"
    print(f"4. Lipstick frame: red present but only {lip*100:.1f}% of frame.")

    # 5. Hue exactness: brown/gray/blue must not read as red at the default
    for img_id, label in ((3, "brown"), (4, "gray"), (5, "blue")):
        got = mod.color_match_share(RED, palettes[img_id], tol)
        assert got == 0, f"{label} matched red at default exactness ({got:.3f})"
    # gray is the trap: it reports hue 0.0, identical to pure red
    assert not mod.color_matches(RED, "#808080", tol), "gray must never match red"
    assert not mod.color_matches(RED, "#7a5038", tol), "brown must not match red"
    assert mod.color_matches(RED, "#c0263a", tol), "crimson should match red"
    assert mod.color_matches(RED, "#e02020", tol), "bright red should match red"
    print("5. Hue match: crimson/bright red in; brown, gray, blue out.")

    # 6. Loosening exactness lets neighbouring hues back in
    loose = mod.exactness_to_hue_tol(0)
    assert mod.color_matches(RED, "#7a5038", loose), "brown should match at loosest"
    assert not mod.color_matches(RED, "#808080", loose), "gray must stay out even at loosest"
    tight = mod.exactness_to_hue_tol(100)
    assert not mod.color_matches(RED, "#c96a4a", tight), "terracotta should drop out at tightest"
    print("6. Exactness slider: widens to brown at 0, drops terracotta at 100, gray never in.")

    # 6b. Saturation guard scales with the picked color, not a flat distance.
    # Picking a vivid red must reject a same-hue but washed-out entry (dusty
    # rose) even though its hue matches exactly — this is the false positive
    # that let desaturated/rust tones through a "close" hue search before the
    # guard was tied to the picked color's own saturation.
    bright = "#e02020"
    assert not mod.color_matches(bright, "#c98a8a", tol), \
        "dusty rose (same hue, low saturation) must not match a vivid red pick"
    assert not mod.color_matches(bright, "#b56a5a", tol), \
        "muted brick must not match a vivid red pick"
    assert mod.color_matches(bright, "#c0263a", tol), "crimson should still match vivid red"
    print("6b. Saturation guard scales with picked color: vivid red rejects dusty/muted same-hue tones.")

    # 7. Picking a neutral matches neutrals, not saturated colors
    gray_tol = mod.exactness_to_hue_tol(mod.DEFAULT_EXACTNESS)
    assert mod.color_matches("#808080", "#8a8a8a", gray_tol), "gray should match near-gray"
    assert not mod.color_matches("#808080", "#b33a3a", gray_tol), "gray must not match red"
    assert not mod.color_matches("#808080", "#1a1a1e", gray_tol), "mid gray vs near-black"
    print("7. Neutral picks: match other neutrals of similar brightness only.")

    # ── End-to-end through /api/search ────────────────────────────────────────
    client = mod.app.test_client()
    setup_r = client.post("/api/setup", json={"email": "test@test.com", "password": "testpass123"})
    assert setup_r.status_code == 200, setup_r.get_json()

    # 8. THE HEADLINE CASE: default coverage keeps the lipstick frame out
    hits = search_ids(client, f"color={RED.replace('#', '%23')}")
    assert 2 in hits, f"red wall must match a red search, got {hits}"
    assert 1 not in hits, f"lipstick frame should NOT match at default coverage, got {hits}"
    assert not ({3, 4, 5} & hits), f"brown/gray/blue leaked in: {hits}"
    print(f"8. Default search for red -> {sorted(hits)} (red wall only). "
          f"The false positive is gone.")

    # 9. Dropping coverage deliberately brings the accent back
    hits_low = search_ids(client, f"color={RED.replace('#', '%23')}&prom=0.5")
    assert {1, 2} <= hits_low, f"low coverage should include the accent, got {hits_low}"
    print(f"9. Coverage 0.5% -> {sorted(hits_low)} (accent back, as intended).")

    # 10. Raising coverage past the wall's own share empties the result
    hits_high = search_ids(client, f"color={RED.replace('#', '%23')}&prom=95")
    assert not hits_high, f"95% coverage should match nothing, got {hits_high}"
    print("10. Coverage 95% -> nothing. Slider spans the full useful range.")

    # 11. Loosening exactness pulls the brown frame in; tightening drops it
    hits_loose = search_ids(client, f"color={RED.replace('#', '%23')}&prom=20&exact=0")
    assert 3 in hits_loose, f"brown should appear at loosest hue match, got {hits_loose}"
    hits_tight = search_ids(client, f"color={RED.replace('#', '%23')}&prom=20&exact=100")
    assert 3 not in hits_tight, f"brown should drop at tightest, got {hits_tight}"
    assert 4 not in hits_loose, "gray must never appear for a red search"
    print("11. /api/search exact= : brown in at 0, out at 100; gray never.")

    # 12. Junk knob values fall back to the defaults instead of erroring
    for junk in ("prom=abc", "exact=abc", "prom=-40", "prom=99999"):
        r = client.get(f"/api/search?color={RED.replace('#', '%23')}&{junk}")
        assert r.status_code == 200, (junk, r.get_json())
    baseline = search_ids(client, f"color={RED.replace('#', '%23')}")
    assert search_ids(client, f"color={RED.replace('#', '%23')}&prom=abc") == baseline
    print("12. Junk prom/exact values fall back to defaults, no 500s.")

    # 13. Palettes stored before V24 (share NULL) still return results
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE colors SET share = NULL WHERE image_id = 2")
    conn.commit()
    conn.close()
    legacy = search_ids(client, f"color={RED.replace('#', '%23')}")
    assert 2 in legacy, (
        "an image whose palette predates V24 must still match on hue alone "
        f"instead of vanishing, got {legacy}"
    )
    print("13. Legacy NULL-share palettes degrade to hue-only matching, not silence.")

    # 14. Colorless search is untouched by the new params
    all_ids = search_ids(client, "prom=50&exact=90")
    assert all_ids == set(CASES), f"knobs must not filter when no color picked, got {all_ids}"
    print("14. No color picked -> knobs ignored, full library returned.")

    # 15. The boot-time self-heal rebuilds pre-V24 palettes.
    #     Image 2's shares were nulled in check 13, standing in for a library
    #     whose palettes were all extracted before this release.
    def null_share_count():
        conn = sqlite3.connect(db_path)
        n = conn.execute("SELECT COUNT(*) FROM colors WHERE share IS NULL").fetchone()[0]
        conn.close()
        return n

    assert null_share_count() > 0, "expected the nulled rows from check 13"
    mod.backfill_palettes()
    for _ in range(100):
        time.sleep(0.05)
        if null_share_count() == 0:
            break
    assert null_share_count() == 0, "backfill left rows without a share"
    # and the rebuilt palette is usable — the wall matches on coverage again
    assert 2 in search_ids(client, f"color={RED.replace('#', '%23')}"), \
        "rebuilt palette should match a red search on coverage"
    print("15. Boot-time backfill rebuilt the pre-V24 palettes; coverage works on them.")

    # 16. Second boot is a no-op — it must not rewrite palettes every restart
    conn = sqlite3.connect(db_path)
    before = conn.execute("SELECT id, image_id, hex, share FROM colors ORDER BY id").fetchall()
    conn.close()
    mod.backfill_palettes()
    time.sleep(0.4)
    conn = sqlite3.connect(db_path)
    after = conn.execute("SELECT id, image_id, hex, share FROM colors ORDER BY id").fetchall()
    conn.close()
    assert before == after, "backfill re-ran on an already-migrated library"
    print("16. Backfill self-disables — a second boot touches nothing.")

    print("\nAll 16 checks passed.")


if __name__ == "__main__":
    main()

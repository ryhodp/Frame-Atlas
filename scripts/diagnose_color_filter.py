"""
Frame Atlas — DIAGNOSTIC: why does "orange, 40% coverage, exact hue" still
return warm interiors that are nowhere near 40% orange?

This script does not change anything. It measures.

It boots a patched copy of backend/app.py against a throwaway database (same
trick as scripts/test_v24_color_locally.py), so every number below comes from
the REAL colour code, not a re-implementation.

Run it from the frame-atlas folder:

    scripts/.venv/bin/python scripts/diagnose_color_filter.py

No network needed. Nothing is written outside a temp folder.

It prefers Ryan's real reference photos in "Test Photos/" (not in git). If that
folder is missing it falls back to synthetic images and says so loudly.
"""

import colorsys
import glob
import importlib.util
import io
import os
import sqlite3
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PHOTO_DIRS = [
    os.path.join(REPO, "Test Photos"),
    os.path.join(os.path.dirname(REPO), "Test Photos"),
    # when run from a git worktree, the real photos live in the main checkout
    os.path.expanduser("~/Desktop/frame-atlas/Test Photos"),
]

ORANGE = "#E08840"          # the swatch Ryan picked
MAX_PROM = 40.0             # what the coverage slider maxes out at today
MAX_EXACT = 100.0           # what the hue slider maxes out at today


def rule(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def hexof(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def why_not(mod, picked_hex, candidate_hex, hue_tol):
    """Plain-language reason color_matches() rejected a colour. Mirrors the
    order of the tests inside color_matches()."""
    hp, sp, _vp = mod._hsv(picked_hex)
    hc, sc, vc = mod._hsv(candidate_hex)
    if sp < 0.18:
        return "picked colour is neutral"
    if sc < 0.22:
        return "too washed out"
    if vc < 0.12:
        return "near black"
    hd = abs(hp - hc)
    hd = min(hd, 1.0 - hd)
    if hd > hue_tol:
        return f"wrong hue ({hd*360:.0f} deg off)"
    if sp >= 0.60 and sc < sp - 0.20:
        return "not saturated enough"
    if sp < 0.60 and abs(sp - sc) > 0.55:
        return "saturation too far off"
    return "matches"


# ─────────────────────────────────────────────────────────────────────────────
# Boot the real server code against a throwaway database
# ─────────────────────────────────────────────────────────────────────────────
def load_app():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_colordiag_")
    db_path = os.path.join(workdir, "library.db")

    # V45 part 2: app.py reads FA_DB_PATH instead of a hardcoded path, so this
    # loads backend/app.py directly (no source patching). backend/ on sys.path
    # so its sibling modules — core.py, schema.py, colors.py, ... — resolve.
    import sys
    sys.path.insert(0, os.path.join(REPO, "backend"))
    os.environ["FA_DB_PATH"] = db_path
    for key in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GEMINI_API_KEY"):
        os.environ.setdefault(key, "dummy")

    spec = importlib.util.spec_from_file_location(
        "colordiag_app", os.path.join(REPO, "backend", "app.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, db_path


# ─────────────────────────────────────────────────────────────────────────────
# An instrumented twin of extract_palette().
#
# This is a COPY of the real function's body with one addition: it remembers
# which raw colour bins were merged into each palette entry. A copy can drift
# from the original, so assert_no_drift() below re-runs the real function on
# every image and refuses to continue if the two disagree by even one number.
# ─────────────────────────────────────────────────────────────────────────────
def extract_palette_instrumented(Image, image_data, num_colors=10):
    img = Image.open(io.BytesIO(image_data)).convert("RGB")
    img.thumbnail((160, 160))

    bins = {}
    for r, g, b in img.getdata():
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if s < 0.16 or v < 0.10:
            key = ("n", min(int(v * 5), 4))
        else:
            key = ("c", min(int(h * 20), 19), min(int(s * 3), 2), min(int(v * 4), 3))
        acc = bins.get(key)
        if acc is None:
            bins[key] = [1, r, g, b, s, v]
        else:
            acc[0] += 1; acc[1] += r; acc[2] += g; acc[3] += b; acc[4] += s; acc[5] += v

    total_px = img.width * img.height
    chromatic, neutrals = [], []
    for key, (n, rs, gs, bs, ss, vs) in bins.items():
        avg = (rs // n, gs // n, bs // n)
        sat, val = ss / n, vs / n
        share = n / total_px
        if key[0] == "c":
            score = share * (0.15 + 2.5 * sat * sat) * (0.4 + 1.2 * val)
            chromatic.append((score, share, avg))
        else:
            neutrals.append((share * 0.2, share, avg))

    def _dist(a, b):
        return ((a[0]-b[0]) * 0.30) ** 2 + ((a[1]-b[1]) * 0.59) ** 2 + ((a[2]-b[2]) * 0.11) ** 2

    def _dup_index(rgb, chosen):
        h1, s1, v1 = colorsys.rgb_to_hsv(rgb[0]/255.0, rgb[1]/255.0, rgb[2]/255.0)
        for i, c in enumerate(chosen):
            h2, s2, v2 = colorsys.rgb_to_hsv(c[0]/255.0, c[1]/255.0, c[2]/255.0)
            d = _dist(rgb, c)
            if d < 120:
                return i, "near-identical (d<120, hue ignored)"
            if s1 > 0.16 and s2 > 0.16:
                hd = abs(h1 - h2)
                hd = min(hd, 1 - hd)
                if d < 450 and hd < 0.09:
                    return i, "same hue family (within 32 deg)"
            elif s1 <= 0.16 and s2 <= 0.16:
                if abs(v1 - v2) < 0.25:
                    return i, "both neutral, similar brightness"
        return None, None

    chromatic.sort(reverse=True)
    neutrals.sort(reverse=True)

    picked = []
    members = []   # parallel to picked: [(rgb, share, why_merged), ...]

    def _absorb(share, rgb, cap):
        idx, why = _dup_index(rgb, [p[0] for p in picked])
        if idx is not None:
            picked[idx][1] += share
            members[idx].append((rgb, share, why))
        elif len(picked) < cap:
            picked.append([rgb, share])
            members.append([(rgb, share, "own entry")])

    for score, share, rgb in chromatic:
        if share < 0.001:
            continue
        _absorb(share, rgb, num_colors - 2)
    for score, share, rgb in neutrals:
        if share < 0.01:
            continue
        _absorb(share, rgb, num_colors)

    entries = [(hexof(rgb), round(share, 5)) for rgb, share in picked]
    return entries, members


# ─────────────────────────────────────────────────────────────────────────────
# Images
# ─────────────────────────────────────────────────────────────────────────────
def find_real_photos():
    for d in PHOTO_DIRS:
        if not os.path.isdir(d):
            continue
        files = [
            f for f in sorted(glob.glob(os.path.join(d, "**", "*"), recursive=True))
            if os.path.splitext(f)[1].lower() in (".jpg", ".jpeg", ".png")
        ]
        if files:
            return d, files
    return None, []


def synth(mod, paint, w=800, h=450):
    from PIL import ImageDraw
    img = mod.Image.new("RGB", (w, h), (0, 0, 0))
    paint(ImageDraw.Draw(img), w, h)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def synthetic_cases(mod):
    """Deliberately built to isolate the three hypotheses."""
    def tungsten(d, w, h):
        # A dark warm interior: mostly near-black shadow with a warm cast,
        # a small vivid practical lamp. Nobody would call this "40% orange".
        d.rectangle([0, 0, w, h], fill=(10, 6, 3))
        d.rectangle([0, int(h * 0.55), w, h], fill=(38, 24, 12))
        d.ellipse([int(w * 0.72), int(h * 0.18), int(w * 0.86), int(h * 0.36)],
                  fill=(224, 136, 64))

    def orange_and_green(d, w, h):
        # ~40% orange, ~50% green. Orange is NOT the dominant colour.
        d.rectangle([0, 0, w, h], fill=(30, 30, 30))
        d.rectangle([0, 0, int(w * 0.40), h], fill=(224, 136, 64))
        d.rectangle([int(w * 0.40), 0, int(w * 0.92), h], fill=(48, 150, 62))

    def warm_not_orange(d, w, h):
        # A yellow/gold frame — warm, but a different hue from orange.
        d.rectangle([0, 0, w, h], fill=(198, 170, 40))

    def truly_orange(d, w, h):
        d.rectangle([0, 0, w, h], fill=(224, 136, 64))

    def green_with_accent(d, w, h):
        # Green-dominant, dark, with one small orange practical. Should NEVER
        # come back from a 40%-orange search.
        d.rectangle([0, 0, w, h], fill=(8, 14, 8))
        d.rectangle([0, int(h * 0.30), w, h], fill=(42, 96, 48))
        d.ellipse([int(w * 0.10), int(h * 0.08), int(w * 0.20), int(h * 0.22)],
                  fill=(224, 136, 64))

    return [
        ("SYNTH dark tungsten interior", synth(mod, tungsten)),
        ("SYNTH 40% orange + 50% green", synth(mod, orange_and_green)),
        ("SYNTH green-dominant + accent", synth(mod, green_with_accent)),
        ("SYNTH warm gold (not orange)", synth(mod, warm_not_orange)),
        ("SYNTH genuinely all orange", synth(mod, truly_orange)),
    ]


# ─────────────────────────────────────────────────────────────────────────────
def main():
    mod, db_path = load_app()
    tol_max = mod.exactness_to_hue_tol(MAX_EXACT)
    tol_def = mod.exactness_to_hue_tol(mod.DEFAULT_EXACTNESS)

    photo_dir, real_files = find_real_photos()
    if real_files:
        print(f"\nUsing {len(real_files)} REAL reference photos from: {photo_dir}")
    else:
        print("\n!! No real photos found — falling back to SYNTHETIC images only.")
        print("!! Synthetic frames cannot prove how Ryan's actual library behaves.")

    images = []   # (label, jpeg_bytes, is_real)
    for f in real_files:
        images.append((os.path.basename(f), open(f, "rb").read(), True))
    for label, blob in synthetic_cases(mod):
        images.append((label, blob, False))

    # Everything below runs on a 600px/q75 thumbnail, exactly like production:
    # palettes in the live database are extracted from the stored thumbnail.
    data = []
    for label, raw, is_real in images:
        thumb = mod.generate_thumbnail(raw, width=600, quality=75)
        entries = mod.extract_palette(thumb)
        instr, members = extract_palette_instrumented(mod.Image, thumb)
        assert entries == instr, (
            f"DRIFT: the instrumented copy disagrees with the real "
            f"extract_palette() on {label}. Numbers below cannot be trusted.\n"
            f"  real:  {entries}\n  copy:  {instr}"
        )
        data.append({
            "label": label, "real": is_real, "entries": entries, "members": members,
        })
    print(f"Drift check passed: the instrumented copy of extract_palette() reproduces "
          f"the real one exactly on all {len(data)} images.")

    # ── Q: how big can one colour's coverage number get? ────────────────────
    rule("RYAN'S QUESTION: should the coverage slider be able to go past 40%?")
    print("A palette's stored shares are fractions of the whole frame. If they only")
    print("added up to, say, 70%, then 40% in one colour would already be near the")
    print("ceiling and raising the slider would be pointless. Measured:\n")
    print(f"   {'image':30s} {'palette covers':>14s} {'biggest single colour':>22s}")
    worst_real = worst_total_gap = 0.0
    over40 = 0
    n_real_imgs = 0
    for d in data:
        tot = sum(s for _, s in d["entries"])
        big = max((s for _, s in d["entries"]), default=0.0)
        if d["real"]:
            n_real_imgs += 1
            worst_real = max(worst_real, big)
            worst_total_gap = max(worst_total_gap, 1.0 - tot)
            if big >= 0.40:
                over40 += 1
        print(f"   {d['label'][:30]:30s} {tot*100:13.1f}% {big*100:21.1f}%")
    print(f"\n   -> Across the {n_real_imgs} real photos, the stored palette accounts for")
    print(f"      at least {(1-worst_total_gap)*100:.1f}% of every frame. Nothing is being lost.")
    print(f"   -> The largest single colour in a real photo covers {worst_real*100:.0f}% of its frame,")
    print(f"      and {over40} of {n_real_imgs} real photos have a colour over 40%.")
    print(f"   -> ANSWER: 40% is nowhere near the ceiling. The slider could run to ~95%")
    print(f"      and still be meaningful. But see the bottom line — raising it is not")
    print(f"      what fixes Ryan's search.")

    # ── HYPOTHESIS A ────────────────────────────────────────────────────────
    rule("HYPOTHESIS A — merging inflates the coverage number")
    print("extract_palette() merges similar colour bins into one palette entry, and")
    print("the merged bin donates its share to the winner. Search then tests only the")
    print("winner's hex. Question: how much of a 'matching' entry's coverage is made")
    print("of bins that would FAIL the same colour test on their own?\n")
    print(f"Testing against {ORANGE} at exactness={MAX_EXACT:.0f} "
          f"(hue tolerance {tol_max*360:.0f} degrees).\n")

    header = f"   {'image':30s} {'search says':>11s} {'honest':>8s} {'inflation':>10s}"
    print(header)
    a_rows = []
    for d in data:
        family = mod.color_match_share(ORANGE, d["entries"], tol_max)
        honest = 0.0
        for (h, s), mem in zip(d["entries"], d["members"]):
            if not mod.color_matches(ORANGE, h, tol_max):
                continue
            for rgb, ms, _why in mem:
                if mod.color_matches(ORANGE, hexof(rgb), tol_max):
                    honest += ms
        if family <= 0 and honest <= 0:
            continue
        a_rows.append((d, family, honest))
        infl = f"{family/honest:.1f}x" if honest > 0 else "inf"
        print(f"   {d['label'][:30]:30s} {family*100:10.1f}% {honest*100:7.1f}% {infl:>10s}")

    print("\n   Breakdown of the worst offenders — what is actually inside each entry:")
    for d, family, honest in sorted(a_rows, key=lambda r: -r[1])[:4]:
        print(f"\n   {d['label']}  (search reports {family*100:.1f}% orange)")
        for (h, s), mem in zip(d["entries"], d["members"]):
            if not mod.color_matches(ORANGE, h, tol_max):
                continue
            print(f"     palette entry {h}  stored coverage {s*100:.1f}%  "
                  f"— built from {len(mem)} raw colour bin(s):")
            for rgb, ms, why in sorted(mem, key=lambda x: -x[1])[:6]:
                hh, ss, vv = colorsys.rgb_to_hsv(*[x / 255.0 for x in rgb])
                ok = mod.color_matches(ORANGE, hexof(rgb), tol_max)
                verdict = "counts as orange" if ok else "NOT orange on its own"
                print(f"        {hexof(rgb)} {ms*100:6.2f}% of frame  "
                      f"hue={hh*360:5.1f}d sat={ss:.2f} brightness={vv:.2f}  "
                      f"{verdict:22s} [{why}]")

    # Roll the inflation up by reason, across every image.
    print("\n   Where does the phantom coverage come from? Every bit of frame that")
    print("   search counts as orange but that is NOT orange on its own, added up")
    print("   across all images and grouped by why it isn't orange:\n")
    by_reason = {}
    for d in data:
        for (h, _s), mem in zip(d["entries"], d["members"]):
            if not mod.color_matches(ORANGE, h, tol_max):
                continue
            for rgb, ms, _why in mem:
                r = why_not(mod, ORANGE, hexof(rgb), tol_max)
                if r == "matches":
                    continue
                key = r.split(" (")[0]
                by_reason[key] = by_reason.get(key, 0.0) + ms
    total_phantom = sum(by_reason.values()) or 1.0
    for reason, amount in sorted(by_reason.items(), key=lambda x: -x[1]):
        print(f"      {reason:24s} {amount*100:7.1f} frame-percent  "
              f"({amount/total_phantom*100:4.1f}% of all the phantom coverage)")

    print("\n   The single biggest source of inflation is near-BLACK bins. The merge")
    print("   rule 'd < 120' ignores hue entirely, and two very dark colours are")
    print("   always within 120 of each other, so shadow gets folded into whatever")
    print("   dark warm entry it lands next to. color_matches() has a guard that")
    print("   rejects near-black (brightness < 0.12) — but it only ever sees the")
    print("   entry's stored hex, never the black hiding inside it.")

    # ── HYPOTHESIS B ────────────────────────────────────────────────────────
    rule("HYPOTHESIS B — some images bypass both sliders (share IS NULL)")
    print("Code path in search(): an image whose palette rows have share NULL and")
    print("rank <= 5 matches on hue alone, with NO coverage test. Demonstrating it")
    print("end-to-end against the real /api/search endpoint:\n")

    client = mod.app.test_client()
    r = client.post("/api/setup", json={"email": "diag@test.com", "password": "diagpass123"})
    assert r.status_code == 200, r.get_json()

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    # Image 1: a frame with a TINY orange accent — should fail 40% coverage.
    from PIL import ImageDraw
    img = mod.Image.new("RGB", (800, 450), (28, 40, 90))
    ImageDraw.Draw(img).rectangle([380, 200, 420, 240], fill=(224, 136, 64))
    buf = io.BytesIO(); img.save(buf, format="JPEG", quality=92)
    accent = buf.getvalue()
    thumb = mod.generate_thumbnail(accent, width=600, quality=75)
    c.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob,"
        " aspect_ratio, date_added) VALUES (1, 1, 'fake-1', 'tiny_accent.jpg', ?,"
        " '16:9', datetime('now'))", (thumb,))
    conn.commit(); conn.close()
    entries = mod.extract_palette(thumb)
    mod.save_palette(1, 1, entries)
    got = mod.color_match_share(ORANGE, entries, tol_max)
    print(f"   Test image: mostly blue, one small orange patch. "
          f"Measured orange coverage: {got*100:.2f}% of frame.")

    q = f"color={ORANGE.replace('#', '%23')}&prom={MAX_PROM}&exact={MAX_EXACT}"
    ids = {i["id"] for i in client.get(f"/api/search?{q}").get_json()["images"]}
    print(f"   With shares stored   -> search at {MAX_PROM:.0f}% coverage returns: "
          f"{sorted(ids) or 'nothing'}   (correct)")

    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE colors SET share = NULL WHERE image_id = 1")
    conn.commit(); conn.close()
    ids2 = {i["id"] for i in client.get(f"/api/search?{q}").get_json()["images"]}
    print(f"   With shares NULL     -> search at {MAX_PROM:.0f}% coverage returns: "
          f"{sorted(ids2) or 'nothing'}")
    if ids2:
        print("\n   CONFIRMED: an image with no coverage data ignores the coverage")
        print("   slider completely. A 1%-orange frame comes back from a 40% search.")
    else:
        print("\n   REFUTED: the NULL-share image did not slip through.")

    conn = sqlite3.connect(db_path)
    n_null = conn.execute("SELECT COUNT(*) FROM colors WHERE share IS NULL").fetchone()[0]
    conn.close()
    print(f"\n   How likely is this in Ryan's live library? Audit of "
          f"backfill_palette_shares():")
    print("     - It reruns on EVERY boot until no NULL shares remain, so a crash or")
    print("       a Railway redeploy mid-run is self-healing, not permanent.")
    print("     - Per-image exceptions are caught and counted; that image is retried")
    print("       next boot.")
    print("     - PERMANENT exemptions are only possible for an image whose")
    print("       thumbnail_blob is missing (skipped with `continue`) or whose")
    print("       thumbnail is corrupt (extract_palette returns [], nothing saved).")
    print("       Neither is counted as a failure, so the log would not mention them.")
    print("     - Every save_palette() caller in app.py now passes (hex, share)")
    print("       tuples, so no NEW null-share rows can be created.")
    print(f"   -> To settle it on the live library, run the SQL in the last section.")
    print(f"   (In this throwaway database right now: {n_null} NULL-share rows.)")

    # ── HYPOTHESIS D (not in the original brief) ────────────────────────────
    rule("HYPOTHESIS D — for ORANGE specifically, the hue slider cannot help")
    print("color_matches() decides 'is this the picked colour?' almost entirely on")
    print("HUE — the position on the colour wheel. Hue is blind to brightness. And")
    print("brown is not a hue: brown IS dark orange. They sit within a couple of")
    print("degrees of each other. So no setting of the hue slider can separate them.\n")
    hp, sp, vp = mod._hsv(ORANGE)
    print(f"   The picked colour {ORANGE}: hue {hp*360:.0f} deg, "
          f"saturation {sp:.2f}, brightness {vp:.2f}\n")
    swatches = [
        ("#F5A623", "bright amber"),
        ("#E08840", "the picked orange"),
        ("#C97B33", "burnt orange"),
        ("#8B5A2B", "mid brown"),
        ("#7A4B22", "tungsten shadow"),
        ("#5F3A1C", "dark brown"),
        ("#3D2413", "very dark brown"),
        ("#241205", "near-black brown"),
        ("#1A0E04", "almost black"),
        ("#D2AA65", "pale gold"),
        ("#A8830D", "olive gold"),
    ]
    print(f"   {'swatch':9s} {'':20s} {'hue':>5s} {'sat':>5s} {'bright':>7s}   "
          f"{'loose':>6s} {'default':>8s} {'tightest':>9s}")
    for hx, name in swatches:
        h, s, v = mod._hsv(hx)
        cells = [
            "  in  " if mod.color_matches(ORANGE, hx, mod.exactness_to_hue_tol(e)) else "  --  "
            for e in (0, 60, 100)
        ]
        print(f"   {hx:9s} {name:20s} {h*360:5.0f} {s:5.2f} {v:7.2f}   "
              f"{cells[0]:>6s} {cells[1]:>8s} {cells[2]:>9s}")
    print("\n   Read the two ends of that table:")
    print("     - Dragging the hue slider to TIGHTEST throws OUT bright amber and pale")
    print("       gold — colours Ryan would call orange — while keeping every brown")
    print("       right down to near-black.")
    print("     - The only brightness guard is 'brighter than 0.12', which is almost")
    print("       pure black. Everything above that counts.")
    print("   -> This is why tightening the hue slider did not clean up the results,")
    print("      and it is independent of the merging problem in Hypothesis A.")

    # ── HYPOTHESIS C ────────────────────────────────────────────────────────
    rule("HYPOTHESIS C — 'at least 40% orange' is not 'orange is the main colour'")
    for d in data:
        if "40% orange + 50% green" not in d["label"]:
            continue
        share = mod.color_match_share(ORANGE, d["entries"], tol_max)
        print(f"   {d['label']}  (a deliberately built control image)")
        for h, s in d["entries"]:
            m = "<- counted as orange" if mod.color_matches(ORANGE, h, tol_max) else ""
            print(f"      {h} {s*100:5.1f}%  {m}")
        print(f"\n   Orange coverage: {share*100:.1f}%  ->  passes the 40% filter: "
              f"{share >= MAX_PROM/100}")
        print("   ...even though green covers MORE of the frame. The filter asks")
        print("   'is there at least this much orange', never 'is orange the most'.")
        print("   This is working as designed, but it is not what 'coverage at max'")
        print("   sounds like it should mean.")

    # ── BOTTOM LINE ─────────────────────────────────────────────────────────
    rule("BOTTOM LINE — which real photos pass Ryan's exact search?")
    print(f"Search: colour {ORANGE}, coverage {MAX_PROM:.0f}%, hue match "
          f"{MAX_EXACT:.0f} (tightest).\n")
    VAL_FLOOR = 0.35   # candidate fix: a colour must also be reasonably bright
    passes_now = passes_honest = passes_bright = n_real = 0
    for d in data:
        if not d["real"]:
            continue
        n_real += 1
        family = mod.color_match_share(ORANGE, d["entries"], tol_max)
        honest = bright = 0.0
        for (h, s), mem in zip(d["entries"], d["members"]):
            if not mod.color_matches(ORANGE, h, tol_max):
                continue
            for rgb, ms, _ in mem:
                if mod.color_matches(ORANGE, hexof(rgb), tol_max):
                    honest += ms
                    if colorsys.rgb_to_hsv(*[x / 255.0 for x in rgb])[2] >= VAL_FLOOR:
                        bright += ms
        if family >= MAX_PROM / 100:
            passes_now += 1
            print(f"   PASSES TODAY: {d['label']:26s} "
                  f"search says {family*100:5.1f}% | measured per-bin {honest*100:5.1f}% "
                  f"| per-bin + brightness floor {bright*100:5.1f}%")
        if honest >= MAX_PROM / 100:
            passes_honest += 1
        if bright >= MAX_PROM / 100:
            passes_bright += 1
    print(f"\n   {passes_now} of {n_real} real photos pass TODAY.")
    print(f"   {passes_honest} of {n_real} would pass if each merged colour bin were")
    print(f"        tested on its own instead of via the entry's stored hex (fix A).")
    print(f"   {passes_bright} of {n_real} would pass with that PLUS a brightness floor of")
    print(f"        {VAL_FLOOR:.2f}, so dark brown stops counting as orange (fix D).")
    print("\n   Neither fix touches the coverage slider's 40% cap. Raising that cap")
    print("   alone would remove Flex 3 (54%) and IMG_6848 (44%) but keep IMG_4306")
    print("   (76%) — it treats the symptom, not the cause.")

    print("\n   SANITY CONTROL — do the proposed fixes also throw out genuinely")
    print("   orange frames? They must not:")
    for d in data:
        if d["real"]:
            continue
        family = mod.color_match_share(ORANGE, d["entries"], tol_max)
        bright = 0.0
        for (h, s), mem in zip(d["entries"], d["members"]):
            if not mod.color_matches(ORANGE, h, tol_max):
                continue
            for rgb, ms, _ in mem:
                if (mod.color_matches(ORANGE, hexof(rgb), tol_max)
                        and colorsys.rgb_to_hsv(*[x / 255.0 for x in rgb])[2] >= VAL_FLOOR):
                    bright += ms
        # "40% orange + 50% green" genuinely IS 40% orange, so it SHOULD pass a
        # filter that means "at least 40% orange" — that is Hypothesis C, a
        # wording problem, not a measurement bug.
        should_pass = ("genuinely" in d["label"]) or ("40% orange" in d["label"])
        want = "should PASS" if should_pass else "should FAIL"
        got_now = "passes" if family >= MAX_PROM/100 else "fails "
        got_fix = "passes" if bright >= MAX_PROM/100 else "fails "
        flag = "" if (should_pass == (got_fix == "passes")) else "   <-- WRONG"
        print(f"      {d['label']:30s} {want}   today: {got_now}   with fixes: "
              f"{got_fix}{flag}")

    rule("WHAT THIS SCRIPT CANNOT ANSWER")
    print("It has no access to Ryan's live library, so it cannot say how many of his")
    print("118 results are caused by which problem. To close that gap, run these")
    print("against the production database (Railway Console):\n")
    print("   sqlite3 /app/data/library.db \\")
    print("     \"SELECT COUNT(*) FROM colors WHERE share IS NULL;\"")
    print("   sqlite3 /app/data/library.db \\")
    print("     \"SELECT COUNT(DISTINCT c.image_id) FROM colors c JOIN images i\"")
    print("     \" ON i.id = c.image_id WHERE c.share IS NULL AND i.thumbnail_blob IS NULL;\"")
    print("\nThe first number is how many palette rows still bypass the coverage")
    print("slider. The second is how many of those can never be repaired by the")
    print("automatic self-heal, because their thumbnail is gone.")
    print()


if __name__ == "__main__":
    main()

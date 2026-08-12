"""Frame Atlas — colour extraction and colour matching.

Split out of app.py in V45 (Day 27). Everything here is pure: it takes image
bytes, hex strings and numbers, and returns numbers. Nothing in this file
touches the database, Google Drive, Flask or the session — which is what makes
it safe to move without changing a single test script. app.py imports these
names into its own namespace, so every existing caller and every test that
reads `mod.color_matches` keeps working unchanged.

The DB-facing parts of colour deliberately stayed behind in app.py:
save_palette() and backfill_palettes() both open connections, and moving them
would break the test harness's single-file copy trick (see the module docstring
in any scripts/test_*_locally.py).

Two calibration facts worth knowing before touching anything here, both learned
the hard way — the long-form reasoning is in CLAUDE.md's "Colour" section:

  · Hue angle, not color_distance(), decides whether two colours are close.
    The weighted-RGB metric is green-heavy and rates brown a closer match to
    red than pink is.
  · Saturation guards are mandatory. Grey reports hue 0.0, identical to pure
    red, and would otherwise match it perfectly.
"""

import io

from PIL import Image

# ── V33: PALETTE MERGE GUARDS ──────────────────────────────────────────────
# A palette entry means "this color FAMILY", and merged-away shades donate
# their share to the family (V24). That is what makes a wall split into
# dark/mid/bright red read as one big red. It is also what made colour search
# lie: nothing stopped a family from absorbing something it had no business
# absorbing, and the search only ever tests the family's stored representative.
#
# PALETTE_DARK_V matches the near-black guard in color_matches() on purpose.
# Below it, hue is numerically unstable and physically meaningless — the
# difference between #020100 and #000102 is one bit of sensor noise but a
# 180-degree swing in reported hue. Anything under this is shadow, whatever
# its nominal saturation says.
PALETTE_DARK_V = 0.12
PALETTE_GRAY_S = 0.16        # same cut the binning step uses
PALETTE_MERGE_HUE_TOL = 0.09  # 32 degrees — a family may span a real gradient


def _is_shadow_or_gray(s, v):
    """Is this HSV triple one whose hue carries no usable information?"""
    return s < PALETTE_GRAY_S or v < PALETTE_DARK_V


# Bumped whenever extract_palette()'s output would change for the same input.
# backfill_palettes() rebuilds anything stamped older than this, so a change
# to the algorithm can't leave the library half-old and half-new. NULL means
# "extracted before versioning existed" and is always rebuilt.
PALETTE_VERSION = 2


def extract_palette(image_data, num_colors=10):
    """Vibrance-weighted palette. Colors are scored by area x saturation, so a
    small patch of vivid red outranks a large gray wall. Binning happens in HSV,
    which keeps vivid regions from being averaged into their muddy surroundings
    (the old quantize approach turned bright gems on dark backgrounds into
    sludge). Vivid colors fill the top ranks — color search reads those — and
    neutrals ride along at the end."""
    try:
        import colorsys
        img = Image.open(io.BytesIO(image_data)).convert('RGB')
        img.thumbnail((160, 160))

        bins = {}
        for r, g, b in img.getdata():
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            if s < 0.16 or v < 0.10:
                key = ('n', min(int(v * 5), 4))
            else:
                key = ('c', min(int(h * 20), 19), min(int(s * 3), 2), min(int(v * 4), 3))
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
            if key[0] == 'c':
                score = share * (0.15 + 2.5 * sat * sat) * (0.4 + 1.2 * val)
                chromatic.append((score, share, avg))
            else:
                neutrals.append((share * 0.2, share, avg))

        def _dist(a, b):
            return ((a[0]-b[0]) * 0.30) ** 2 + ((a[1]-b[1]) * 0.59) ** 2 + ((a[2]-b[2]) * 0.11) ** 2

        def _dup_index(rgb, chosen):
            # Hue-aware dedupe: the brightness-weighted distance alone thinks
            # dark green == dark brown and white == pale sage. Colors from
            # different hue families never merge unless nearly identical.
            # Returns the index of the color that absorbs this one, else None.
            #
            # V33: a bin may only join a family it actually belongs to. HSV
            # saturation is meaningless once a color is nearly black — #020100
            # reports saturation 1.0 and hue 30, arithmetically indistinguish-
            # able from a vivid orange — so the old "both saturated" branch
            # happily merged pure shadow into a dark warm entry, and the
            # shadow then DONATED its share to it. Measured on Flex 3.jpg:
            # search reported 54% orange where only 9.5% of the frame was
            # orange, the gap being a #020100 bin covering 39% of the frame.
            # Classify by the same rule the binning step above uses, and never
            # merge across that divide in either direction.
            h1, s1, v1 = colorsys.rgb_to_hsv(rgb[0]/255.0, rgb[1]/255.0, rgb[2]/255.0)
            n1 = _is_shadow_or_gray(s1, v1)
            for i, c in enumerate(chosen):
                h2, s2, v2 = colorsys.rgb_to_hsv(c[0]/255.0, c[1]/255.0, c[2]/255.0)
                if _is_shadow_or_gray(s2, v2) != n1:
                    continue
                if n1:
                    if abs(v1 - v2) < 0.25:  # spread neutrals across brightness
                        return i
                    continue
                d = _dist(rgb, c)
                if d < 120:  # nearly identical regardless of hue
                    return i
                hd = abs(h1 - h2)
                hd = min(hd, 1 - hd)  # hue wraps around the color wheel
                if d < 450 and hd < PALETTE_MERGE_HUE_TOL:
                    return i
            return None

        chromatic.sort(reverse=True)
        neutrals.sort(reverse=True)

        # V24: `picked` carries [rgb, share]. A merged-away shade donates its
        # share to whichever color absorbed it, so the stored number means
        # "how much of the frame this color FAMILY covers" — a wall split into
        # dark/mid/bright red reads as one big red, which is what the eye sees
        # and what the prominence filter needs. Keep scanning after the palette
        # fills so those late shades still get counted; only new entries stop.
        picked = []

        def _absorb(share, rgb, cap):
            idx = _dup_index(rgb, [p[0] for p in picked])
            if idx is not None:
                picked[idx][1] += share
            elif len(picked) < cap:
                picked.append([rgb, share])

        for score, share, rgb in chromatic:
            if share < 0.001:  # under ~0.1% of pixels = JPEG noise, not a color
                continue
            _absorb(share, rgb, num_colors - 2)
        for score, share, rgb in neutrals:
            if share < 0.01:  # a neutral must cover at least 1% of the frame
                continue
            _absorb(share, rgb, num_colors)

        return [('#%02x%02x%02x' % tuple(rgb), round(share, 5)) for rgb, share in picked]
    except Exception:
        return []


def hex_to_rgb(hex_color):
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def color_distance(hex_a, hex_b):
    ra, ga, ba = hex_to_rgb(hex_a)
    rb, gb, bb = hex_to_rgb(hex_b)
    # Weighted RGB distance — human eyes are most sensitive to green
    return ((rb - ra) * 0.30) ** 2 + ((gb - ga) * 0.59) ** 2 + ((bb - ba) * 0.11) ** 2


# ── V24: COLOR SEARCH — PROMINENCE + HUE EXACTNESS ─────────────────────────
# Two knobs replace the old single fixed distance threshold:
#   prominence — how much of the frame the color must cover (summed share)
#   exactness  — how close in hue it has to be
#
# Hue replaces weighted-RGB for the closeness test because that metric is
# green-heavy and so reads "darker and duller" as "different color": measured
# against the red swatch, brown scored 461 and rust 406, both inside the old
# 1000 threshold, while pink (a far better red match) scored 1633. In hue
# terms the split is clean — real reds land under 14 degrees, brown/rust/
# orange sit at 22-27. Saturation guards are mandatory: gray reports hue 0.0,
# identical to pure red, and would otherwise match it perfectly.

EXACTNESS_LOOSE_DEG = 30.0   # exactness = 0
EXACTNESS_TIGHT_DEG = 6.0    # exactness = 100
DEFAULT_EXACTNESS = 60.0     # ~15 deg: keeps crimson/brick/burgundy, drops brown/rust
DEFAULT_PROMINENCE = 6.0     # percent of frame

# V33: the exactness slider also controls a BRIGHTNESS tolerance, because for
# warm colors hue alone physically cannot do the job. Brown is not its own
# hue — brown IS dark orange, sitting within a couple of degrees of it on the
# wheel (measured: picked orange #E08840 at 27 deg, mid brown #8B5A2B at 29,
# near-black brown #241205 at 25). Dragging exactness to maximum therefore
# used to discard bright amber and pale gold — colors Ryan would call orange —
# while keeping every brown down to brightness 0.14. That is backwards.
#
# This is deliberately NOT a third slider. Hue-tightness and brightness-
# tightness point the same direction for every warm color, and the control
# already reads as "be pickier", so folding them keeps two knobs instead of
# three. The V24 red calibration is unaffected: red's neighbours differ in
# hue, so the hue test was already doing the work there.
EXACTNESS_LOOSE_VAL = 0.90   # exactness = 0   -> effectively unconstrained
EXACTNESS_TIGHT_VAL = 0.22   # exactness = 100 -> must be about as bright


def _hsv(hex_color):
    import colorsys
    r, g, b = hex_to_rgb(hex_color)
    return colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)


def exactness_to_hue_tol(exactness):
    """0-100 slider -> allowed hue difference as a fraction of the wheel."""
    e = max(0.0, min(100.0, float(exactness)))
    deg = EXACTNESS_LOOSE_DEG - (e / 100.0) * (EXACTNESS_LOOSE_DEG - EXACTNESS_TIGHT_DEG)
    return deg / 360.0


def exactness_to_value_tol(exactness):
    """0-100 slider -> allowed brightness difference (HSV value, 0-1)."""
    e = max(0.0, min(100.0, float(exactness)))
    return EXACTNESS_LOOSE_VAL - (e / 100.0) * (EXACTNESS_LOOSE_VAL - EXACTNESS_TIGHT_VAL)


def color_matches(picked_hex, candidate_hex, hue_tol, value_tol=None):
    """Does one palette entry count as the picked color?

    value_tol is the V33 brightness rule. It defaults to None = no brightness
    constraint beyond the hard near-black floor, which keeps every non-search
    caller (notably the duplicate-detection color gate, calibrated in V29/V30
    to 0 false positives across 38 cases) behaving exactly as before."""
    try:
        hp, sp, vp = _hsv(picked_hex)
        hc, sc, vc = _hsv(candidate_hex)
    except Exception:
        return False

    # Picking a neutral (gray/black/cream): hue carries no information, so
    # match on "also neutral, and about as bright."
    if sp < 0.18:
        if sc >= 0.28:
            return False
        return abs(vp - vc) <= 0.22

    # Picked a real color — a washed-out or near-gray palette entry is not it,
    # however close its nominal hue reads.
    if sc < 0.22:
        return False
    # Near-black entries have unstable hue; don't let them count. This floor
    # is physical, not a preference, so it applies at every slider setting.
    if vc < PALETTE_DARK_V:
        return False
    # V33: brightness distance. Symmetric on purpose — picking a bright orange
    # rejects dark brown, and picking a dark brown rejects bright orange.
    if value_tol is not None and abs(vp - vc) > value_tol:
        return False

    hd = abs(hp - hc)
    hd = min(hd, 1.0 - hd)  # hue wraps around the wheel
    if hd > hue_tol:
        return False

    # Sanity on saturation distance so a pale pastel doesn't read as a
    # saturated hue at loose settings (and vice versa). Scaled to the picked
    # color's own saturation: picking a vivid red (sp high) should reject
    # muddy rust/brown (sc low) even though their hue is close, while picking
    # a muted/dusty color shouldn't demand the candidate be equally vivid.
    if sp >= 0.60:
        return sc >= sp - 0.20
    return abs(sp - sc) <= 0.55


def color_match_share(picked_hex, entries, hue_tol, value_tol=None):
    """Summed share of every palette entry matching the picked color.

    entries: list of (hex, share). A wall extracted as dark/mid/bright red
    counts once, at its combined size — matching how the eye reads the frame.
    Entries stored before V24 have share NULL; those fall back to counting as
    a match with no size information (see caller)."""
    total = 0.0
    for hex_color, share in entries:
        if share is None:
            continue
        if color_matches(picked_hex, hex_color, hue_tol, value_tol):
            total += share
    return total


# V27: color-overlap check for near-duplicate detection. phash only reads
# brightness LAYOUT (dark frame, one bright patch) — two completely
# different photos can share that shape (e.g. a night street and a lit
# doorway) and get flagged as duplicates even though no human would say they
# match. Requiring the actual colors to line up too, using the same
# hue-based closeness test as color search, catches that.
DUP_COLOR_HUE_TOL = exactness_to_hue_tol(DEFAULT_EXACTNESS)
DUP_COLOR_MIN_OVERLAP = 0.5  # each side's own colorful content must be at least half explained by the other's


def _chromatic_entries(entries):
    """Palette entries with enough color to carry hue information. Near-
    black/white/gray entries are excluded — otherwise two unrelated dark
    photos would "match" purely because both are mostly black background."""
    out = []
    for hex_color, share in entries:
        if share is None:
            continue
        try:
            _, s, v = _hsv(hex_color)
        except Exception:
            continue
        if s >= 0.22 and v >= 0.12:
            out.append((hex_color, share))
    return out


def palettes_overlap(entries_a, entries_b):
    """True if two palettes describe the same actual colors, not just a
    similar brightness shape. entries: list of (hex, share) from the colors
    table. If either image has no real color signal (b&w, or palette not
    extracted yet), this doesn't block a match — phash alone decides, same
    graceful-degradation rule color search already uses for pre-V24 rows."""
    chroma_a = _chromatic_entries(entries_a)
    chroma_b = _chromatic_entries(entries_b)
    if not chroma_a or not chroma_b:
        return True

    def matched_fraction(entries, other):
        total = sum(share for _, share in entries)
        if total <= 0:
            return 0.0
        matched = sum(share for hex_color, share in entries
                      if any(color_matches(hex_color, other_hex, DUP_COLOR_HUE_TOL)
                             for other_hex, _ in other))
        return matched / total

    return (matched_fraction(chroma_a, chroma_b) >= DUP_COLOR_MIN_OVERLAP
            and matched_fraction(chroma_b, chroma_a) >= DUP_COLOR_MIN_OVERLAP)

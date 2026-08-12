"""Frame Atlas — perceptual fingerprints for near-duplicate detection.

Split out of app.py in V45 (Day 27). Pure: image bytes in, hashes and numbers
out. No database, no Drive, no Flask.

Duplicate detection runs three gates and ALL of them must agree — phash (a
cheap pre-filter) → signature (does it actually look alike?) → palette (is it
actually the same colour, see colors.py). This file holds the first two. The
order is the same in find_duplicates() and _ingest_image() so the Duplicate
Review screen and the live upload check can never disagree.

The one rule not to relitigate: phash alone is not evidence, at any grid size.
The reasoning is in compute_signature()'s comment below and in CLAUDE.md.
"""

import io

from PIL import Image

# V30: the fingerprint grid. Was 8x8/64 bits through V29; see compute_phash.
PHASH_GRID = 16
PHASH_BITS = PHASH_GRID * PHASH_GRID   # 256
PHASH_HEX_LEN = PHASH_BITS // 4        # 64 hex characters

def compute_phash(image_data):
    """Perceptual 'difference hash' — a 256-bit visual fingerprint.

    Shrinks the image to a 17x16 grayscale grid and records, for each pixel,
    whether it's brighter than its right-hand neighbor. Two visually identical
    images (even resized, screenshotted, or re-saved) produce nearly identical
    fingerprints; counting differing bits (hamming distance) measures how
    visually different they are.

    V30 widened the grid from 8x8. This hash is now only a fast PRE-FILTER,
    never the decider — see compute_signature() for why it cannot be trusted
    alone no matter how fine the grid gets."""
    try:
        img = Image.open(io.BytesIO(image_data)).convert('L').resize(
            (PHASH_GRID + 1, PHASH_GRID), Image.Resampling.LANCZOS)
        px = list(img.getdata())
        bits = 0
        stride = PHASH_GRID + 1
        for row in range(PHASH_GRID):
            for col in range(PHASH_GRID):
                bits = (bits << 1) | (1 if px[row * stride + col] > px[row * stride + col + 1] else 0)
        return f'{bits:0{PHASH_HEX_LEN}x}'
    except Exception:
        return None

def phash_distance(a, b):
    """Differing bits between two fingerprints. Mismatched lengths mean one
    side predates the V30 widening and hasn't been backfilled yet — report
    them as maximally different rather than XOR-ing a 64-bit hash against a
    256-bit one, which would produce a meaningless (and often small) number."""
    if not a or not b or len(a) != len(b):
        return PHASH_BITS + 1
    return bin(int(a, 16) ^ int(b, 16)).count('1')

# At or below this many differing bits (out of 256), two images MAY be
# near-duplicates. Deliberately generous: measured on Ryan's real reference
# photos, a resized + heavily recompressed copy scored at worst 13, while
# genuinely unrelated pairs scored 47+. The gap is wide here, but it collapses
# on flat frames (see compute_signature), so this only nominates candidates —
# signature and colour still have to agree before anything is called a dupe.
PHASH_NEAR_DUP_THRESHOLD = 20

# ── V30: SIGNATURE — the check that actually decides ────────────────────────
# The difference-hash asks "is this pixel brighter than its neighbour?" On a
# soft, dark, letterboxed frame the answer is "no" almost everywhere, so the
# hash comes out nearly blank — measured on real moody stills, only 5-12 of
# the old 64 bits carried any signal. Two mostly-blank fingerprints are
# MATHEMATICALLY FORCED to look alike: their distance can never exceed the
# number of bits they set between them. That is why three unrelated warm,
# letterboxed frames kept grouping as duplicates, and why widening the grid
# alone does not fix it — at 16x16 those same frames set only 16 of 256 bits.
#
# The signature keeps ACTUAL brightness values instead of only their ordering,
# so a flat frame still describes itself. Contrast-normalising (subtract mean,
# divide by standard deviation) makes it immune to exposure and compression
# shifts, which is what lets the cutoff sit so far from real duplicates.
#
# Measured over 19 of Ryan's real reference photos (171 unrelated pairs):
#     resized 55% + JPEG q55 copies ... 0.004 - 0.029
#     genuinely unrelated pairs ....... 0.463 - 2.0+
# a 16x separation. The cutoff sits between, nearer the duplicate end.
SIGNATURE_GRID = 16
SIGNATURE_MAX_DISTANCE = 0.15

def compute_signature(image_data):
    """Contrast-normalised tiny grayscale. Returns a list of floats, or None."""
    try:
        img = Image.open(io.BytesIO(image_data)).convert('L').resize(
            (SIGNATURE_GRID, SIGNATURE_GRID), Image.Resampling.LANCZOS)
        px = list(img.getdata())
        if not px:
            return None
        mean = sum(px) / len(px)
        sd = (sum((p - mean) ** 2 for p in px) / len(px)) ** 0.5
        if sd < 1e-6:
            # A completely uniform frame (a solid colour card). It has no
            # structure to compare, so give it a flat signature — the colour
            # check is what will tell two solid cards apart.
            return [0.0] * len(px)
        return [(p - mean) / sd for p in px]
    except Exception:
        return None

def signature_distance(a, b):
    """RMS difference between two signatures. 0 = identical structure."""
    if not a or not b or len(a) != len(b):
        return None
    return (sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)) ** 0.5

def signatures_match(a, b):
    """Do two images actually look alike? Unlike the fingerprint this stays
    meaningful on flat/dark/letterboxed frames. A missing signature (an
    unreadable thumbnail) does not veto — phash and colour still apply."""
    d = signature_distance(a, b)
    if d is None:
        return True
    return d <= SIGNATURE_MAX_DISTANCE


"""Frame Atlas — thumbnail generation and aspect-ratio maths.

Split out of app.py in V45 (Day 27). Pure: image bytes and strings in, bytes
and labels out. No database, no Drive, no Flask.

Two facts here are load-bearing and easy to undo by accident:

  · generate_thumbnail() MUST apply EXIF orientation before resizing (V36).
    A phone photo shot sideways otherwise gets re-saved with the sideways
    pixels baked in and the rotation tag dropped. The full-res view still
    looks right — it streams the untouched Drive original, tag intact — so
    the bug only ever shows up in the grid.
  · ar_float_from_str() is the single source of truth for parsing a stored
    aspect ratio. The tile label and /api/search's ar filter have to agree on
    which bucket an image falls in, or results won't match what's on screen.
"""

import io
import re

from PIL import Image, ImageOps

def generate_thumbnail(image_data, width=800, quality=85):
    try:
        img = Image.open(io.BytesIO(image_data))
        # Phones store rotation as an EXIF tag, not in the pixels themselves.
        # Bake it in now (V36) — otherwise the re-saved JPEG below has no
        # orientation tag left and the thumbnail comes out sideways, even
        # though the untouched original (served straight from Drive) still
        # carries the tag and displays upright. Same fix as the crop path.
        img = ImageOps.exif_transpose(img)
        aspect_ratio = img.width / img.height if img.height > 0 else 1
        # Never upscale — a source narrower than the target stays at native size
        if img.width > width:
            height = int(width / aspect_ratio)
            img = img.resize((width, height), Image.Resampling.LANCZOS)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        thumb_io = io.BytesIO()
        img.save(thumb_io, format='JPEG', quality=quality)
        thumb_io.seek(0)
        return thumb_io.getvalue()
    except Exception:
        return None

def get_image_aspect_ratio(image_data):
    try:
        img = Image.open(io.BytesIO(image_data))
        width, height = img.size
        from math import gcd
        g = gcd(width, height)
        return f"{width // g}:{height // g}"
    except Exception:
        return "16:9"

# Standard cinematography formats — raw ratios like 80:43 get displayed as the
# nearest of these. The exact ratio stays in the DB for layout math.
STANDARD_ASPECT_RATIOS = [
    ('9:16', 9 / 16),
    ('2:3', 2 / 3),
    ('3:4', 3 / 4),
    ('4:5', 4 / 5),
    ('1:1', 1.0),
    ('4:3', 4 / 3),
    ('3:2', 3 / 2),
    ('16:9', 16 / 9),
    ('1.85:1', 1.85),
    ('2:1', 2.0),
    ('2.39:1', 2.39),
]

def normalize_ar_label(ar_float):
    from math import log
    if not ar_float or ar_float <= 0:
        return '16:9'
    # Log distance treats "too wide" and "too tall" symmetrically
    return min(STANDARD_ASPECT_RATIOS, key=lambda s: abs(log(ar_float / s[1])))[0]

def ar_float_from_str(ar_str):
    """Parse a stored aspect_ratio string ("80:43", "1.85:1", "2") to a float.
    Single source of truth for this parsing — build_image_dict (display) and
    /api/search's ar filter (V15) must always agree on what bucket an image
    falls in, or search results wouldn't match the labels shown on tiles."""
    ar_str = ar_str or '16:9'
    try:
        w, h = ar_str.split(':', 1) if ':' in ar_str else (ar_str, '1')
        return float(w) / float(h)
    except Exception:
        return 16 / 9

# V15: plain-English ways a cinematographer might type a format into search
AR_QUERY_ALIASES = {
    'scope': '2.39:1', 'anamorphic': '2.39:1', 'cinemascope': '2.39:1',
    'flat': '1.85:1', 'vertical': '9:16', 'portrait': '9:16',
    'square': '1:1', 'widescreen': '16:9',
}

def ar_query_labels(q):
    """Which standard aspect-ratio buckets does a search query point at?
    Pure string logic, no database. Three ways to match:
      - alias words: "scope" / "vertical" / "square" ...
      - a typed ratio or decimal ("2.35", "2.35:1", "9:16", "16x9") snaps to
        the nearest standard bucket — so 2.35 and 2.39 both find Scope.
        Bare integers are excluded here ("9" shouldn't mean "9.0:1 wide").
      - substring on the label itself: "16" hits both 16:9 and 9:16.
    """
    labels = []
    alias = AR_QUERY_ALIASES.get(q)
    if alias:
        labels.append(alias)
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*[:/x]\s*(\d+(?:\.\d+)?)|(\d+\.\d+)', q)
    if m:
        try:
            num = float(m.group(1) or m.group(3))
            den = float(m.group(2)) if m.group(2) else 1.0
            if num > 0 and den > 0:
                labels.append(normalize_ar_label(num / den))
        except (ValueError, ZeroDivisionError):
            # Deliberately the ONE silent handler left after the V44/Day 26
            # audit, for two reasons. It's unreachable: the regex above only
            # ever captures digit strings, so float() can't raise ValueError,
            # and the num/den guard runs BEFORE the division, so
            # ZeroDivisionError can't fire either (verified against "16:0",
            # "0:9", "0.0:0.0"). And this runs on every autocomplete
            # keystroke, so a log line here would be pure noise. Kept purely
            # as a belt-and-braces guard in case the regex is ever loosened.
            pass
    for label, _ in STANDARD_ASPECT_RATIOS:
        if q in label:
            labels.append(label)
    return list(dict.fromkeys(labels))  # dedupe, keep order

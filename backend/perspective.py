"""Frame Atlas — perspective crop geometry (V32).

Split out of app.py in V45 (Day 27). Pure geometry: percentages and PIL images
in, coefficients and a straightened image out. No database, no Drive, no Flask.

Ryan photographs monitors, posters and projected images that sit on an angle.
A rectangle can't extract those, so crop gained a second SHAPE — four
independently-dragged corners, straightened back into a rectangle. It is not a
second crop tool: both shapes converge before the destructive Drive write in
app.py, so the backup-first rule and the post-write refresh are the same lines
for both. Never fork that tail (V27 crashed after overwriting Drive and left
the database holding a pre-crop thumbnail).

The 8 homography coefficients are solved by hand here and again in
frontend/src/perspective.js, cross-checked to 9 decimals. numpy is
deliberately NOT a dependency — it isn't in backend/requirements.txt, and
touching that file adds 3+ minutes to every Railway deploy.
"""

import math

from PIL import Image

# The quad has to cover at least this much of the frame (percent of the whole
# image area). Same spirit as the rectangle path's "w >= 1% and h >= 1%":
# a stray double-click that collapses the quad to a speck should be refused
# with a readable message, not turned into an 8-pixel file in Drive.
PERSPECTIVE_MIN_AREA_PCT = 1.0

# Two corners closer together than this (in percent-of-frame units) are
# treated as the same point — a degenerate "triangle" quad, which has no
# unique perspective solution and makes the 8x8 solve singular.
PERSPECTIVE_MIN_CORNER_GAP_PCT = 0.5


def _quad_signed_area(pts):
    """Shoelace area of a polygon. The sign tells you which way the points
    wind; the magnitude is the area in whatever units the points are in."""
    total = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def parse_perspective_corners(raw):
    """Validate the four corner points the browser sends and hand back
    [(x, y), ...] in percent (0-100), in the order the caller gave them:
    top-left, top-right, bottom-right, bottom-left.

    Raises ValueError with a message written for Ryan, not for a log file.

    Why this is stricter than the rectangle path, which just clamps: clamping
    a rectangle is harmless — it stays a rectangle in the same place. Clamping
    ONE corner of a quadrilateral silently changes the SHAPE of the correction,
    so the file Drive ends up holding would not be the one the preview showed.
    Every check below has to fail HERE, before the destructive write, because a
    crop overwrites the Drive file in place and there is no undo.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError('Perspective correction needs exactly four corner points.')

    pts = []
    for p in raw:
        if isinstance(p, dict):
            cx, cy = p.get('x'), p.get('y')
        elif isinstance(p, (list, tuple)) and len(p) == 2:
            cx, cy = p
        else:
            raise ValueError('Each corner must be a point with numeric x and y percentages.')
        try:
            cx = float(cx)
            cy = float(cy)
        except (TypeError, ValueError):
            raise ValueError('Each corner must be a point with numeric x and y percentages.')
        if not (math.isfinite(cx) and math.isfinite(cy)):
            raise ValueError('Corner points must be real numbers.')
        # Rejected, not clamped — see the docstring.
        if not (0.0 <= cx <= 100.0) or not (0.0 <= cy <= 100.0):
            raise ValueError('Corner points must sit inside the image (0-100%).')
        pts.append((cx, cy))

    # Two corners on top of each other collapse the quad to a triangle, which
    # has no unique perspective solution (the 8x8 system below goes singular).
    for i in range(4):
        for j in range(i + 1, 4):
            if math.dist(pts[i], pts[j]) < PERSPECTIVE_MIN_CORNER_GAP_PCT:
                raise ValueError('Two corner points are on top of each other — drag them apart.')

    # Convexity, checked as "every turn goes the same way". This is the
    # bow-tie guard: drag the top-left handle past the top-right one and the
    # edges cross, one cross product flips sign, and we refuse. It also
    # rejects concave quads, which is correct rather than merely convenient —
    # a rectangle photographed from ANY angle is always convex, so a dented
    # quad is not a perspective view of anything and the transform would fold
    # the picture over itself.
    signs = []
    for i in range(4):
        ax, ay = pts[i]
        bx, by = pts[(i + 1) % 4]
        cx, cy = pts[(i + 2) % 4]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) < 1e-9:
            raise ValueError('Those four corners are in a straight line — they do not enclose an area.')
        signs.append(cross > 0)
    if len(set(signs)) != 1:
        raise ValueError('Those corners cross over each other. Drag them so the outline is a simple four-sided shape.')

    # Winding direction is deliberately NOT pinned. A flipped quad produces a
    # mirrored result, which is exactly what the on-screen preview shows, so
    # it stays the user's call rather than a confusing rejection.

    if abs(_quad_signed_area(pts)) < PERSPECTIVE_MIN_AREA_PCT:
        raise ValueError('That selection is too small — it must cover at least 1% of the image.')

    return pts


def perspective_is_whole_image(pts, tolerance=0.5):
    """True when the quad is (within a hair of) the untouched image corners —
    a no-op. Mirrors the rectangle path's "covers the whole image" refusal so
    a stray Apply can't burn a destructive Drive rewrite doing nothing."""
    want = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    return all(abs(p[0] - q[0]) <= tolerance and abs(p[1] - q[1]) <= tolerance
               for p, q in zip(pts, want))


def solve_linear_system(matrix, rhs):
    """Gaussian elimination with partial pivoting. Returns the solution vector.

    Deliberately hand-written: numpy would do this in one line, but numpy is
    NOT in requirements.txt, and adding it costs every Railway deploy an extra
    few minutes forever in exchange for one 8x8 solve that fits in 20 lines.
    """
    n = len(rhs)
    # Work on copies — callers reuse their coefficient rows.
    a = [list(row) + [rhs[i]] for i, row in enumerate(matrix)]

    for col in range(n):
        # Partial pivoting: swap the largest remaining magnitude into place.
        # Without it a perfectly legitimate quad whose diagonal entry happens
        # to be zero — an exactly axis-aligned corner, which is the COMMON
        # case here since the handles are seeded from a rectangle — divides
        # by zero instead of solving.
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError('Those four corners do not describe a valid perspective.')
        a[col], a[pivot] = a[pivot], a[col]

        inv = 1.0 / a[col][col]
        for r in range(col + 1, n):
            factor = a[r][col] * inv
            if factor == 0.0:
                continue
            for k in range(col, n + 1):
                a[r][k] -= factor * a[col][k]

    out = [0.0] * n
    for row in range(n - 1, -1, -1):
        acc = a[row][n] - sum(a[row][k] * out[k] for k in range(row + 1, n))
        out[row] = acc / a[row][row]
    return out


def solve_perspective_coeffs(from_pts, to_pts):
    """The eight numbers Pillow's Image.PERSPECTIVE wants.

    A perspective map is  X = (a*x + b*y + c) / (g*x + h*y + 1)
                          Y = (d*x + e*y + f) / (g*x + h*y + 1)
    Multiplying out gives two linear equations per corner; four corners make
    an 8x8 system in (a, b, c, d, e, f, g, h).

    IMPORTANT DIRECTION NOTE: Pillow's transform walks the OUTPUT pixels and
    asks where each one came FROM, so the coefficients must map
    output -> input. Call this with from_pts = the destination rectangle and
    to_pts = the quad in the source photo. Getting this backwards warps the
    picture the wrong way instead of raising, so it is worth the paragraph.
    """
    matrix = []
    rhs = []
    for (x, y), (tx, ty) in zip(from_pts, to_pts):
        matrix.append([x, y, 1, 0, 0, 0, -tx * x, -tx * y])
        rhs.append(tx)
        matrix.append([0, 0, 0, x, y, 1, -ty * x, -ty * y])
        rhs.append(ty)
    return solve_linear_system(matrix, rhs)


def perspective_output_size(src_px):
    """Pixel size of the de-skewed rectangle, derived from the quad's own edges.

    Each side of the output is the AVERAGE of the two OPPOSITE edges of the
    quad. Picking one edge instead would bake the perspective back into the
    result: on a monitor shot from the left, the near (right) edge is much
    longer than the far one, so sizing off the near edge stretches the whole
    image and sizing off the far edge squashes it. The average lands between
    the two and keeps the subject's proportions roughly true, which is what
    "de-skew" should mean. It also can't invent detail — the output is never
    larger than the longest edge the photo actually contains.
    """
    tl, tr, br, bl = src_px
    top = math.dist(tl, tr)
    bottom = math.dist(bl, br)
    left = math.dist(tl, bl)
    right = math.dist(tr, br)
    return int(round((top + bottom) / 2.0)), int(round((left + right) / 2.0))


def perspective_correct(img, corners_pct):
    """Straighten the quadrilateral `corners_pct` (percentages, TL/TR/BR/BL)
    out of `img` into a plain rectangle. Returns a new PIL image."""
    w_px, h_px = img.width, img.height
    # Percent -> pixels happens HERE and only here, which is what lets the same
    # four numbers mean the same selection whether the browser was looking at a
    # 600px preview or the 6000px original.
    src = [(x / 100.0 * w_px, y / 100.0 * h_px) for x, y in corners_pct]

    out_w, out_h = perspective_output_size(src)
    # Same floor the rectangle path uses — below this there is no usable
    # picture left and the downstream thumbnail/palette work gets silly.
    if out_w < 8 or out_h < 8:
        raise ValueError('Perspective selection is too small to produce a usable image')

    dst = [(0, 0), (out_w, 0), (out_w, out_h), (0, out_h)]
    coeffs = solve_perspective_coeffs(dst, src)
    return img.transform((out_w, out_h), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC)

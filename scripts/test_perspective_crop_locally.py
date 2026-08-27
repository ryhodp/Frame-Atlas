"""
Frame Atlas — local test for V32 four-point perspective correction.

Crop mode gained a second shape: instead of only an axis-aligned rectangle,
four corners can be dragged independently onto a tilted screen/poster and the
result is de-skewed into a straight rectangle.

What this file is actually guarding, in order of how expensive the bug would be:

  1. THE DESTRUCTIVE WRITE. A crop overwrites the Drive file in place. So a
     bad quadrilateral must be refused BEFORE the original is touched, and a
     failed backup must abort before the overwrite — exactly the rule the
     rectangle path already obeys. Perspective gets no exemption.
  2. THE V27 DISASTER. That worker overwrote Drive and then crashed writing to
     columns that never existed, leaving Drive cropped and the database holding
     a stale pre-crop thumbnail. So after a perspective transform we assert the
     row really was refreshed — thumbnail, aspect ratio, md5, phash, palette —
     the same set a rectangle crop refreshes.
  3. THE MATHS. The 8-coefficient solve is checked two ways: algebraically (the
     coefficients map the output corners exactly onto the source corners) and
     by round-trip pixels (a known pattern is warped INTO a tilted quad, then
     recovered through the real endpoint and checked quadrant by quadrant).
  4. THE RECTANGLE PATH IS UNTOUCHED. A request with no `corners` field must
     behave exactly as it did before V32.

Drive is faked, so nothing leaves the machine.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_perspective_crop_locally.py
"""

import importlib.util
import io
import math
import os
import sqlite3
import sys
import tempfile
import time

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))


# ── Fake Drive ────────────────────────────────────────────────────────────────
# Same shape as test_crop_queue_locally.py's fakes, with one addition: update()
# keeps the BYTES it was handed, so the test can decode what would really have
# landed in Drive and check the actual pixels.

class FakeRequest:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeFilesResource:
    def __init__(self, drive):
        self.drive = drive

    def get(self, fileId=None, fields=None, **kw):
        return FakeRequest(lambda: {"id": fileId, "mimeType": "image/jpeg"})

    def get_media(self, fileId=None):
        req = FakeRequest(lambda: None)
        req.data = self.drive.files_bytes.get(fileId, self.drive.jpeg_bytes)
        return req

    def update(self, fileId=None, media_body=None, fields=None, **kw):
        self.drive.update_calls.append(fileId)
        if media_body is not None:
            self.drive.uploaded[fileId] = media_body.getbytes(0, media_body.size())
        if self.drive.next_update_error is not None:
            err = self.drive.next_update_error
            self.drive.next_update_error = None

            def boom():
                raise err
            return FakeRequest(boom)
        return FakeRequest(lambda: {"id": fileId, "md5Checksum": "md5-after-crop"})

    def list(self, q=None, fields=None, **kw):
        return FakeRequest(lambda: {"files": [{"id": "REMOVED_FOLDER_ID"}]})

    def create(self, body=None, media_body=None, fields=None, **kw):
        raise AssertionError("service account must never files().create() — no storage quota")


class FakeDrive:
    def __init__(self, jpeg_bytes):
        self.jpeg_bytes = jpeg_bytes
        self.files_bytes = {}
        self.uploaded = {}
        self.update_calls = []
        self.next_update_error = None

    def files(self):
        return FakeFilesResource(self)


class FakeUserFilesResource:
    def __init__(self, drive):
        self.drive = drive

    def create(self, body=None, media_body=None, fields=None, **kw):
        self.drive.create_calls.append((body or {}).get("name"))
        if self.drive.next_create_error is not None:
            err = self.drive.next_create_error
            self.drive.next_create_error = None

            def boom():
                raise err
            return FakeRequest(boom)
        return FakeRequest(lambda: {"id": "BACKUP_FILE_ID"})


class FakeUserDrive:
    def __init__(self):
        self.create_calls = []
        self.next_create_error = None

    def files(self):
        return FakeUserFilesResource(self)


class FakeDownloader:
    def __init__(self, fh, req):
        fh.write(req.data)

    def next_chunk(self):
        return (None, True)


# ── Fixtures ──────────────────────────────────────────────────────────────────

# Four strongly separated colours, one per quadrant of the test pattern.
QUAD_COLORS = [
    (220, 40, 40),    # top-left     red
    (40, 200, 60),    # top-right    green
    (50, 70, 220),    # bottom-right blue   (note: BR, matching corner order)
    (230, 210, 40),   # bottom-left  yellow
]

# The tilted "screen" inside the source photo, in source pixels, ordered
# top-left, top-right, bottom-right, bottom-left — the order the API expects.
TEST_QUAD = [(80, 60), (560, 110), (520, 430), (120, 380)]
SRC_W, SRC_H = 640, 480

# 0.6% x 0.6% of the frame — corners far enough apart to clear the "same point"
# check, but an area of 0.36%, below the 1% floor both shapes share.
SPECK_QUAD = [{"x": 10, "y": 10}, {"x": 10.6, "y": 10},
              {"x": 10.6, "y": 10.6}, {"x": 10, "y": 10.6}]


def make_pattern(mod, w=400, h=300):
    """A four-quadrant pattern: TL red, TR green, BR blue, BL yellow."""
    img = mod.Image.new("RGB", (w, h), (0, 0, 0))
    d = mod.ImageDraw.Draw(img)
    d.rectangle([0, 0, w // 2, h // 2], fill=QUAD_COLORS[0])
    d.rectangle([w // 2, 0, w, h // 2], fill=QUAD_COLORS[1])
    d.rectangle([w // 2, h // 2, w, h], fill=QUAD_COLORS[2])
    d.rectangle([0, h // 2, w // 2, h], fill=QUAD_COLORS[3])
    return img


def make_tilted_source(mod, quad=TEST_QUAD):
    """Build the photo a de-skew is supposed to recover from.

    The pattern is warped INTO `quad` on a grey background, which is the exact
    inverse of what the feature does — so if the coefficient solve is right,
    running perspective_correct on this image with the same quad hands the
    pattern back.
    """
    pattern = make_pattern(mod)
    pw, ph = pattern.size
    pattern_corners = [(0, 0), (pw, 0), (pw, ph), (0, ph)]

    # Output is source space, input is pattern space -> map source -> pattern.
    coeffs = mod.solve_perspective_coeffs(quad, pattern_corners)
    warped = pattern.transform((SRC_W, SRC_H), mod.Image.PERSPECTIVE, coeffs,
                               resample=mod.Image.BICUBIC)

    mask = mod.Image.new("L", (SRC_W, SRC_H), 0)
    mod.ImageDraw.Draw(mask).polygon(quad, fill=255)

    src = mod.Image.new("RGB", (SRC_W, SRC_H), (90, 90, 95))
    src.paste(warped, (0, 0), mask)
    return src


def to_jpeg(img, quality=95):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, subsampling=0)
    return buf.getvalue()


def make_flat_jpeg(mod, color=(40, 90, 200), size=(400, 240)):
    return to_jpeg(mod.Image.new("RGB", size, color), quality=90)


def quad_to_percent(quad, w=SRC_W, h=SRC_H):
    return [{"x": x / w * 100.0, "y": y / h * 100.0} for x, y in quad]


def nearest_color_index(px):
    """Which of the four quadrant colours is this pixel closest to?
    Comparing by nearest colour rather than an exact value keeps the test
    honest about JPEG and bicubic resampling without being loose about which
    quadrant landed where."""
    return min(range(4), key=lambda i: sum((px[c] - QUAD_COLORS[i][c]) ** 2 for c in range(3)))


def wait_for_crop_to_finish(client, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        progress = client.get("/api/crop-progress").get_json()
        if progress["in_progress"] == 0 and progress["total"] > 0:
            return progress
        time.sleep(0.05)
    return None


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_perspective_test_")
    db_path = os.path.join(workdir, "library.db")

    os.environ["FA_DB_PATH"] = db_path

    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy-client-id")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")

    spec = importlib.util.spec_from_file_location("fa_perspective_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_perspective_app"] = mod
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True

    # ImageDraw isn't imported by app.py; the fixtures need it.
    from PIL import ImageDraw
    mod.ImageDraw = ImageDraw

    drive = FakeDrive(make_flat_jpeg(mod))
    user_drive = FakeUserDrive()
    mod.drive.get_drive_service = lambda: drive
    mod.drive.get_user_drive_service = lambda uid: user_drive
    mod.MediaIoBaseDownload = FakeDownloader
    # Day 29: download_drive_file() moved to drive.py, so the crop worker's
    # download now resolves MediaIoBaseDownload in drive.py's namespace.
    mod.drive.MediaIoBaseDownload = FakeDownloader
    mod.tagging.trigger_tagging = lambda *a, **k: None
    print("App imported OK (Drive faked).")

    failures = []

    def check(label, cond, detail=""):
        if cond:
            print(f"{label} — OK")
        else:
            print(f"{label} — FAIL  {detail}")
            failures.append(label)

    # ══ PART 1: the coefficient solve ═════════════════════════════════════════
    print("\n── Coefficient solve ──")

    out_w, out_h = mod.perspective_output_size(TEST_QUAD)
    exp_w = round((math.dist(TEST_QUAD[0], TEST_QUAD[1]) + math.dist(TEST_QUAD[3], TEST_QUAD[2])) / 2)
    exp_h = round((math.dist(TEST_QUAD[0], TEST_QUAD[3]) + math.dist(TEST_QUAD[1], TEST_QUAD[2])) / 2)
    check("Output size is the average of opposite edges",
          (out_w, out_h) == (exp_w, exp_h), f"got {(out_w, out_h)}, expected {(exp_w, exp_h)}")

    # Algebraic check: the coefficients Pillow gets must send each corner of
    # the OUTPUT rectangle exactly onto the matching corner of the source quad.
    dst_rect = [(0, 0), (out_w, 0), (out_w, out_h), (0, out_h)]
    co = mod.solve_perspective_coeffs(dst_rect, TEST_QUAD)
    a, b, c, d, e, f, g, h = co
    worst = 0.0
    for (X, Y), (sx, sy) in zip(dst_rect, TEST_QUAD):
        w = g * X + h * Y + 1
        worst = max(worst, abs((a * X + b * Y + c) / w - sx), abs((d * X + e * Y + f) / w - sy))
    check("Coefficients map every output corner onto its source corner",
          worst < 1e-6, f"worst corner error {worst}")

    # A point that is NOT a corner has to be right too, otherwise the solve
    # could be fitting the corners while warping everything between them.
    # The quad's diagonals cross at one point; so do the output rectangle's,
    # and a projective map preserves that.
    def line_intersect(p1, p2, p3, p4):
        x1, y1 = p1; x2, y2 = p2; x3, y3 = p3; x4, y4 = p4
        den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        t1 = x1 * y2 - y1 * x2
        t2 = x3 * y4 - y3 * x4
        return ((t1 * (x3 - x4) - (x1 - x2) * t2) / den,
                (t1 * (y3 - y4) - (y1 - y2) * t2) / den)

    cx, cy = line_intersect(*dst_rect[0::2], *dst_rect[1::2])   # rect diagonals
    qx, qy = line_intersect(*TEST_QUAD[0::2], *TEST_QUAD[1::2])  # quad diagonals
    w = g * cx + h * cy + 1
    mapped = ((a * cx + b * cy + c) / w, (d * cx + e * cy + f) / w)
    check("Coefficients map the interior (diagonal crossing) correctly too",
          math.dist(mapped, (qx, qy)) < 1e-6,
          f"mapped {mapped}, expected {(qx, qy)}")

    # Round-trip in pure Pillow, before any Flask/Drive machinery is involved.
    source_img = make_tilted_source(mod)
    recovered = mod.perspective_correct(source_img, [(x / SRC_W * 100, y / SRC_H * 100) for x, y in TEST_QUAD])
    check("De-skewed output is the averaged-edge size",
          recovered.size == (out_w, out_h), recovered.size)

    def quadrant_probe(img):
        """Sample well inside each quadrant of a de-skewed result."""
        W, H = img.size
        pts = [(W // 4, H // 4), (3 * W // 4, H // 4), (3 * W // 4, 3 * H // 4), (W // 4, 3 * H // 4)]
        return [nearest_color_index(img.convert("RGB").getpixel(p)) for p in pts]

    check("Warped pattern is recovered quadrant-for-quadrant",
          quadrant_probe(recovered) == [0, 1, 2, 3], quadrant_probe(recovered))

    # ══ PART 2: validation — everything must fail BEFORE the destructive write ═
    print("\n── Quadrilateral validation ──")

    good = quad_to_percent(TEST_QUAD)

    def rejects(label, corners, expect_substring=None):
        try:
            mod.parse_perspective_corners(corners)
        except ValueError as err:
            ok = expect_substring is None or expect_substring.lower() in str(err).lower()
            check(label, ok, f"message was: {err}")
            return
        check(label, False, "accepted a quad it should have refused")

    try:
        parsed = mod.parse_perspective_corners(good)
        check("A legitimate tilted quad is accepted", len(parsed) == 4, parsed)
    except ValueError as err:
        check("A legitimate tilted quad is accepted", False, str(err))

    # Bow-tie: swap the two top corners so the edges cross.
    bowtie = [good[1], good[0], good[2], good[3]]
    rejects("Bow-tie (crossed) quad is rejected", bowtie, "cross")

    # Concave: pull the bottom-right corner deep inside the shape. A rectangle
    # photographed from any angle is always convex, so this is not a real
    # perspective and the transform would fold the picture over itself.
    concave = [
        {"x": 10, "y": 10}, {"x": 90, "y": 10},
        {"x": 30, "y": 30}, {"x": 10, "y": 90},
    ]
    rejects("Concave (dented) quad is rejected", concave)

    rejects("Corner past the right edge is rejected",
            [{"x": 10, "y": 10}, {"x": 105, "y": 10}, {"x": 90, "y": 90}, {"x": 10, "y": 90}], "0-100")
    rejects("Negative corner is rejected",
            [{"x": -1, "y": 10}, {"x": 90, "y": 10}, {"x": 90, "y": 90}, {"x": 10, "y": 90}], "0-100")
    rejects("Two corners in the same place is rejected",
            [{"x": 10, "y": 10}, {"x": 10, "y": 10}, {"x": 90, "y": 90}, {"x": 10, "y": 90}], "top of each other")
    rejects("Collinear corners are rejected",
            [{"x": 10, "y": 10}, {"x": 40, "y": 10}, {"x": 70, "y": 10}, {"x": 90, "y": 10}])
    # Deliberately just BELOW the floor, not miles below: the corners are 0.6%
    # apart, so they clear the "same point" check and are refused on area
    # alone. The floor matches the rectangle path's own minimum — a 1% x 1%
    # selection is the smallest either shape accepts.
    rejects("A speck of a quad is rejected", SPECK_QUAD, "too small")
    rejects("Three corners is rejected", good[:3], "exactly four")
    rejects("Five corners is rejected", good + [good[0]], "exactly four")
    rejects("A non-list is rejected", {"x": 1}, "exactly four")
    rejects("Non-numeric corners are rejected",
            [{"x": "left", "y": 10}, {"x": 90, "y": 10}, {"x": 90, "y": 90}, {"x": 10, "y": 90}], "numeric")
    rejects("Infinity is rejected",
            [{"x": float("inf"), "y": 10}, {"x": 90, "y": 10}, {"x": 90, "y": 90}, {"x": 10, "y": 90}])

    check("Untouched image corners count as a no-op",
          mod.perspective_is_whole_image([(0, 0), (100, 0), (100, 100), (0, 100)]))
    check("A real tilted quad is not treated as a no-op",
          not mod.perspective_is_whole_image(mod.parse_perspective_corners(good)))

    # Mirrored winding is deliberately allowed — it produces a mirrored result,
    # which is what the on-screen preview shows, so it stays the user's call.
    mirrored = [good[1], good[0], good[3], good[2]]
    try:
        mod.parse_perspective_corners(mirrored)
        check("Mirrored (reverse-wound) quad is allowed, not refused", True)
    except ValueError as err:
        check("Mirrored (reverse-wound) quad is allowed, not refused", False, str(err))

    # ══ PART 3: the endpoint, end to end ══════════════════════════════════════
    print("\n── Endpoint + background worker ──")

    admin = mod.app.test_client()
    admin.post("/api/setup", json={"email": "ryan@test.com", "password": "adminpass123"})

    source_jpeg = to_jpeg(source_img)
    drive.files_bytes["drive-file-1"] = source_jpeg

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status) "
        "VALUES (1, 1, 'drive-file-1', 'tilted-monitor.jpg', ?, '4:3', 'done')",
        (source_jpeg,),
    )
    conn.commit()
    old_thumbnail = conn.execute("SELECT thumbnail_blob FROM images WHERE id = 1").fetchone()[0]
    conn.close()

    r = admin.post("/api/images/1/crop", json={"corners": good})
    body = r.get_json()
    check("Perspective crop is queued immediately",
          r.status_code == 200 and body.get("queued") is True, body)

    progress = wait_for_crop_to_finish(admin)
    check("Perspective job's progress counter reaches 0", progress is not None,
          "timed out" if progress is None else progress)
    if progress is not None:
        check("Perspective job reports no failure", not progress.get("failed"), progress.get("failed"))

    # The bytes that would really have landed in Drive.
    uploaded = drive.uploaded.get("drive-file-1")
    check("Something was actually uploaded to Drive", bool(uploaded))
    if uploaded:
        result = mod.Image.open(io.BytesIO(uploaded))
        check("Uploaded image is the de-skewed rectangle size",
              result.size == (out_w, out_h), f"{result.size} vs {(out_w, out_h)}")
        check("Uploaded image really is de-skewed (pattern recovered)",
              quadrant_probe(result) == [0, 1, 2, 3], quadrant_probe(result))

    # V27 regression: the row must reflect the new pixels, not the old ones.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT thumbnail_blob, aspect_ratio, md5_checksum, phash FROM images WHERE id = 1").fetchone()
    palette_n = conn.execute("SELECT COUNT(*) n FROM colors WHERE image_id = 1").fetchone()["n"]
    conn.close()

    check("Thumbnail was refreshed (not left stale — the V27 bug)",
          row["thumbnail_blob"] != old_thumbnail)
    check("Aspect ratio was recalculated from the straightened image",
          row["aspect_ratio"] == mod.get_image_aspect_ratio(uploaded) if uploaded else False,
          row["aspect_ratio"])
    check("md5 checksum was refreshed", row["md5_checksum"] == "md5-after-crop", row["md5_checksum"])
    check("Fingerprint was recomputed", bool(row["phash"]), row["phash"])
    check("Colour palette was recomputed", palette_n > 0, palette_n)

    check("Exactly one backup was written before the destructive overwrite",
          len(user_drive.create_calls) == 1, user_drive.create_calls)
    check("Backup name marks it as pre-crop",
          bool(user_drive.create_calls) and "pre-crop" in user_drive.create_calls[0],
          user_drive.create_calls)
    check("The original Drive file was overwritten in place (update, not create)",
          drive.update_calls == ["drive-file-1"], drive.update_calls)

    # ── A bad quad must be refused before anything is downloaded or written ──
    admin.post("/api/crop-progress/reset")
    drive.update_calls.clear()
    user_drive.create_calls.clear()

    for label, corners in [
        ("bow-tie", bowtie),
        ("out-of-range", [{"x": 10, "y": 10}, {"x": 140, "y": 10}, {"x": 90, "y": 90}, {"x": 10, "y": 90}]),
        ("degenerate speck", SPECK_QUAD),
        ("whole image", [{"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 100, "y": 100}, {"x": 0, "y": 100}]),
    ]:
        r = admin.post("/api/images/1/crop", json={"corners": corners})
        check(f"A {label} quad is refused with a 400", r.status_code == 400, (r.status_code, r.get_json()))

    prog = admin.get("/api/crop-progress").get_json()
    check("No rejected quad was ever queued", prog["total"] == 0, prog)
    check("No rejected quad touched Drive",
          drive.update_calls == [] and user_drive.create_calls == [],
          (drive.update_calls, user_drive.create_calls))

    # ── Failed backup must abort before the Drive overwrite ─────────────────
    admin.post("/api/crop-progress/reset")
    drive.update_calls.clear()
    user_drive.create_calls.clear()
    drive.files_bytes["drive-file-2"] = source_jpeg

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status) "
        "VALUES (2, 1, 'drive-file-2', 'tilted-2.jpg', ?, '4:3', 'done')",
        (source_jpeg,),
    )
    conn.commit()
    before_thumb = conn.execute("SELECT thumbnail_blob FROM images WHERE id = 2").fetchone()[0]
    conn.close()

    user_drive.next_create_error = RuntimeError("backup upload exploded")
    admin.post("/api/images/2/crop", json={"corners": good})
    progress = wait_for_crop_to_finish(admin)
    check("Failed backup fails the perspective job",
          bool(progress and progress["failed"]), progress)
    check("Failed backup prevents the perspective Drive overwrite",
          "drive-file-2" not in drive.update_calls, drive.update_calls)

    conn = sqlite3.connect(db_path)
    after_thumb = conn.execute("SELECT thumbnail_blob FROM images WHERE id = 2").fetchone()[0]
    conn.close()
    check("Failed backup leaves the database row untouched", after_thumb == before_thumb)

    # ── No connected Google account: same abort ──────────────────────────────
    admin.post("/api/crop-progress/reset")
    drive.update_calls.clear()
    saved_get_user_drive = mod.drive.get_user_drive_service
    mod.drive.get_user_drive_service = lambda uid: None

    drive.files_bytes["drive-file-3"] = source_jpeg
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status) "
        "VALUES (3, 1, 'drive-file-3', 'tilted-3.jpg', ?, '4:3', 'done')",
        (source_jpeg,),
    )
    conn.commit()
    conn.close()

    admin.post("/api/images/3/crop", json={"corners": good})
    progress = wait_for_crop_to_finish(admin)
    check("No connected Google account fails the perspective job",
          bool(progress and progress["failed"]), progress)
    check("No connected Google account prevents the Drive overwrite",
          "drive-file-3" not in drive.update_calls, drive.update_calls)
    mod.drive.get_user_drive_service = saved_get_user_drive

    # ══ PART 4: the rectangle path is untouched ═══════════════════════════════
    print("\n── Rectangle path (must behave exactly as before V32) ──")

    admin.post("/api/crop-progress/reset")
    drive.update_calls.clear()
    user_drive.create_calls.clear()

    flat = make_flat_jpeg(mod, color=(200, 60, 60), size=(400, 240))
    drive.files_bytes["drive-file-9"] = flat
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status) "
        "VALUES (9, 1, 'drive-file-9', 'plain.jpg', ?, '5:3', 'done')",
        (flat,),
    )
    conn.commit()
    old_thumb_9 = conn.execute("SELECT thumbnail_blob FROM images WHERE id = 9").fetchone()[0]
    conn.close()

    r = admin.post("/api/images/9/crop", json={"box": {"x": 10, "y": 10, "w": 50, "h": 50}})
    check("Rectangle crop still queues", r.get_json().get("queued") is True, r.get_json())
    progress = wait_for_crop_to_finish(admin)
    check("Rectangle crop still completes", bool(progress and not progress["failed"]), progress)

    rect_bytes = drive.uploaded.get("drive-file-9")
    if rect_bytes:
        rect_img = mod.Image.open(io.BytesIO(rect_bytes))
        check("Rectangle crop produced the same pixel box as before V32",
              rect_img.size == (200, 120), rect_img.size)
    else:
        check("Rectangle crop produced the same pixel box as before V32", False, "nothing uploaded")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row9 = conn.execute("SELECT thumbnail_blob, md5_checksum, phash FROM images WHERE id = 9").fetchone()
    conn.close()
    check("Rectangle crop still refreshes the thumbnail", row9["thumbnail_blob"] != old_thumb_9)
    check("Rectangle crop still refreshes the checksum", row9["md5_checksum"] == "md5-after-crop")
    check("Rectangle crop still backs up first", len(user_drive.create_calls) == 1, user_drive.create_calls)

    # The old rejections still read the same way.
    r = admin.post("/api/images/9/crop", json={"box": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}})
    check("Rectangle 'too small' rejection unchanged",
          r.status_code == 400 and "at least 1%" in r.get_json().get("error", ""), r.get_json())
    r = admin.post("/api/images/9/crop", json={"box": {"x": 0, "y": 0, "w": 100, "h": 100}})
    check("Rectangle 'whole image' rejection unchanged",
          r.status_code == 400 and "whole image" in r.get_json().get("error", ""), r.get_json())
    r = admin.post("/api/images/9/crop", json={})
    check("Missing box still rejected the same way",
          r.status_code == 400 and "numeric x, y, w, h" in r.get_json().get("error", ""), r.get_json())

    # A queued job dict from before V32 has no 'corners' key at all — the
    # worker must still run it down the rectangle path rather than crashing.
    admin.post("/api/crop-progress/reset")
    drive.update_calls.clear()
    user_drive.create_calls.clear()
    drive.files_bytes["drive-file-10"] = flat
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status) "
        "VALUES (10, 1, 'drive-file-10', 'legacy.jpg', ?, '5:3', 'done')",
        (flat,),
    )
    conn.commit()
    conn.close()

    with mod._crop_lock:
        mod._crop_progress['total'] += 1
        mod._crop_progress['in_progress'] += 1
    mod._crop_queue.put({
        'id': 9999,
        'image_id': 10,
        'user_id': 1,
        'box': {'x': 0, 'y': 0, 'w': 25, 'h': 25},
        'filename': 'legacy.jpg',
        # deliberately NO 'corners' key
    })
    progress = wait_for_crop_to_finish(admin)
    check("A pre-V32 job dict (no 'corners' key) still processes",
          bool(progress and not progress["failed"]), progress)
    legacy_bytes = drive.uploaded.get("drive-file-10")
    check("Pre-V32 job produced the rectangle crop it asked for",
          bool(legacy_bytes) and mod.Image.open(io.BytesIO(legacy_bytes)).size == (100, 60),
          mod.Image.open(io.BytesIO(legacy_bytes)).size if legacy_bytes else None)

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED ❌")
        for fl in failures:
            print(f"   - {fl}")
    else:
        print("ALL PERSPECTIVE CROP TESTS PASSED ✅")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

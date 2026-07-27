"""
Frame Atlas — regression test for the color-overlap check on duplicate
detection (the V27 fix for false-positive "duplicates" that just share a
brightness layout, not actual content).

Background: the duplicate checker's perceptual hash (phash) only reads
brightness LAYOUT — it shrinks a photo to a 9x8 grid and records whether each
pixel is brighter than its right-hand neighbor. Two completely different
photos that happen to share the same rough "dark frame, bright patch in the
middle" shape hash almost identically and used to get flagged as duplicates
of each other. The fix requires the actual color palettes to also overlap
(palettes_overlap() in backend/app.py) before two phash-matched images count
as a real duplicate.

This test builds two images with an IDENTICAL brightness split (so the phash
alone would call them duplicates, reproducing the original bug) but
different colors, and asserts they are no longer flagged — while a
same-color near-duplicate (simulating a resize/recompress) still is.

Drive is faked, so nothing leaves the machine.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_duplicate_color_check_locally.py
"""

import importlib.util
import io
import os
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")


# ── fake Drive (just enough for files().create() and a folder listing) ─────
class FakeRequest:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeFiles:
    def __init__(self, drive):
        self.drive = drive

    def create(self, body=None, media_body=None, fields=None):
        def run():
            self.drive.created.append(body)
            fid = f"drive-file-{len(self.drive.created)}"
            return {"id": fid, "md5Checksum": f"md5-{len(self.drive.created)}"}
        return FakeRequest(run)

    def list(self, q=None, fields=None, **kw):
        return FakeRequest(lambda: {"files": []})


class FakeDrive:
    def __init__(self):
        self.created = []

    def files(self):
        return FakeFiles(self)


def shape_bytes(mod, left_rgb, right_rgb=(10, 10, 10), size=(240, 160)):
    """A JPEG with a hard brightness split down the middle: left_rgb fills
    the left half, right_rgb the right half.

    The difference-hash only compares brightness between neighboring pixels,
    so any two images sharing this exact light/dark layout hash almost
    identically no matter what color fills each half — this reproduces the
    shape of the original false-positive bug."""
    img = mod.Image.new("RGB", size, right_rgb)
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, size[0] // 2, size[1]], fill=left_rgb)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_dupcolor_test_")
    db_path = os.path.join(workdir, "library.db")

    src = open(os.path.join(REPO, "backend", "app.py")).read()
    patched = src.replace("DB_PATH = '/app/data/library.db'", f"DB_PATH = {db_path!r}")
    assert patched != src, "Could not find DB_PATH line to patch"
    open(os.path.join(workdir, "app.py"), "w").write(patched)

    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy-client-id")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")

    spec = importlib.util.spec_from_file_location("fa_dupcolor_app", os.path.join(workdir, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_dupcolor_app"] = mod
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True

    drive = FakeDrive()
    mod.get_user_drive_service = lambda uid: drive
    mod.get_drive_service = lambda: drive
    mod.get_root_folder_id = lambda uid: "folder-root"
    mod.trigger_tagging = lambda *a, **k: None      # no Gemini calls in tests
    print("App imported OK (Drive faked).")

    failures = []

    def check(label, cond, detail=""):
        if cond:
            print(f"{label} — OK")
        else:
            print(f"{label} — FAIL  {detail}")
            failures.append(label)

    admin = mod.app.test_client()
    admin.post("/api/setup", json={"email": "ryan@test.com", "password": "adminpass123"})

    # ── build the three test images ────────────────────────────────────────
    green_v1 = shape_bytes(mod, left_rgb=(0, 200, 0))     # green highlight, dark surround
    green_v2 = shape_bytes(mod, left_rgb=(10, 190, 10))   # same shape, slightly different green (simulated re-save)
    orange_v1 = shape_bytes(mod, left_rgb=(230, 140, 20)) # SAME shape, unrelated color

    # ── 0. sanity check: prove this scenario reproduces the original bug's
    #    precondition — same brightness shape, so phash alone would call
    #    green and orange duplicates ────────────────────────────────────────
    ph_green = mod.compute_phash(green_v1)
    ph_orange = mod.compute_phash(orange_v1)
    ph_green2 = mod.compute_phash(green_v2)
    dist_green_orange = mod.phash_distance(ph_green, ph_orange)
    dist_green_green = mod.phash_distance(ph_green, ph_green2)
    check(
        "Precondition: green and orange share the same brightness shape (phash would call them dupes)",
        dist_green_orange <= mod.PHASH_NEAR_DUP_THRESHOLD,
        f"distance={dist_green_orange}, threshold={mod.PHASH_NEAR_DUP_THRESHOLD}",
    )
    check(
        "Precondition: the two green shades also phash-match (simulated resize/recompress)",
        dist_green_green <= mod.PHASH_NEAR_DUP_THRESHOLD,
        f"distance={dist_green_green}",
    )

    # ── 1. unit-level: palettes_overlap() itself ───────────────────────────
    green_palette = mod.extract_palette(green_v1)
    green2_palette = mod.extract_palette(green_v2)
    orange_palette = mod.extract_palette(orange_v1)

    check(
        "palettes_overlap() rejects same-shape/different-color palettes",
        not mod.palettes_overlap(green_palette, orange_palette),
    )
    check(
        "palettes_overlap() accepts same-shape/same-color palettes",
        mod.palettes_overlap(green_palette, green2_palette),
    )
    check(
        "palettes_overlap() falls back to True when one side has no chromatic signal",
        mod.palettes_overlap([("#0a0a0a", 1.0)], green_palette),
    )

    # ── 2. end-to-end: /api/upload should not flag the color-different image
    #    as a duplicate, but should still catch the same-color near-dupe ────
    r = admin.post("/api/upload", data={"files": (io.BytesIO(green_v1), "green.jpg")},
                    content_type="multipart/form-data")
    results = (r.get_json() or {}).get("results", [])
    check("First (green) upload succeeds", results and results[0]["status"] == "uploaded", r.get_json())

    r = admin.post("/api/upload", data={"files": (io.BytesIO(orange_v1), "orange.jpg")},
                    content_type="multipart/form-data")
    results = (r.get_json() or {}).get("results", [])
    check(
        "Same-shape, different-color upload is NOT flagged as a duplicate (the false-positive bug)",
        results and results[0]["status"] == "uploaded",
        r.get_json(),
    )

    r = admin.post("/api/upload", data={"files": (io.BytesIO(green_v2), "green-again.jpg")},
                    content_type="multipart/form-data")
    results = (r.get_json() or {}).get("results", [])
    check(
        "Same-shape, same-color upload IS still flagged as a duplicate",
        results and results[0]["status"] == "duplicate",
        r.get_json(),
    )

    # ── 3. end-to-end: the admin Duplicate Review scan groups the same way ──
    # The live upload check (section 2) never lets a duplicate reach the
    # database at all, so there's nothing left there for the review screen
    # to find. That screen exists for photos that slipped in a different
    # way — e.g. synced from Drive twice — so seed rows directly, the way a
    # sync would, with no phash/palette yet (duplicates_scan() backfills
    # both before comparing). Fresh colors (blue/purple, not the green/orange
    # already uploaded in section 2) keep this section independent — reusing
    # orange_v1 here would create a second, genuine copy of it and correctly
    # get grouped with the first, muddying what this section is checking.
    def insert_synced_image(filename, image_bytes):
        thumbnail = mod.generate_thumbnail(image_bytes)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, "
            "aspect_ratio, tagging_status) VALUES (1, ?, ?, ?, 1.5, 'pending')",
            (f"synced-{filename}", filename, thumbnail),
        )
        conn.commit()
        conn.close()

    blue_v1 = shape_bytes(mod, left_rgb=(20, 60, 220))    # same shape again, blue this time
    blue_v2 = shape_bytes(mod, left_rgb=(30, 70, 200))    # same shape, slightly different blue
    purple_v1 = shape_bytes(mod, left_rgb=(160, 30, 200)) # same shape, unrelated color

    insert_synced_image("synced-blue-1.jpg", blue_v1)
    insert_synced_image("synced-blue-2.jpg", blue_v2)
    insert_synced_image("synced-purple.jpg", purple_v1)

    r = admin.post("/api/duplicates/scan")
    body = r.get_json()
    groups = body.get("groups", [])

    def group_containing(filename):
        for g in groups:
            if any(img["filename"] == filename for img in g["images"]):
                return g
        return None

    blue_group = group_containing("synced-blue-1.jpg")
    purple_group = group_containing("synced-purple.jpg")
    check("The Duplicate Review scan runs and returns groups", isinstance(groups, list), body)
    check(
        "The two synced blue photos are grouped as a near-duplicate",
        blue_group is not None and len(blue_group["images"]) == 2
        and {img["filename"] for img in blue_group["images"]} == {"synced-blue-1.jpg", "synced-blue-2.jpg"},
        blue_group,
    )
    check(
        "The synced purple photo is NOT grouped with the blue photos (same shape, different color)",
        purple_group is None,
        purple_group,
    )

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED ❌")
        for f in failures:
            print(f"   - {f}")
    else:
        print("ALL DUPLICATE COLOR-CHECK TESTS PASSED ✅")

    shutil.rmtree(workdir, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

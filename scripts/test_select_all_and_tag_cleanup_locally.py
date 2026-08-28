"""
Frame Atlas — local test for V32: "select all N results" + library-wide tag
removal.

The bug this came from: Ryan searched "neon", selected 7 photos, and found no
"neon" in the shared-tags list to remove. Two causes, both covered here.

  1. /api/search/ids (new). "Select all loaded" only ever selected the
     thumbnails the grid had scrolled far enough to load — 60 of 118, with
     nothing on screen saying so. The new endpoint returns every id the
     filter matches. The critical property, tested filter by filter, is that
     it returns EXACTLY the set /api/search pages through: both now share
     build_search_filters(), and a select-all that disagreed with the grid
     would be worse than no select-all at all.

  2. /api/tags/removal-preview (new) + the existing /api/tags/bulk-remove.
     Removing a tag from every result of a search, with a look at the photos
     first. Grouped by category, because "car (Location)" and "car (Objects)"
     are two different true facts about a photo. Tag values go through
     normalize_tag_value() on the way in (V30), and the removal must never
     touch a photo row — only the tag.

Nothing leaves the machine; there is no Drive or Gemini call on these paths.

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_select_all_and_tag_cleanup_locally.py
"""

import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, 'backend'))


def fake_thumbnail(mod, n):
    img = mod.Image.new("RGB", (120, 68), (10, (20 + n * 7) % 200, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_v32_test_")
    db_path = os.path.join(workdir, "library.db")

    os.environ["FA_DB_PATH"] = db_path

    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy-client-id")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")

    spec = importlib.util.spec_from_file_location("fa_v32_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_v32_app"] = mod
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True
    mod.tagging.trigger_tagging = lambda *a, **k: None
    print("App imported OK.")

    failures = []

    def check(label, cond, detail=""):
        if cond:
            print(f"  {label} — OK")
        else:
            print(f"  {label} — FAIL  {detail}")
            failures.append(label)

    admin = mod.app.test_client()
    admin.post("/api/setup", json={"email": "ryan@test.com", "password": "adminpass123"})

    # ── Fixture library ──────────────────────────────────────────────────────
    # 20 admin photos with a deliberately awkward tag layout:
    #   1-8    lighting_quality / neon   <- the "bad tag" to clean up
    #   5-12   mood / neon             <- SAME value, DIFFERENT category
    #   1-20   mood / tense            <- must survive every removal
    #   1-6    subjects / car          <- stored normalized, queried as "cars"
    #   1-10   aspect ratio 2.39:1, 11-20 16:9
    #   1-5    gold palette, 6-20 teal palette
    #   1-3    filmography "Tenet"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    for n in range(1, 21):
        c.execute(
            "INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio)"
            " VALUES (?, 1, ?, ?, ?, ?)",
            (n, f"drive-{n}", f"img_{n}.jpg", fake_thumbnail(mod, n),
             "2.39:1" if n <= 10 else "16:9"),
        )
        tags = [("mood", "tense")]
        if n <= 8:
            tags.append(("lighting_quality", "neon"))
        if 5 <= n <= 12:
            tags.append(("mood", "neon"))
        if n <= 6:
            tags.append(("subjects", "car"))
        for cat, val in tags:
            c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, ?, ?)",
                      (n, cat, val))
        hexv = "#D9A441" if n <= 5 else "#2E8B8B"
        c.execute("INSERT INTO colors (image_id, user_id, hex, rank, share) VALUES (?, 1, ?, 1, 0.5)",
                  (n, hexv))
        if n <= 3:
            c.execute("INSERT INTO filmography (image_id, title, director, dp, year)"
                      " VALUES (?, 'Tenet', 'Christopher Nolan', 'Hoyte van Hoytema', '2020')", (n,))
    conn.commit()
    conn.close()
    print("Seeded 20 admin photos.\n")

    def search_all_ids(client, query, per=7):
        """Page through /api/search exactly like the grid does, and collect
        every id it hands back. This is the ground truth that /api/search/ids
        has to agree with."""
        ids, page = [], 0
        while True:
            r = client.get(f"/api/search?{query}&page={page}&per={per}")
            assert r.status_code == 200, r.get_json()
            data = r.get_json()
            ids += [i["id"] for i in data["images"]]
            if not data["has_more"]:
                return ids, data["total"]
            page += 1

    def select_all_ids(client, query):
        r = client.get(f"/api/search/ids?{query}")
        assert r.status_code == 200, r.get_json()
        return r.get_json()

    # ── 1. /api/search/ids agrees with /api/search, filter type by filter type ──
    print("1. Select-all returns exactly what the filter shows:")
    cases = [
        ("no filter at all", ""),
        ("chips (exact tag)", "chips=neon"),
        ("chips, two tags ANDed", "chips=neon,car"),
        ("natural language (any of a group)", "nl=" + json.dumps([["neon", "car"]])),
        ("natural language, two groups", "nl=" + json.dumps([["neon"], ["car"]])),
        ("colour", "color=%23D9A441"),
        ("colour with prom/exact knobs", "color=%23D9A441&prom=20&exact=80"),
        ("colour that matches nothing", "color=%23FF00FF&prom=90"),
        ("aspect ratio", "ar=2.39:1"),
        ("film / director / DP", "film=Tenet"),
        ("chips + aspect ratio + colour together", "chips=neon&ar=2.39:1&color=%23D9A441"),
    ]
    for label, query in cases:
        grid_ids, grid_total = search_all_ids(admin, query)
        got = select_all_ids(admin, query)
        check(f"{label}: same set as the grid ({grid_total} images)",
              sorted(got["ids"]) == sorted(grid_ids) and got["total"] == grid_total,
              f"grid={sorted(grid_ids)} ids={sorted(got['ids'])}")

    # The specific trap from the bug report: a page-limited grid must not
    # limit the selection.
    r = admin.get("/api/search?chips=neon&page=0&per=5")
    loaded = len(r.get_json()["images"])
    all_ids = select_all_ids(admin, "chips=neon")
    check("A grid showing only 5 of 12 still selects all 12",
          loaded == 5 and all_ids["total"] == 12, f"loaded={loaded} total={all_ids['total']}")

    # The shuffle seed only ever applies to the unfiltered grid; ids must not
    # care about it either way.
    seeded = select_all_ids(admin, "seed=abc123")
    check("A shuffle seed doesn't change which images are selected",
          sorted(seeded["ids"]) == list(range(1, 21)), seeded["total"])

    # ── 2. Removal preview: counts, grouping, and it writes nothing ──────────
    print("\n2. Tag removal preview (the look-before-you-remove step):")
    r = admin.get("/api/tags/removal-preview?value=neon&chips=neon")
    body = r.get_json()
    check("Preview returns 200", r.status_code == 200, body)
    groups = {g["category"]: g for g in body.get("groups", [])}
    check("Splits the two categories that share the value 'neon'",
          set(groups) == {"lighting_quality", "mood"}, list(groups))
    check("lighting_quality/neon counts 8 photos",
          groups.get("lighting_quality", {}).get("count") == 8, groups.get("lighting_quality", {}).get("count"))
    check("mood/neon counts 8 photos",
          groups.get("mood", {}).get("count") == 8, groups.get("mood", {}).get("count"))
    check("Preview hands back the full id list, not just the pictured sample",
          len(groups["lighting_quality"]["image_ids"]) == 8, groups["lighting_quality"]["image_ids"])
    check("Every previewed photo carries a thumbnail to look at",
          all(s["thumbnail"].startswith("data:image/jpeg;base64,")
              for s in groups["lighting_quality"]["samples"]),
          "missing thumbnails")

    # Narrow the filter: the preview must follow it, not the whole library.
    r = admin.get("/api/tags/removal-preview?value=neon&chips=neon&ar=2.39:1")
    narrowed = {g["category"]: g["count"] for g in r.get_json()["groups"]}
    check("Preview respects the active filter (2.39:1 only)",
          narrowed == {"lighting_quality": 8, "mood": 6}, narrowed)

    def tag_count(category, value):
        conn = sqlite3.connect(db_path)
        n = conn.execute("SELECT COUNT(*) FROM tags WHERE category = ? AND value = ?",
                         (category, value)).fetchone()[0]
        conn.close()
        return n

    def image_count():
        conn = sqlite3.connect(db_path)
        n = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        conn.close()
        return n

    check("Previewing changed nothing — it is read-only",
          tag_count("lighting_quality", "neon") == 8 and image_count() == 20,
          f"tags={tag_count('lighting_quality', 'neon')} images={image_count()}")

    # ── 3. Normalization on the way in (V30) ────────────────────────────────
    print("\n3. Tag values are normalized on every path:")
    r = admin.get("/api/tags/removal-preview?value=CARS")
    body = r.get_json()
    check("Preview for 'CARS' normalizes to the stored 'car'", body.get("value") == "car", body.get("value"))
    check("...and finds all 6 photos tagged 'car'",
          [g["count"] for g in body["groups"]] == [6], body.get("groups"))
    r = admin.get("/api/tags/removal-preview?value=%20%20")
    check("A blank value is rejected with 400", r.status_code == 400, r.get_json())

    # ── 4. Removing only touches the named tag ──────────────────────────────
    print("\n4. Removal only touches the tag it was told to:")
    ids = groups["lighting_quality"]["image_ids"]
    before_images = image_count()
    r = admin.post("/api/tags/bulk-remove",
                   json={"image_ids": ids, "category": "lighting_quality", "value": "neon"})
    check("bulk-remove reports 8 removed", r.get_json().get("removed") == 8, r.get_json())
    check("lighting_quality/neon is gone", tag_count("lighting_quality", "neon") == 0)
    check("mood/neon — same word, other category — is untouched",
          tag_count("mood", "neon") == 8, tag_count("mood", "neon"))
    check("mood/tense on all 20 photos is untouched", tag_count("mood", "tense") == 20)
    check("subjects/car is untouched", tag_count("subjects", "car") == 6)
    check("NO photo was deleted — this removes tags, never images",
          image_count() == before_images == 20, image_count())

    # Removing via a plural spelling still hits the stored singular.
    r = admin.post("/api/tags/bulk-remove",
                   json={"image_ids": [1, 2, 3], "category": "subjects", "value": "Cars"})
    check("Removing 'Cars' normalizes and removes the stored 'car'",
          r.get_json().get("removed") == 3 and tag_count("subjects", "car") == 3, r.get_json())

    # And the search the grid re-runs afterwards really does drop them.
    after = select_all_ids(admin, "chips=neon")
    check("Photos that lost the tag drop out of a chips=neon search",
          sorted(after["ids"]) == list(range(5, 13)), after["ids"])

    # ── 5. Admin-only, and libraries stay isolated ──────────────────────────
    print("\n5. Permissions:")
    friend_code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    reg = friend.post("/api/auth/register", json={
        "invite_code": friend_code, "username": "casey",
        "email": "casey@test.com", "password": "friendpass1"})
    check("Friend registration succeeds", reg.status_code == 200, reg.get_json())

    # The library-wide removal preview (V32) stays admin-only.
    r = friend.get("/api/tags/removal-preview?value=neon")
    check("Non-admin is refused the removal preview (403)", r.status_code == 403, r.get_json())
    # V75: bulk-remove is no longer 403 for a friend — but it only touches the
    # friend's OWN photos, so aiming it at the admin's images 5 & 6 removes
    # nothing and leaves every 'neon' tag in place.
    r = friend.post("/api/tags/bulk-remove",
                    json={"image_ids": [5, 6], "category": "mood", "value": "neon"})
    check("A friend's bulk-remove succeeds but is scoped to their own photos",
          r.status_code == 200 and r.get_json().get("removed") == 0, r.get_json())
    check("...so nothing the admin owns was removed by the attempt", tag_count("mood", "neon") == 8)

    anon = mod.app.test_client()
    r = anon.get("/api/search/ids")
    check("Logged-out visitor can't list ids at all", r.status_code in (401, 403), r.status_code)

    # search/ids is not admin-gated (friends use Select Mode too), but it is
    # scoped to the caller's own library like /api/search is.
    r = friend.get("/api/search/ids")
    check("A friend's select-all sees only their own (empty) library",
          r.status_code == 200 and r.get_json()["ids"] == [], r.get_json())

    # ── 6. Big selections don't blow the SQL placeholder limit ──────────────
    print("\n6. Selections larger than one SQL batch:")
    big_n = mod.SQL_PARAM_CHUNK * 2 + 37   # deliberately not a clean multiple
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    thumb = fake_thumbnail(mod, 1)
    for n in range(1000, 1000 + big_n):
        c.execute("INSERT INTO images (id, user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio)"
                  " VALUES (?, 1, ?, ?, ?, '4:3')", (n, f"drive-{n}", f"big_{n}.jpg", thumb))
        c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, 'misc', 'bulkytag')", (n,))
        c.execute("INSERT INTO tags (image_id, user_id, category, value) VALUES (?, 1, 'mood', 'keepme')", (n,))
    conn.commit()
    conn.close()
    print(f"  (seeded {big_n} more photos, all tagged 'bulkytag')")

    got = select_all_ids(admin, "chips=bulkytag")
    check(f"Select-all returns all {big_n} ids in one request",
          got["total"] == big_n and len(set(got["ids"])) == big_n, got["total"])

    r = admin.post("/api/tags/selection-summary", json={"image_ids": got["ids"]})
    summary = r.get_json()
    check("Shared-tag summary survives a selection that big",
          r.status_code == 200 and
          sorted((t["category"], t["value"]) for t in summary["tags"]) ==
          [("misc", "bulkytag"), ("mood", "keepme")], summary.get("tags"))
    check("Shared tags are the strict intersection (V31 behaviour intact)",
          all(t["count"] == summary["total"] for t in summary["tags"]), summary.get("tags"))

    r = admin.post("/api/tags/bulk-remove",
                   json={"image_ids": got["ids"], "category": "misc", "value": "bulkytag"})
    check(f"bulk-remove clears all {big_n} in one call (chunked internally)",
          r.get_json().get("removed") == big_n, r.get_json())
    check("The other tag on those same photos survived", tag_count("mood", "keepme") == big_n)
    check("All those photos still exist", image_count() == 20 + big_n, image_count())

    # A mixed selection: the intersection must exclude a tag only some carry.
    r = admin.post("/api/tags/selection-summary", json={"image_ids": [1, 2, 1000]})
    vals = {t["value"] for t in r.get_json()["tags"]}
    check("A tag on only some of the selection is correctly NOT shared",
          "tense" not in vals and "keepme" not in vals, vals)

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED ❌")
        for f in failures:
            print(f"   - {f}")
    else:
        print("ALL V32 SELECT-ALL + TAG-CLEANUP TESTS PASSED ✅")

    shutil.rmtree(workdir, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

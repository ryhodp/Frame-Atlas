"""
Frame Atlas — local test for Day 32 (V74): the Gemini auto-tag worker split
into backend/tagging.py.

Before this the tag loop lived in app.py and was only ever *disabled* by tests
(8 scripts set trigger_tagging to a no-op). This gives the module direct
coverage with a fake Gemini client:
  - split wiring: app.py exposes `tagging`; the moved names are NOT bare globals
    on app.py; genai_client stays on app.py (interpret/models still use it)
  - GEMINI_TAGGING_PROMPT still carries all 16 tag categories + "Return ONLY the JSON"
  - _select_pending_for_tagging: pending-before-failed ordering, 'done' excluded,
    keyless owner -> rows returned but images empty
  - _run_tagging_job_inner: valid JSON -> tags (normalized), caption, filmography
    written, tagging_status='done', _tag_progress advanced, usage recorded
  - a bad response -> image marked 'failed', loop continues, _tag_progress['failed']++
  - trigger_tagging synchronous branches: nothing pending -> 'complete'/"Nothing to
    tag."; pending but no key -> 'error'; the 'running' guard rejects re-entry
  - _broadcast_progress pushes a JSON payload (with pct) to a registered SSE queue
  - the app.py routes still work: GET /api/tag-progress reads tagging._tag_progress

Usage (from the frame-atlas folder):
    scripts/.venv/bin/python scripts/test_tagging_locally.py
"""

import importlib.util
import io
import json
import os
import queue as queue_module
import sqlite3
import sys
import tempfile
import time

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(REPO, "backend"))

PASS = 0
FAIL = 0


def check(label, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f"FAIL  {label}" + (f"  ({extra!r})" if extra is not None else ""))


def make_jpeg(mod, color=(120, 90, 60)):
    buf = io.BytesIO()
    mod.Image.new("RGB", (64, 64), color).save(buf, format="JPEG")
    return buf.getvalue()


class FakeUsage:
    prompt_token_count = 800
    candidates_token_count = 400


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.usage_metadata = FakeUsage()


class FakeModels:
    def __init__(self, script):
        self._script = script  # callable(contents) -> text, or raises

    def generate_content(self, model=None, contents=None):
        return FakeResponse(self._script(contents))


class FakeClient:
    def __init__(self, script):
        self.models = FakeModels(script)


GOOD_JSON = json.dumps({
    "caption": "A lone figure on a wet street",
    "tags": {
        "mood": ["Lonely", "tense"],
        "subjects": ["Cars", "streetlight"],   # 'Cars' must normalize to 'car'
        "location_type": ["urban-street"],
    },
    "filmography": {"title": "Drive", "director": "Nicolas Winding Refn", "dp": "Newton Thomas Sigel", "year": 2011},
})


def main():
    workdir = tempfile.mkdtemp(prefix="frame_atlas_tagging_test_")
    os.environ["FA_DB_PATH"] = os.path.join(workdir, "library.db")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy")
    os.environ.setdefault("GOOGLE_OAUTH_CLIENT_SECRET", "dummy")
    os.environ["GEMINI_API_KEY"] = "admin-shared-key"   # so get_user_gemini_key(1) is truthy
    os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    spec = importlib.util.spec_from_file_location("fa_tagging_test_app", os.path.join(REPO, "backend", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fa_tagging_test_app"] = mod
    spec.loader.exec_module(mod)
    print("App imported OK.")

    tagging = mod.tagging

    # ── 1. split wiring ───────────────────────────────────────────────────
    print("\n1. Split wiring")
    moved = ["_tag_progress", "_tag_progress_lock", "_sse_queues", "_sse_lock",
             "GEMINI_TAGGING_PROMPT", "_broadcast_progress", "_select_pending_for_tagging",
             "_run_tagging_job", "_run_tagging_job_inner", "trigger_tagging"]
    for name in moved:
        check(f"tagging.{name} exists", hasattr(tagging, name))
    leaked = [n for n in moved if n in vars(mod)]
    check("no moved name left as a bare global on app.py", leaked == [], leaked)
    check("genai_client stays imported on app.py (interpret/models use it)", hasattr(mod, "genai_client"))
    check("NL_INTERPRET_PROMPT stays on app.py (search, not tagging)", hasattr(mod, "NL_INTERPRET_PROMPT"))
    check("tagging shares core.get_db", tagging.get_db is mod.get_db)
    check("tagging shares core.normalize_tag_value", tagging.normalize_tag_value is mod.normalize_tag_value)

    # ── 2. prompt sanity ─────────────────────────────────────────────────
    print("\n2. GEMINI_TAGGING_PROMPT")
    p = tagging.GEMINI_TAGGING_PROMPT
    cats = ["mood", "lighting_quality", "lighting_color_temperature", "color_palette",
            "shot_type", "framing_composition", "location_type", "time_of_day_weather",
            "source_type", "subject_count", "subject_camera_relationship", "performance_emotion",
            "genre_aesthetic", "era_decade", "camera_format", "subjects"]
    check("all 16 tag categories named in the prompt", all(c in p for c in cats),
          [c for c in cats if c not in p])
    check("prompt still demands JSON-only output", "Return ONLY the JSON" in p)

    # ── 3. seed images + _select_pending_for_tagging ─────────────────────
    print("\n3. _select_pending_for_tagging")
    conn = sqlite3.connect(mod.DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    blob = make_jpeg(mod)
    ids = {}
    for name, status in [("done.jpg", "done"), ("failed.jpg", "failed"),
                         ("pend1.jpg", "pending"), ("pend2.jpg", "pending")]:
        c.execute("INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status)"
                  " VALUES (1,?,?,?, '16:9', ?)", (name, name, blob, status))
        ids[name] = c.lastrowid
    conn.commit()

    rows, images, clients = tagging._select_pending_for_tagging()
    row_ids = [r["id"] for r in rows]
    check("'done' image is excluded", ids["done.jpg"] not in row_ids, row_ids)
    check("pending sorts before failed", row_ids == [ids["pend1.jpg"], ids["pend2.jpg"], ids["failed.jpg"]], row_ids)
    check("admin has a key, so all 3 are taggable", len(images) == 3, len(images))

    # keyless friend: rows come back, images do not
    code = None
    admin = mod.app.test_client()
    assert admin.post("/api/setup", json={"email": "a@a.com", "password": "testpass123"}).status_code == 200
    code = admin.post("/api/admin/invite-codes").get_json()["code"]
    friend = mod.app.test_client()
    assert friend.post("/api/auth/register", json={
        "invite_code": code, "username": "casey", "email": "c@c.com", "password": "friendpass1"}).status_code == 200
    c.execute("INSERT INTO images (user_id, drive_file_id, filename, thumbnail_blob, aspect_ratio, tagging_status)"
              " VALUES (2,'ff.jpg','ff.jpg',?, '16:9','pending')", (blob,))
    conn.commit()
    rows2, images2, _ = tagging._select_pending_for_tagging(user_id=2)
    check("keyless friend: pending row returned but not taggable", len(rows2) == 1 and images2 == [], (len(rows2), len(images2)))

    # ── 4. _run_tagging_job_inner writes a good response ─────────────────
    print("\n4. _run_tagging_job_inner — happy path")
    tagging._tag_progress.update({"running": True, "total": 2, "done": 0, "failed": 0, "status": "running"})
    good_client = FakeClient(lambda contents: GOOD_JSON)
    target = c.execute("SELECT id, user_id, thumbnail_blob, filename FROM images WHERE filename='pend1.jpg'").fetchone()
    tagging._run_tagging_job_inner([target], {1: good_client})
    conn2 = sqlite3.connect(mod.DB_PATH); conn2.row_factory = sqlite3.Row
    d = conn2.cursor()
    tid = ids["pend1.jpg"]
    tags = {(r["category"], r["value"]) for r in d.execute("SELECT category, value FROM tags WHERE image_id=?", (tid,))}
    check("tags written and normalized ('Cars' -> 'car', 'Lonely' -> 'lonely')",
          ("subjects", "car") in tags and ("mood", "lonely") in tags, sorted(tags))
    cap = d.execute("SELECT caption FROM images WHERE id=?", (tid,)).fetchone()["caption"]
    check("caption written", cap == "A lone figure on a wet street", cap)
    fr = d.execute("SELECT title, director FROM filmography WHERE image_id=?", (tid,)).fetchone()
    check("filmography written", fr and fr["title"] == "Drive", fr and tuple(fr))
    st = d.execute("SELECT tagging_status FROM images WHERE id=?", (tid,)).fetchone()["tagging_status"]
    check("tagging_status flipped to 'done'", st == "done", st)
    check("_tag_progress advanced by 1", tagging._tag_progress["done"] == 1, tagging._tag_progress)
    usage = d.execute("SELECT input_tokens, output_tokens FROM gemini_usage WHERE user_id=1").fetchone()
    check("Gemini usage recorded for the owner", usage and usage["input_tokens"] == 800, usage and tuple(usage))

    # ── 5. a bad response marks the image failed, loop continues ─────────
    print("\n5. _run_tagging_job_inner — one bad, one good, in the same batch")
    tagging._tag_progress.update({"running": True, "total": 2, "done": 0, "failed": 0, "status": "running"})
    b = c.execute("SELECT id, user_id, thumbnail_blob, filename FROM images WHERE filename='pend2.jpg'").fetchone()
    g = c.execute("SELECT id, user_id, thumbnail_blob, filename FROM images WHERE filename='failed.jpg'").fetchone()

    def flaky(contents, _state={"n": 0}):
        _state["n"] += 1
        if _state["n"] == 1:
            return "this is not json at all"
        return GOOD_JSON

    tagging._run_tagging_job_inner([b, g], {1: FakeClient(flaky)})
    conn3 = sqlite3.connect(mod.DB_PATH); conn3.row_factory = sqlite3.Row
    e = conn3.cursor()
    check("bad response -> image marked 'failed'",
          e.execute("SELECT tagging_status FROM images WHERE id=?", (ids["pend2.jpg"],)).fetchone()["tagging_status"] == "failed")
    check("loop continued -> the second image still tagged 'done'",
          e.execute("SELECT tagging_status FROM images WHERE id=?", (ids["failed.jpg"],)).fetchone()["tagging_status"] == "done")
    check("_tag_progress['failed'] == 1, done == 2", tagging._tag_progress["failed"] == 1 and tagging._tag_progress["done"] == 2,
          dict(tagging._tag_progress))
    check("run ended 'complete'", tagging._tag_progress["status"] == "complete", tagging._tag_progress["status"])

    # ── 6. _broadcast_progress -> SSE queue ─────────────────────────────
    print("\n6. _broadcast_progress")
    q = queue_module.Queue()
    with tagging._sse_lock:
        tagging._sse_queues.append(q)
    tagging._tag_progress.update({"done": 3, "total": 6})
    tagging._broadcast_progress()
    payload = json.loads(q.get_nowait())
    check("SSE payload carries pct computed from done/total", payload.get("pct") == 50, payload)
    with tagging._sse_lock:
        tagging._sse_queues.remove(q)

    # ── 7. trigger_tagging synchronous branches ─────────────────────────
    print("\n7. trigger_tagging — synchronous decision")
    e.execute("UPDATE images SET tagging_status='done'")
    conn3.commit()
    tagging._tag_progress.update({"running": False})
    tagging.trigger_tagging()
    check("nothing pending -> status 'complete', 'Nothing to tag.'",
          tagging._tag_progress["status"] == "complete" and tagging._tag_progress["message"] == "Nothing to tag.",
          dict(tagging._tag_progress))

    e.execute("UPDATE images SET tagging_status='pending' WHERE user_id=2")
    conn3.commit()
    tagging._tag_progress.update({"running": False})
    tagging.trigger_tagging(user_id=2)   # friend has no key
    check("pending but no usable key -> status 'error'",
          tagging._tag_progress["status"] == "error" and "key" in tagging._tag_progress["message"].lower(),
          dict(tagging._tag_progress))

    tagging._tag_progress.update({"running": True})
    before = dict(tagging._tag_progress)
    tagging.trigger_tagging()
    check("the 'running' guard makes a re-entrant call a no-op", dict(tagging._tag_progress) == before)
    tagging._tag_progress.update({"running": False, "status": "idle", "message": ""})

    # ── 8. the app.py route still reads tagging._tag_progress ───────────
    print("\n8. GET /api/tag-progress (route stayed in app.py)")
    r = admin.get("/api/tag-progress")
    check("route responds 200 with a snapshot + status_counts",
          r.status_code == 200 and "status_counts" in r.get_json() and "pct" in r.get_json(), r.get_json())

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

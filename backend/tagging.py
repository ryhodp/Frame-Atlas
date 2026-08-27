"""tagging.py — Gemini auto-tag worker + live-progress plumbing (Day 32 / Phase 3).

The background loop that sends each untagged image's thumbnail to Gemini, parses
the JSON back into tags / caption / filmography, and streams progress to the UI
over Server-Sent Events. The tag-progress *routes* stay in app.py (Phase 3 keeps
Flask routes put) and reach this module's shared state as `tagging._tag_progress`,
`tagging._sse_queues`, etc.

Every function body here is character-for-character what it was in app.py — only
its home changed.

Phase 3 rules: imports only from core.py + gemini.py + the google.genai client,
never from app.py. app.py does `import tagging` and qualifies every call site.
"""
import io
import json
import time
import threading

from PIL import Image
from google import genai as genai_client

from core import get_db, GEMINI_MODEL, normalize_tag_value, clear_ai_tags
import gemini


# ── Shared progress state ──────────────────────────────────────────────────
# One tagging run at a time, app-wide. The routes in app.py read/write this
# same dict object (never rebound, only .update()'d) via `tagging._tag_progress`.
_tag_progress = {
    'running': False,
    'total': 0,
    'done': 0,
    'failed': 0,
    'status': 'idle',
    'message': ''
}
_tag_progress_lock = threading.Lock()
_sse_queues = []
_sse_lock = threading.Lock()


# ============================================================================
# GEMINI TAG TAXONOMY PROMPT
# ============================================================================

GEMINI_TAGGING_PROMPT = """Analyze this image and return ONLY a JSON object with no markdown, no backticks, no explanation.

Return exactly this structure:
{
  "caption": "One vivid sentence describing the image cinematically (e.g. 'Lone figure at rain-soaked payphone, hard sodium backlight, urban night')",
  "tags": {
    "mood": [],
    "lighting_quality": [],
    "lighting_color_temperature": [],
    "color_palette": [],
    "shot_type": [],
    "framing_composition": [],
    "location_type": [],
    "time_of_day_weather": [],
    "source_type": [],
    "subject_count": [],
    "subject_camera_relationship": [],
    "performance_emotion": [],
    "genre_aesthetic": [],
    "era_decade": [],
    "camera_format": [],
    "subjects": []
  },
  "filmography": {
    "title": null,
    "director": null,
    "dp": null,
    "year": null
  }
}

For cinematography tags, ONLY use tags from these allowed lists.
For subjects, identify any visible objects, people, animals, or elements in the frame — be specific and comprehensive (subjects are open-ended, not restricted to a list).

BE GENEROUS. This is a searchable reference library for a working cinematographer —
more tags means more discoverability. Include every tag that plausibly applies, not
just the single most obvious one per category. If an image sits between two moods,
tag both. If the lighting could read as both soft and low-key, tag both.
Aim for 12-25 tags total across all categories. Most categories should have at
least one tag; only leave an array empty [] when the category truly does not apply
(e.g. performance_emotion for a landscape with no people).

mood: lonely, intimate, tense, ominous, serene, chaotic, melancholic, warm, euphoric, epic, mundane, dreamlike, claustrophobic, vast
lighting_quality: hard, soft, motivated, unmotivated, single-source, practical-heavy, high-key, low-key, no-fill, bounce-heavy, silhouette, chiaroscuro
lighting_color_temperature: warm-tungsten, cool-daylight, mixed-sources, green-practical, neon, firelight, moonlight
color_palette: desaturated, high-contrast, monochromatic, warm-palette, cool-palette, earthy, high-saturation, bleach-bypass, golden, teal-orange
shot_type: extreme-wide, wide, medium-wide, medium, close-up, extreme-close-up, aerial, POV, over-shoulder, two-shot
framing_composition: centered, rule-of-thirds, dutch-angle, low-angle, high-angle, eye-level, negative-space, symmetrical, foreground-frame
location_type: interior, exterior, diner, hospital, warehouse, rooftop, forest, urban-street, office, home, car, bar, stage, industrial, desert, water
time_of_day_weather: golden-hour, magic-hour, midday, blue-hour, night, overcast, dawn, rain, fog, snow, harsh-sun
source_type: film-still, BTS, production-still, mood-texture, abstract
subject_count: no-subject, solo, pair, group, crowd
subject_camera_relationship: looking-at-camera, looking-away, profile, back-to-camera
performance_emotion: joy, grief, fear, rage, longing, neutral, shock, tenderness, defiance
genre_aesthetic: horror, western, sci-fi, romance, documentary, thriller, noir, drama, comedy, action
era_decade: period-piece, 70s, 80s, 90s, contemporary, futuristic
camera_format: 35mm-film, 16mm-film, anamorphic, spherical, digital, arri, red, sony, blackmagic
subjects: any objects, people, animals, or elements visible in the frame (e.g. man, woman, child, dog, cat, fish, horse, mountain, building, tree, water, fire, etc.)

For filmography: only fill in if this is clearly a recognizable film still. Otherwise leave null.
Return ONLY the JSON. No other text."""


# ============================================================================
# TAGGING PROGRESS — SSE HELPERS
# ============================================================================

def _broadcast_progress():
    with _tag_progress_lock:
        data = dict(_tag_progress)
    pct = int(data['done'] / data['total'] * 100) if data['total'] > 0 else 0
    payload = json.dumps({**data, 'pct': pct})
    with _sse_lock:
        dead = []
        for q in _sse_queues:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_queues.remove(q)


# ============================================================================
# TAGGING WORKER
# ============================================================================

def _select_pending_for_tagging(user_id=None):
    """The query half of a tagging run, split out from the loop that
    actually calls Gemini (V48) — see trigger_tagging() for why this needs
    to happen synchronously in the CALLER's thread rather than inside the
    background worker thread."""
    conn = get_db()
    c = conn.cursor()
    query = """
        SELECT id, user_id, thumbnail_blob, filename
        FROM images
        WHERE tagging_status != 'done'
        {owner_filter}
        ORDER BY
            CASE tagging_status
                WHEN 'pending' THEN 0
                WHEN 'failed'  THEN 1
                ELSE 2
            END,
            id ASC
    """
    if user_id is not None:
        rows = c.execute(query.format(owner_filter='AND user_id = ?'), (user_id,)).fetchall()
    else:
        rows = c.execute(query.format(owner_filter='')).fetchall()
    conn.close()

    clients = {}
    images = []
    for row in rows:
        owner_id = row['user_id']
        if owner_id not in clients:
            key = gemini.get_user_gemini_key(owner_id)
            clients[owner_id] = genai_client.Client(api_key=key) if key else None
        if clients[owner_id] is not None:
            images.append(row)

    return rows, images, clients


def _run_tagging_job_inner(images, clients, user_id=None):
    """user_id=None tags every pending/failed image across every owner (the
    admin's global 'tag now' / post-sync trigger). A specific user_id scopes
    the run to just that person's own library (friend's 'Tag my photos').
    Either way, each image is tagged with ITS OWNER's key — owners who
    haven't saved a key are skipped, their photos left untagged but
    searchable, at zero cost to anyone.

    Takes the already-resolved (images, clients) from
    _select_pending_for_tagging() rather than querying again — see
    trigger_tagging() for why that decision has to happen before this
    function's thread even starts."""
    for img in images:
        img_id = img['id']
        owner_id = img['user_id']
        thumb_blob = img['thumbnail_blob']
        filename = img['filename']
        client = clients[owner_id]

        try:
            pil_img = Image.open(io.BytesIO(thumb_blob))

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[GEMINI_TAGGING_PROMPT, pil_img]
            )
            gemini.record_gemini_usage(owner_id, getattr(response, 'usage_metadata', None))
            raw = response.text.strip()

            if raw.startswith('```'):
                raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()

            data = json.loads(raw)

            conn = get_db()
            c = conn.cursor()

            # V15: replace only the AI's own tags. Manual categories (My Work,
            # misc) are human decisions — a re-tag must never erase them.
            clear_ai_tags(c, img_id)
            for category, values in data.get('tags', {}).items():
                for val in values:
                    if val and val.strip():
                        # normalize_tag_value: lowercase + plural-collapse, to
                        # match every other tag-writing path (manual edit,
                        # bulk apply) — Gemini's word choice isn't consistent
                        # run to run, so "Tense"/"tense" or "car"/"cars" would
                        # otherwise sit as separate-looking duplicates
                        # anywhere tags get grouped (autocomplete, detail
                        # panel, analytics).
                        c.execute(
                            "INSERT INTO tags (image_id, user_id, category, value) VALUES (?, ?, ?, ?)",
                            (img_id, owner_id, category, normalize_tag_value(val))
                        )

            caption = data.get('caption', '')
            if caption:
                c.execute("UPDATE images SET caption = ? WHERE id = ?", (caption, img_id))

            film = data.get('filmography', {})
            if any(film.get(k) for k in ['title', 'director', 'dp', 'year']):
                c.execute("DELETE FROM filmography WHERE image_id = ?", (img_id,))
                c.execute(
                    "INSERT INTO filmography (image_id, title, director, dp, year) VALUES (?,?,?,?,?)",
                    (img_id, film.get('title'), film.get('director'), film.get('dp'), str(film.get('year', '')))
                )

            c.execute("UPDATE images SET tagging_status = 'done' WHERE id = ?", (img_id,))
            conn.commit()
            conn.close()

            with _tag_progress_lock:
                _tag_progress['done'] += 1
                remaining = _tag_progress['total'] - _tag_progress['done']
                _tag_progress['message'] = f"Tagged {_tag_progress['done']} of {_tag_progress['total']} — {remaining} remaining"

        except Exception as e:
            print(f"[tagging] Failed {filename}: {e}")
            try:
                conn = get_db()
                c = conn.cursor()
                c.execute("UPDATE images SET tagging_status = 'failed' WHERE id = ?", (img_id,))
                conn.commit()
                conn.close()
            except Exception as mark_err:
                # Still swallowed on purpose — the tagging run must continue
                # through the remaining images — but no longer invisibly. An
                # image stuck at 'pending' despite having failed is otherwise
                # indistinguishable from one never attempted (V44/Day 26).
                print(f"[tagging] Could not mark image {img_id} as failed: {mark_err}")
            with _tag_progress_lock:
                _tag_progress['failed'] += 1
                _tag_progress['done'] += 1

        _broadcast_progress()
        time.sleep(0.05)

    with _tag_progress_lock:
        failed = _tag_progress['failed']
        total = _tag_progress['total']
        _tag_progress.update({
            'running': False,
            'status': 'complete',
            'message': f"Sync complete! Tagged {total - failed} images." + (f" {failed} failed." if failed else "")
        })
    _broadcast_progress()


def _run_tagging_job(images, clients, user_id=None):
    try:
        _run_tagging_job_inner(images, clients, user_id=user_id)
    except Exception as e:
        print(f"[tagging] Job failed: {e}")
        with _tag_progress_lock:
            _tag_progress.update({'running': False, 'status': 'error', 'message': str(e)})
        _broadcast_progress()


def trigger_tagging(user_id=None):
    """V48: the "is there anything to tag" decision — the DB query and the
    per-owner Gemini-key check — now happens SYNCHRONOUSLY, in the caller's
    own thread, before this function returns. Only the actual per-image
    tagging loop (the slow part, one Gemini call per photo) is handed off to
    a background thread.

    This matters because sync_folder_worker calls this from its own finally
    block right before flipping sync_state['in_progress'] to False, and the
    Home page's background-sync toast watches for that flip to know when to
    check whether a tagging phase followed. Before this split, the decision
    itself ran inside the spawned thread, so there was a real window where
    frontend polling could see in_progress=False and _tag_progress still
    showing yesterday's stale 'running': false — indistinguishable from "no
    tagging needed" even though a tagging run was about to start (or, for a
    handful of already-failing images, had already started AND finished).
    Resolving it here means _tag_progress is always caught up by the time
    in_progress flips, no polling delay needed on the frontend to paper over
    the gap."""
    with _tag_progress_lock:
        if _tag_progress['running']:
            return

    rows, images, clients = _select_pending_for_tagging(user_id)

    if not images:
        # "Nothing pending at all" (the routine case after a re-sync with no
        # new photos) is not the same failure as "photos are pending but
        # nobody has a usable key" — conflating them as one 'error' branch
        # was actively wrong for the first case (admin always has a key)
        # and made a background sync-then-tag toast look like it failed
        # every time a sync brought in nothing new.
        with _tag_progress_lock:
            if not rows:
                _tag_progress.update({'running': False, 'status': 'complete', 'message': 'Nothing to tag.'})
            else:
                _tag_progress.update({
                    'running': False, 'status': 'error',
                    'message': 'No Gemini API key available for the queued photos.'
                })
        _broadcast_progress()
        return

    with _tag_progress_lock:
        _tag_progress.update({
            'running': True,
            'total': len(images),
            'done': 0,
            'failed': 0,
            'status': 'running',
            'message': f'Tagging {len(images)} images…'
        })
    _broadcast_progress()

    t = threading.Thread(target=_run_tagging_job, kwargs={'images': images, 'clients': clients, 'user_id': user_id}, daemon=True)
    t.start()

"""core.py — Frame Atlas shared foundation (Day 28 / Phase 3).

The genuinely shared, low-level pieces every other backend module leans on:
the database connection, tag-value normalisation, the tag-category display
maps, and the Gemini model/pricing constants. Split out of app.py verbatim —
every function here is character-for-character what it was, only its home
changed.

Rule for this phase: this module imports from nothing in the project (only
the standard library). app.py and the other new modules import FROM here.
"""
import os
import sqlite3
import zlib

# FA_DB_PATH lets test scripts point the app at a throwaway database without
# editing any source file (V45 part 2) — unset in production, so Railway keeps
# using the real path with zero config change. Read live via db_path() rather
# than snapshotted at import, so a test harness that boots several app
# instances in one process (each with its own FA_DB_PATH) gets the right file
# every time even though this module is imported only once.
DEFAULT_DB_PATH = '/app/data/library.db'


def db_path():
    return os.environ.get('FA_DB_PATH', DEFAULT_DB_PATH)


# Gemini model — overridable via Railway env var if Google retires this one
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')

# USD per 1,000,000 tokens. Every user is expected to run the same
# GEMINI_MODEL (see get_user_gemini_key) so one entry covers everyone — if
# that ever changes, add a row here per model.
GEMINI_PRICING = {
    'gemini-2.5-flash': {'input': 0.30, 'output': 2.50},
}
DEFAULT_GEMINI_PRICING = {'input': 0.30, 'output': 2.50}

def get_model_pricing(model_name):
    return GEMINI_PRICING.get(model_name, DEFAULT_GEMINI_PRICING)


# Fixed tag category taxonomy — display color/label for each of the 15
# categories Gemini tags images with. Used by /api/autocomplete,
# /api/tag-categories, and the bulk tag endpoints below.
CAT_COLORS = {
    'mood': '#8b7cf6', 'lighting_quality': '#f59e0b',
    'lighting_color_temperature': '#f97316', 'color_palette': '#ec4899',
    'shot_type': '#06b6d4', 'framing_composition': '#10b981',
    'location_type': '#84cc16', 'time_of_day_weather': '#c9a253',
    'source_type': '#6366f1', 'subject_count': '#94a3b8',
    'subject_camera_relationship': '#a78bfa', 'genre_aesthetic': '#f43f5e',
    'era_decade': '#fb923c', 'camera_format': '#22d3ee',
    'performance_emotion': '#e879f9',
    'subjects': '#f472b6',
    'my_work': '#d9a441',
}
CAT_LABELS = {
    'mood': 'Mood', 'lighting_quality': 'Lighting',
    'lighting_color_temperature': 'Color Temp', 'color_palette': 'Palette',
    'shot_type': 'Shot', 'framing_composition': 'Framing',
    'location_type': 'Location', 'time_of_day_weather': 'Time / Weather',
    'source_type': 'Source', 'subject_count': 'Subjects',
    'subject_camera_relationship': 'Camera Rel.', 'genre_aesthetic': 'Genre',
    'era_decade': 'Era', 'camera_format': 'Format',
    'performance_emotion': 'Emotion',
    'subjects': 'Objects',
    'my_work': 'My Work',
}

# V15: categories only a human can apply — the AI tagger never writes these,
# and re-tagging an image must never delete them. 'my_work' marks Ryan's own
# projects (gaffed / DP'd / photographed); 'misc' is the free-form bucket the
# manual tag editor uses when no category is picked.
MANUAL_TAG_CATEGORIES = ('misc', 'my_work')

# V32: how many `?` placeholders we're willing to put in one statement. SQLite
# has a hard cap (999 on older builds), and "remove this tag from all 2,000
# results" can now hand a query the whole filtered library in one go, so any
# id list that could come from a select-all gets sliced into batches this big.
SQL_PARAM_CHUNK = 400

def chunked(seq, size=SQL_PARAM_CHUNK):
    """Slice a list into batches small enough to pass as SQL placeholders."""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]

# V30: words where stripping a trailing 's' would be wrong — either it isn't
# a plural at all (glass, lens, gas), or the plural is itself the natural
# search term (hands: "two hands in frame" is a distinct, useful composition
# detail from "a hand," not drift to collapse away).
TAG_PLURAL_STRIP_EXCEPTIONS = {
    'glass', 'glasses', 'sunglasses', 'grass', 'lens', 'bus', 'gas',
    'dress', 'stairs', 'clothes', 'scissors', 'binoculars', 'headlights',
    'hands', 'news', 'series', 'species',
}

def normalize_tag_value(value):
    """Lowercase and (V30) collapse a trailing plural 's' so 'car' and 'cars'
    land as the same searchable tag. Only the fixed-vocabulary categories
    (mood, location_type, etc.) are truly closed lists — `subjects` is
    explicitly open-ended free text in the tagging prompt, which is exactly
    where an LLM's own singular/plural word choice drifts run to run. This
    mirrors the lowercase-casing fix already applied at every tag-write site
    for the same reason (Gemini's casing isn't consistent either).

    Deliberately conservative: only strips a bare trailing 's' (not 'es'/
    'ies', which usually change the stem and are more likely a genuinely
    singular word that happens to end in 's') and skips a short exception
    list where the plural is itself the natural tag."""
    v = (value or '').strip().lower()
    if len(v) > 3 and v.endswith('s') and not v.endswith('ss') and v not in TAG_PLURAL_STRIP_EXCEPTIONS:
        return v[:-1]
    return v

def clear_ai_tags(cursor, image_id):
    """Delete an image's AI-written tags ahead of a re-tag, preserving every
    manually-applied category (see MANUAL_TAG_CATEGORIES)."""
    ph = ','.join('?' * len(MANUAL_TAG_CATEGORIES))
    cursor.execute(
        f"DELETE FROM tags WHERE image_id = ? AND category NOT IN ({ph})",
        (image_id, *MANUAL_TAG_CATEGORIES))


def favorite_col(user_id, alias='images'):
    """SQL fragment computing is_favorite for one user against the per-user
    user_favorites table — slots into any `images` SELECT in place of the old
    boolean column. user_id is always an int pulled from the session (never
    request input), so inlining it directly is safe and avoids threading an
    extra positional param through call sites that already build dynamic
    WHERE clauses.

    (V55: used to also compute is_flagged against user_flags for the since-
    removed Flagged feature. That table and the legacy is_flagged column on
    `images` deliberately still exist — see the removal note above
    get_utility_view() — but nothing queries or serves is_flagged anymore, so
    this function only computes the one column it's now named for.)

    Day 31 (Phase 3): moved here from app.py so images_common._fetch_image_dict
    can build its own favourite-aware SELECT without importing app.py. app.py's
    other call sites are unchanged — it re-imports this name from core."""
    uid = int(user_id)
    return f"EXISTS(SELECT 1 FROM user_favorites uf WHERE uf.user_id = {uid} AND uf.image_id = {alias}.id) AS is_favorite"


def _shuffle_key(seed, image_id):
    # Deterministic pseudo-random sort key: the same (seed, image) pair always
    # produces the same number, so page 2 of a shuffled feed continues exactly
    # where page 1 left off. A new seed produces a completely different order.
    # crc32 (unlike Python's hash()) gives identical results across restarts.
    return zlib.crc32(f'{seed}:{image_id}'.encode())


def get_db():
    # db_path() (not a module-level constant) so a multi-boot test harness
    # picks up each instance's FA_DB_PATH — see the note above.
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.create_function('shuffle_key', 2, _shuffle_key)
    return conn


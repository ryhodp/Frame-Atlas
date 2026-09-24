"""search_filters.py — the ONE place search query params become a WHERE clause (Day 37 / Phase 3).

build_search_filters() and its FTS5 helper _fts5_match_query() moved here out
of app.py, character-for-character. They are deliberately NOT in
routes_search.py: /api/search and /api/search/ids (routes_search.py) AND the
library-wide tag-removal preview (/api/tags/removal-preview, Day 38's
routes_tags.py) all filter through this same function — see V32's note on why
a second hand-copied version would drift. A plain helper module lets both
blueprints import it without either blueprint importing the other.

No Flask, no request object: callers pass request.args in as `args`.
"""
import json

from colors import (
    DEFAULT_EXACTNESS, DEFAULT_PROMINENCE,
    exactness_to_hue_tol, exactness_to_value_tol,
    color_matches, color_match_share,
)
from imaging import normalize_ar_label, ar_float_from_str


def _fts5_match_query(phrase, prefix=False):
    """Turns a raw user phrase into a safe notes_fts MATCH query. A raw
    phrase can't go straight into MATCH — FTS5's query syntax gives meaning
    to characters like -, ", *, : — so every token is quoted to be treated
    literally. A bareword sequence of quoted tokens implicitly ANDs them,
    which is exactly the "forgiving of word order" behavior Ryan wants
    (an Omnisearch-style match, not a rigid substring/phrase match).

    prefix=True additionally leaves the LAST token unquoted with a trailing
    * (FTS5 prefix syntax), for live-typing autocomplete against a query
    that isn't finished yet. Embedded double-quote characters are stripped
    from every token first — otherwise one could break out of the quoting."""
    tokens = [t.replace('"', '') for t in phrase.split() if t.replace('"', '')]
    if not tokens:
        return None
    if prefix:
        *head, last = tokens
        parts = [f'"{t}"' for t in head]
        # The trailing token is deliberately left UNQUOTED so the * prefix
        # wildcard means anything to FTS5 — but that also means every OTHER
        # FTS5-meaningful character (-, *, :, (, ), ^) is live here too, not
        # neutralized by quoting the way it is for every other token above.
        # Strip to alphanumerics before appending the wildcard (verified: a
        # raw token like `weird"-*query` 500'd the endpoint before this).
        last_clean = ''.join(ch for ch in last if ch.isalnum())
        if last_clean:
            parts.append(f'{last_clean}*')
        if not parts:
            return None
    else:
        parts = [f'"{t}"' for t in tokens]
    return ' '.join(parts)

def build_search_filters(c, uid, args):
    """Turn the search query params into (conditions, params, is_unfiltered)
    for a WHERE clause over the `images` table.

    V32: pulled out of search() so /api/search, /api/search/ids and the tag
    removal preview all filter through ONE piece of code. A "select all 118
    results" button that quietly disagreed with the 118 results on screen
    would be worse than having no button at all, and a second hand-copied
    version of five filter types (chips / natural language / colour /
    aspect ratio / film) will drift apart the first time one of them changes.

    Every condition here refers to plain `images` columns (`user_id`, `id`),
    never a table alias, so callers can also drop the whole WHERE clause
    inside a `SELECT id FROM images ...` subquery.
    """
    chips_raw = args.get('chips', '').strip()
    nl_raw = args.get('nl', '').strip()
    notes_raw = args.get('notes', '').strip()  # V39: JSON array of on-set-notes phrases
    color_raw = args.get('color', '').strip()
    film_raw = args.get('film', '').strip()
    ar_raw = args.get('ar', '').strip()  # V15: aspect-ratio bucket, e.g. "2.39:1"
    # V24: color search knobs. Absent (old bookmarks, old clients) = the new
    # defaults, which is the agreed behaviour — a saved search returns fewer,
    # cleaner results than it used to rather than keeping the old noise.
    try:
        prominence = float(args.get('prom', DEFAULT_PROMINENCE))
    except ValueError:
        prominence = DEFAULT_PROMINENCE
    try:
        exactness = float(args.get('exact', DEFAULT_EXACTNESS))
    except ValueError:
        exactness = DEFAULT_EXACTNESS
    prominence = max(0.0, min(100.0, prominence))
    active_chips = [t.strip() for t in chips_raw.split(',') if t.strip()] if chips_raw else []

    # NL groups: JSON array of tag arrays. Image must match >=1 tag per group.
    nl_groups = []
    if nl_raw:
        try:
            parsed = json.loads(nl_raw)
            nl_groups = [[str(t) for t in g] for g in parsed if isinstance(g, list) and g]
        except Exception:
            nl_groups = []

    # V39: notes phrases. JSON array of plain strings — each is its own
    # AND'd notes_fts MATCH, same shape as an nl_groups entry above. Invalid
    # JSON or a non-list just means no notes filter, never a 500.
    notes_phrases = []
    if notes_raw:
        try:
            parsed = json.loads(notes_raw)
            notes_phrases = [str(p) for p in parsed if isinstance(p, str) and p.strip()]
        except Exception:
            notes_phrases = []

    conditions = ['user_id = ?']
    params = [uid]

    if active_chips:
        placeholders = ','.join('?' * len(active_chips))
        conditions.append(f'''id IN (
            SELECT image_id FROM tags WHERE value IN ({placeholders})
            GROUP BY image_id HAVING COUNT(DISTINCT value) = ?
        )''')
        params.extend(active_chips + [len(active_chips)])

    for group in nl_groups:
        gph = ','.join('?' * len(group))
        conditions.append(f'id IN (SELECT image_id FROM tags WHERE value IN ({gph}))')
        params.extend(group)

    for phrase in notes_phrases:
        match_query = _fts5_match_query(phrase)
        if not match_query:
            continue
        conditions.append('id IN (SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?)')
        params.append(match_query)

    if color_raw:
        # Small library — compute color matches in Python.
        # V24: an image matches when the palette entries close enough in hue
        # to the picked color TOGETHER cover at least `prominence` percent of
        # the frame. This is what kills the old false positives: a lipstick-
        # sized patch of red is a real red, but it's ~1% of the frame, so it
        # only survives at a low prominence setting.
        hue_tol = exactness_to_hue_tol(exactness)
        value_tol = exactness_to_value_tol(exactness)
        min_share = prominence / 100.0

        entries_by_image = {}
        legacy_rank_hits = set()  # pre-V24 rows: share unknown
        for row in c.execute(
            'SELECT image_id, hex, rank, share FROM colors WHERE user_id = ?', (uid,)
        ).fetchall():
            entries_by_image.setdefault(row['image_id'], []).append((row['hex'], row['share']))
            if row['share'] is None and row['rank'] is not None and row['rank'] <= 5:
                legacy_rank_hits.add(row['image_id'])

        matched_ids = set()
        for image_id, entries in entries_by_image.items():
            if color_match_share(color_raw, entries, hue_tol, value_tol) >= min_share:
                matched_ids.add(image_id)
                continue
            # Graceful degradation: palettes extracted before V24 have no
            # share, so prominence can't be judged. Rather than have color
            # search go silently empty until the backfill runs, fall back to
            # the old hue-only test on the top ranks for those images.
            if image_id in legacy_rank_hits and any(
                s is None and color_matches(color_raw, h, hue_tol, value_tol)
                for h, s in entries
            ):
                matched_ids.add(image_id)

        if matched_ids:
            cph = ','.join('?' * len(matched_ids))
            conditions.append(f'id IN ({cph})')
            params.extend(list(matched_ids))
        else:
            conditions.append('1 = 0')

    if ar_raw:
        # V15: aspect-ratio filter. Same trick as the color filter above —
        # small library, so snap every image to its nearest standard format
        # in Python (identical math to the ar_label shown on tiles) and pass
        # the matching ids into SQL.
        ar_ids = [
            row['id'] for row in c.execute(
                'SELECT id, aspect_ratio FROM images WHERE user_id = ?', (uid,)
            ).fetchall()
            if normalize_ar_label(ar_float_from_str(row['aspect_ratio'])) == ar_raw
        ]
        if ar_ids:
            aph = ','.join('?' * len(ar_ids))
            conditions.append(f'id IN ({aph})')
            params.extend(ar_ids)
        else:
            conditions.append('1 = 0')

    if film_raw:
        # Clicking a name in the detail panel sends the exact string, so try an
        # exact (case-insensitive) match first. Only fall back to substring
        # matching when nothing matches exactly — otherwise a short title like
        # "Her" would also return every "Christopher Nolan" film.
        #
        # V81: painter/photographer joined title/director/dp here — clicking
        # "Rembrandt" or "Ansel Adams" in the detail panel needs to filter the
        # same way clicking a director already does.
        exact_hit = c.execute('''
            SELECT 1 FROM filmography
            WHERE title = ? COLLATE NOCASE OR director = ? COLLATE NOCASE
               OR dp = ? COLLATE NOCASE OR painter = ? COLLATE NOCASE
               OR photographer = ? COLLATE NOCASE LIMIT 1
        ''', (film_raw, film_raw, film_raw, film_raw, film_raw)).fetchone()
        if exact_hit:
            conditions.append('''id IN (
                SELECT image_id FROM filmography
                WHERE title = ? COLLATE NOCASE OR director = ? COLLATE NOCASE
                   OR dp = ? COLLATE NOCASE OR painter = ? COLLATE NOCASE
                   OR photographer = ? COLLATE NOCASE
            )''')
            params.extend([film_raw, film_raw, film_raw, film_raw, film_raw])
        else:
            like = f'%{film_raw}%'
            conditions.append('''id IN (
                SELECT image_id FROM filmography
                WHERE title LIKE ? OR director LIKE ? OR dp LIKE ?
                   OR painter LIKE ? OR photographer LIKE ?
            )''')
            params.extend([like, like, like, like, like])

    is_unfiltered = not (active_chips or nl_groups or notes_phrases or color_raw or film_raw or ar_raw)
    return conditions, params, is_unfiltered

"""routes_search.py — Frame Atlas search routes as a Flask Blueprint (Day 37 / Phase 3).

/api/search, /api/search/ids, /api/autocomplete, /api/interpret (natural-
language search via Gemini), /api/bookmarks (saved searches), and
/api/images/<id>/similar ("more like this"). Every route body is
character-for-character what it was in app.py — only @app.route became
@bp.route, and every URL path is byte-identical, so the frontend needed zero
changes.

The filter-building code these routes share with the tag-removal preview
lives in search_filters.py, not here — see that file's docstring.

/api/tag-categories and /api/filmography/autocomplete stayed in app.py on
purpose: the build plan gives tag-categories to Day 38 (tags) and filmography
autocomplete to Day 39, next to the filmography editor it serves.
"""
import json
from array import array

from flask import Blueprint, jsonify, request, session
from google import genai as genai_client

from core import (
    GEMINI_MODEL, CAT_COLORS, CAT_LABELS,
    get_db, favorite_col, current_user_id,
)
from imaging import normalize_ar_label, ar_float_from_str, ar_query_labels
from search_filters import build_search_filters, _fts5_match_query
import gemini
import images_common

bp = Blueprint('search', __name__)


NL_INTERPRET_PROMPT = """You translate a cinematographer's search phrase into tags from a fixed taxonomy.

ALLOWED TAGS (use ONLY these, exactly as written):
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
subjects: man, woman, child, couple, wedding, hand, hands, body, face, animal, dog, cat, horse, bird, building, house, car, door, window, street, bridge, fire, water, mirror, glass, weapon, crowd, performance

Pick the 2-5 tags that best capture the FEELING and VISUAL QUALITIES of the phrase.
Return ONLY a JSON array of tag strings, e.g. ["lonely","low-key","night"]. No markdown, no explanation.

Phrase: """

@bp.route('/api/interpret', methods=['POST'])
def interpret_nl():
    phrase = (request.get_json() or {}).get('phrase', '').strip()
    if not phrase:
        return jsonify({'error': 'No phrase provided'}), 400

    uid = current_user_id()
    gemini_api_key = gemini.get_user_gemini_key(uid)
    if not gemini_api_key:
        return jsonify({'error': 'Add your Gemini API key in Account settings to use natural-language search.'}), 400

    try:
        client = genai_client.Client(api_key=gemini_api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[NL_INTERPRET_PROMPT + phrase]
        )
        gemini.record_gemini_usage(uid, getattr(response, 'usage_metadata', None))
        raw = response.text.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        tags = json.loads(raw)
        if not isinstance(tags, list):
            return jsonify({'error': 'Bad interpretation'}), 500
        tags = [str(t).strip() for t in tags if str(t).strip()][:5]
        return jsonify({'phrase': phrase, 'tags': tags})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bp.route('/api/autocomplete')
def autocomplete():
    q = request.args.get('q', '').strip().lower()
    active_chips = [t.strip() for t in request.args.get('chips', '').split(',') if t.strip()]

    if not q:
        return jsonify([])

    uid = session['user_id']
    conn = get_db()
    c = conn.cursor()

    if active_chips:
        placeholders = ','.join('?' * len(active_chips))
        rows = c.execute(f'''
            SELECT t.value, t.category, COUNT(*) as cnt
            FROM tags t
            WHERE t.user_id = ?
            AND t.image_id IN (
                SELECT image_id FROM tags
                WHERE value IN ({placeholders})
                GROUP BY image_id
                HAVING COUNT(DISTINCT value) = ?
            )
            AND LOWER(t.value) LIKE ?
            AND t.value NOT IN ({placeholders})
            GROUP BY t.value, t.category
            ORDER BY cnt DESC
            LIMIT 20
        ''', [uid] + active_chips + [len(active_chips), f'{q}%'] + active_chips).fetchall()
    else:
        rows = c.execute('''
            SELECT value, category, COUNT(*) as cnt
            FROM tags
            WHERE user_id = ? AND LOWER(value) LIKE ?
            GROUP BY value, category
            ORDER BY cnt DESC
            LIMIT 20
        ''', (uid, f'{q}%')).fetchall()

    # Filmography matches — lets the same search bar find "Her" by title,
    # "Spike Jonze" by director/DP, "Rembrandt" by painter, or "Ansel Adams"
    # by photographer — reusing the exact film= filter that clicking a name
    # in the detail panel already applies (see /api/search).
    #
    # Matches anywhere in the value, not just the start — "Ozu" finds
    # "Yasujiro Ozu", "Korra" finds "The Legend of Korra" — since the useful
    # search word is routinely NOT the first one. Without this, typing a
    # last name that finds nothing here meant Enter fell through to the NL
    # interpreter instead of setting the film= filter, silently failing a
    # search that should have worked (same bug class as the filmography
    # editor's own autocomplete, fixed separately in
    # /api/filmography/autocomplete). A true prefix match still ranks above
    # a same-frequency contains-only match via the prefix_rank tiebreaker.
    like = f'%{q}%'
    prefix_like = f'{q}%'
    film_rows = c.execute('''
        SELECT f.title AS value, 'title' AS field, COUNT(DISTINCT f.image_id) AS cnt,
               CASE WHEN LOWER(f.title) LIKE ? THEN 0 ELSE 1 END AS prefix_rank
        FROM filmography f JOIN images i ON i.id = f.image_id
        WHERE i.user_id = ? AND f.title IS NOT NULL AND LOWER(f.title) LIKE ?
        GROUP BY f.title
        UNION ALL
        SELECT f.director, 'director', COUNT(DISTINCT f.image_id),
               CASE WHEN LOWER(f.director) LIKE ? THEN 0 ELSE 1 END
        FROM filmography f JOIN images i ON i.id = f.image_id
        WHERE i.user_id = ? AND f.director IS NOT NULL AND LOWER(f.director) LIKE ?
        GROUP BY f.director
        UNION ALL
        SELECT f.dp, 'dp', COUNT(DISTINCT f.image_id),
               CASE WHEN LOWER(f.dp) LIKE ? THEN 0 ELSE 1 END
        FROM filmography f JOIN images i ON i.id = f.image_id
        WHERE i.user_id = ? AND f.dp IS NOT NULL AND LOWER(f.dp) LIKE ?
        GROUP BY f.dp
        UNION ALL
        SELECT f.painter, 'painter', COUNT(DISTINCT f.image_id),
               CASE WHEN LOWER(f.painter) LIKE ? THEN 0 ELSE 1 END
        FROM filmography f JOIN images i ON i.id = f.image_id
        WHERE i.user_id = ? AND f.painter IS NOT NULL AND LOWER(f.painter) LIKE ?
        GROUP BY f.painter
        UNION ALL
        SELECT f.photographer, 'photographer', COUNT(DISTINCT f.image_id),
               CASE WHEN LOWER(f.photographer) LIKE ? THEN 0 ELSE 1 END
        FROM filmography f JOIN images i ON i.id = f.image_id
        WHERE i.user_id = ? AND f.photographer IS NOT NULL AND LOWER(f.photographer) LIKE ?
        GROUP BY f.photographer
        ORDER BY prefix_rank ASC, cnt DESC
        LIMIT 8
    ''', (prefix_like, uid, like, prefix_like, uid, like, prefix_like, uid, like,
          prefix_like, uid, like, prefix_like, uid, like)).fetchall()

    # V15: aspect-ratio matches — "9:16", "2.35", "scope" etc. suggest format
    # buckets. Counting requires a scan of the user's images, so only do it
    # when the query actually looks like a ratio (ar_query_labels is pure
    # string logic and returns [] for normal tag searches).
    ar_results = []
    ar_labels = ar_query_labels(q)
    if ar_labels:
        bucket_counts = {}
        for row in c.execute('SELECT aspect_ratio FROM images WHERE user_id = ?', (uid,)).fetchall():
            label = normalize_ar_label(ar_float_from_str(row['aspect_ratio']))
            bucket_counts[label] = bucket_counts.get(label, 0) + 1
        ar_results = [{
            'type': 'ar',
            'value': label,
            'count': bucket_counts[label]
        } for label in ar_labels if bucket_counts.get(label)]

    # V39: on-set notes — live suggestion, not a list of discrete values like
    # tags/film. There's no fixed vocabulary to suggest FROM (notes are
    # freeform prose), so this checks whether the CURRENT typed text has any
    # match at all and, if so, offers exactly one entry: "run this phrase as
    # a notes search." A prefix MATCH (see _fts5_match_query) so it updates
    # as Ryan keeps typing, same as everything else in this dropdown.
    # Deliberately NOT scoped by active_chips co-occurrence like tag
    # suggestions are — a global per-user count is enough for v1.
    note_results = []
    match_query = _fts5_match_query(q, prefix=True)
    if match_query:
        # FTS5's special MATCH binding only recognizes the table by its real
        # name, not an alias — `n MATCH ?` throws "no such column: n" even
        # though `n` is a valid alias for notes_fts everywhere else in this
        # query (verified directly against sqlite3, not assumed).
        note_count = c.execute('''
            SELECT COUNT(DISTINCT n.rowid) AS cnt
            FROM notes_fts n JOIN images i ON i.id = n.rowid
            WHERE i.user_id = ? AND notes_fts MATCH ?
        ''', (uid, match_query)).fetchone()['cnt']
        if note_count:
            note_results = [{'type': 'note', 'value': q, 'count': note_count}]

    conn.close()

    tag_results = [{
        'type': 'tag',
        'value': row['value'],
        'category': row['category'],
        'catLabel': CAT_LABELS.get(row['category'], row['category']),
        'color': CAT_COLORS.get(row['category'], '#9c988d'),
        'count': row['cnt']
    } for row in rows]

    film_results = [{
        'type': 'film',
        'value': row['value'],
        'field': row['field'],
        'count': row['cnt']
    } for row in film_rows]

    # An exact match (typed "Tenet", there's a film called Tenet) should
    # always sit at the very top regardless of type or how many images carry
    # it — otherwise a popular tag that merely starts with the same letters
    # can bury the one result you actually typed for.
    combined = tag_results + film_results + ar_results + note_results
    combined.sort(key=lambda r: (r['value'].lower() != q, -r['count']))
    return jsonify(combined)

@bp.route('/api/search')
def search():
    page = int(request.args.get('page', 0))
    per = int(request.args.get('per', 50))

    uid = session['user_id']
    conn = get_db()
    c = conn.cursor()

    conditions, params, is_unfiltered = build_search_filters(c, uid, request.args)
    where = 'WHERE ' + ' AND '.join(conditions)

    # V14: shuffled home feed. When the default (unfiltered) grid sends a seed,
    # order by a seeded shuffle instead of newest-first. Any active filter
    # switches back to the normal newest-first ordering.
    #
    # V35: dropped the "seen in the last 7 days sinks to the bottom" bucket.
    # Once most of the library has been viewed recently (Ryan's case: 3496 of
    # 3499 images), that bucket swallows almost everything and only the tiny
    # unseen leftover ever occupies the top of the feed — so the "shuffle"
    # stops looking random, since day to day the same few unseen images keep
    # winning the top slots. A straight seeded shuffle stays fresh regardless
    # of view history.
    seed = request.args.get('seed', '').strip()
    if seed and is_unfiltered:
        order_by = 'shuffle_key(?, images.id)'
        order_params = [seed]
    else:
        order_by = 'date_added DESC'
        order_params = []

    rows = c.execute(f'''
        SELECT id, filename, thumbnail_blob, caption, aspect_ratio, md5_checksum,
               camera_rig, lens, lens_filter, stop, onset_notes, {favorite_col(uid)}
        FROM images {where}
        ORDER BY {order_by} LIMIT ? OFFSET ?
    ''', params + order_params + [per, page * per]).fetchall()
    total = c.execute(f'SELECT COUNT(*) FROM images {where}', params).fetchone()[0]

    images_out = images_common.hydrate_image_rows(c, rows)
    conn.close()

    return jsonify({'images': images_out, 'total': total, 'page': page, 'per': per, 'has_more': (page + 1) * per < total})

@bp.route('/api/search/ids')
def search_ids():
    """Every image id matching the current filter — not just the page the
    browser happens to have scrolled to (V32).

    Select Mode's old "Select all loaded" only ever selected the thumbnails
    already in the grid, so on a 118-result search that had loaded 60 it
    silently grabbed 60 and said nothing. Sending ids instead of forcing the
    grid to fetch every remaining page is what makes "select all" cheap: a
    few kilobytes of numbers versus tens of megabytes of base64 thumbnails.

    Takes exactly the same query params as /api/search and shares
    build_search_filters() with it, so the ids returned here are precisely
    the images on screen — they cannot drift apart. No `seed` handling:
    ordering is irrelevant to a selection, and the shuffle only ever applies
    to the unfiltered grid anyway."""
    uid = session['user_id']
    conn = get_db()
    c = conn.cursor()
    conditions, params, _ = build_search_filters(c, uid, request.args)
    where = 'WHERE ' + ' AND '.join(conditions)
    rows = c.execute(f'SELECT id FROM images {where} ORDER BY date_added DESC', params).fetchall()
    conn.close()
    ids = [r['id'] for r in rows]
    return jsonify({'ids': ids, 'total': len(ids)})

@bp.route('/api/bookmarks', methods=['GET', 'POST'])
def bookmarks():
    user_id = session['user_id']

    if request.method == 'GET':
        conn = get_db()
        c = conn.cursor()
        rows = c.execute('''
            SELECT id, name, chips_json, created_at FROM saved_searches
            WHERE user_id = ? ORDER BY created_at DESC
        ''', (user_id,)).fetchall()
        conn.close()
        out = []
        for r in rows:
            try:
                state = json.loads(r['chips_json'] or '{}')
            except Exception:
                state = {}
            out.append({'id': r['id'], 'name': r['name'], 'state': state, 'created_at': r['created_at']})
        return jsonify(out)

    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    state = data.get('state') or {}
    if not name:
        return jsonify({'error': 'Name required'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO saved_searches (user_id, name, chips_json) VALUES (?, ?, ?)',
              (user_id, name, json.dumps(state)))
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return jsonify({'success': True, 'id': new_id})

@bp.route('/api/bookmarks/<int:bookmark_id>', methods=['DELETE'])
def delete_bookmark(bookmark_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM saved_searches WHERE id = ? AND user_id = ?', (bookmark_id, session['user_id']))
    found = c.rowcount > 0
    conn.commit()
    conn.close()
    if not found:
        return jsonify({'error': 'Bookmark not found'}), 404
    return jsonify({'success': True})

def _cosine_similarity(vec_a, vec_b):
    """Plain-Python cosine similarity between two equal-length float lists.
    Re-normalizes defensively (the seed vectors are already L2-normalized,
    but we don't want to trust that blindly), and guards against a
    zero-magnitude vector blowing up with a divide-by-zero."""
    dot = 0.0
    mag_a = 0.0
    mag_b = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        mag_a += a * a
        mag_b += b * b
    mag_a = mag_a ** 0.5
    mag_b = mag_b ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)

@bp.route('/api/images/<int:image_id>/similar')
def get_similar_images(image_id):
    """Visual + tag similarity for the 'more like this' feature. Combines a
    CLIP embedding cosine similarity (how visually alike two images are) with
    a tag overlap score (how much cinematography vocabulary they share):
    combined = 0.7 * cosine + 0.3 * tag_overlap.
    Requires embeddings_seed.json.gz to have been loaded (see
    load_embeddings_seed) — if the source image has no vector yet, this
    returns 404 rather than guessing."""
    limit = request.args.get('limit', 40, type=int)
    if not limit or limit <= 0:
        limit = 40
    limit = min(limit, 100)

    uid = session['user_id']
    conn = get_db()
    c = conn.cursor()

    c.execute('SELECT filename FROM images WHERE id = ? AND user_id = ?', (image_id, uid))
    source_img = c.fetchone()
    if not source_img:
        conn.close()
        return jsonify({'error': 'Image not found'}), 404

    c.execute('SELECT clip_vector FROM embeddings WHERE image_id = ?', (image_id,))
    source_row = c.fetchone()
    if not source_row or not source_row['clip_vector']:
        conn.close()
        return jsonify({'error': 'no_embedding'}), 404

    source_vec = array('f', source_row['clip_vector']).tolist()

    # All embeddings, joined to the columns build_image_dict() needs — one
    # query, no per-candidate lookups. Scoped to this user's own images.
    candidates = c.execute(f'''
        SELECT e.image_id, e.clip_vector,
               i.id, i.filename, i.thumbnail_blob, i.caption, i.aspect_ratio, i.md5_checksum,
               i.camera_rig, i.lens, i.lens_filter, i.stop, i.onset_notes,
               {favorite_col(uid, alias='i')}
        FROM embeddings e
        JOIN images i ON i.id = e.image_id
        WHERE e.image_id != ? AND e.clip_vector IS NOT NULL AND i.user_id = ?
    ''', (image_id, uid)).fetchall()

    # Tags for the source image plus every candidate, in one query — grouped
    # by image_id in Python instead of one query per candidate. Keep both the
    # full {'category','value'} dicts (for the response, same shape as
    # /api/search) and a plain set of values (for the overlap score).
    all_ids = [image_id] + [row['image_id'] for row in candidates]
    tags_by_image = {}
    tag_values_by_image = {}
    if all_ids:
        ph = ','.join('?' * len(all_ids))
        for tr in c.execute(f'SELECT image_id, category, value FROM tags WHERE image_id IN ({ph})', all_ids).fetchall():
            tags_by_image.setdefault(tr['image_id'], []).append({'category': tr['category'], 'value': tr['value']})
            tag_values_by_image.setdefault(tr['image_id'], set()).add(tr['value'])

    source_tag_values = tag_values_by_image.get(image_id, set())

    scored = []
    for row in candidates:
        cand_vec = array('f', row['clip_vector']).tolist()
        cosine = _cosine_similarity(source_vec, cand_vec)

        cand_tag_values = tag_values_by_image.get(row['image_id'], set())
        if source_tag_values and cand_tag_values:
            overlap = len(source_tag_values & cand_tag_values) / min(len(source_tag_values), len(cand_tag_values))
        else:
            overlap = 0.0

        combined = 0.7 * cosine + 0.3 * overlap
        scored.append((combined, row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[:limit]

    # Build response dicts only for the images we're actually returning —
    # no point base64-encoding thumbnails we're about to throw away.
    top_ids = [row['image_id'] for _, row in top]
    colors_map = {}
    if top_ids:
        ph = ','.join('?' * len(top_ids))
        for cr in c.execute(f'SELECT image_id, hex FROM colors WHERE image_id IN ({ph}) ORDER BY rank ASC', top_ids).fetchall():
            colors_map.setdefault(cr['image_id'], []).append(cr['hex'])
        film_map = {}
        for fr in c.execute(f'SELECT image_id, title, director, dp, year FROM filmography WHERE image_id IN ({ph})', top_ids).fetchall():
            film_map[fr['image_id']] = {
                'title': fr['title'], 'director': fr['director'],
                'dp': fr['dp'], 'year': fr['year']
            }
    else:
        film_map = {}

    conn.close()

    images_out = []
    for combined, row in top:
        img_dict = images_common.build_image_dict(
            row,
            tags_by_image.get(row['image_id'], []),
            colors_map.get(row['image_id'], []),
            film_map.get(row['image_id'])
        )
        img_dict['similarity'] = round(combined, 3)
        images_out.append(img_dict)

    return jsonify({
        'source': {'id': image_id, 'filename': source_img['filename']},
        'images': images_out
    })

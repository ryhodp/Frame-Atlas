"""app.py — Frame Atlas's Flask app: setup, the login gate, route registration, startup.

After Phase 3 (Days 28-43) this file holds no features of its own. Where
everything lives now:

  Shared foundation
    core.py              get_db/db_path, taxonomy maps, tag normalisation,
                         Gemini constants, login gate + admin_required,
                         _scope_ids_to_user, RUNNING_LOCALLY
    schema.py            init_db + every migration, check_schema, embeddings seed

  Pure maths (bytes/numbers in, numbers out — no DB, no Flask)
    colors.py            palette extraction + colour matching
    fingerprint.py       phash + signature duplicate detection
    imaging.py           thumbnails + aspect-ratio maths
    perspective.py       homography solver for perspective crop
    pdf_export.py        PDF lookbook layout

  Workers + shared helpers
    drive.py             Google Drive clients, folder listing, _Removed
    gemini.py            friend Gemini keys (encrypted) + spend tracking
    images_common.py     image-row hydration, palette writer, boot backfills
    tagging.py           Gemini auto-tag worker + SSE progress state
    backup.py            monthly DB snapshot to Drive + scheduler
    crop.py              background crop queue + worker
    sync.py              Drive folder sync, upload/clip ingest, reconcile
    search_filters.py    the ONE query-params -> WHERE-clause builder
    decks_common.py      deck helpers shared by routes_decks/routes_share,
                         plus run_self_test (the boot-time canary check)

  Route blueprints (every URL unchanged from before its move)
    routes_auth.py       Frame Atlas login, register, setup, invite codes
    routes_search.py     search, select-all ids, autocomplete, NL, bookmarks, similar
    routes_tags.py       tag editing (single/bulk/cleanup) + auto-tagger controls
    routes_images.py     view/favourite/film credits/notes/download/delete/crop
    routes_maintenance.py  admin: Duplicate Review, regen thumbs/colours, backups, models
    routes_decks.py      decks, scenes, members, storyboard, owner share controls, PDF
    routes_share.py      the PUBLIC no-login share-link routes — the whole public surface
    routes_sync.py       photos IN: folder sync, /api/folders, upload, clip
    routes_account.py    setup checklist, Gemini key + spend, Google Drive connection
    routes_analytics.py  analytics dashboard, per-user rollup, utility views, view log

  Still here: the app object + its config, the login gate's registration,
  /api/health + /api/config (they describe the app itself), the React
  catch-all, and startup.

Blueprint rule: none of the files above may import app.py (circular import).
Day-by-day history of every move: CLAUDE.md and docs/3_Session_Log.md.
"""
import os
import base64
import threading
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image

# ── Names re-exported for the test scripts ───────────────────────────────────
# ~30 scripts/test_*_locally.py read these as `mod.<name>` off this module
# (mod.color_matches, mod.PALETTE_DARK_V, mod.Image, mod.base64, mod.tagging,
# mod.decks_common …). Most are unused *in this file* and a linter will say
# so. Do not delete them on that basis: dropping one breaks a test script.
from colors import (
    PALETTE_DARK_V, PALETTE_GRAY_S, PALETTE_MERGE_HUE_TOL, PALETTE_VERSION,
    EXACTNESS_LOOSE_DEG, EXACTNESS_TIGHT_DEG, EXACTNESS_LOOSE_VAL, EXACTNESS_TIGHT_VAL,
    DEFAULT_EXACTNESS, DEFAULT_PROMINENCE,
    DUP_COLOR_HUE_TOL, DUP_COLOR_MIN_OVERLAP,
    _is_shadow_or_gray, _hsv, _chromatic_entries,
    extract_palette, hex_to_rgb, color_distance,
    exactness_to_hue_tol, exactness_to_value_tol,
    color_matches, color_match_share, palettes_overlap,
)
from fingerprint import (
    PHASH_GRID, PHASH_BITS, PHASH_HEX_LEN, PHASH_NEAR_DUP_THRESHOLD,
    SIGNATURE_GRID, SIGNATURE_MAX_DISTANCE,
    compute_phash, phash_distance,
    compute_signature, signature_distance, signatures_match,
)
from imaging import (
    STANDARD_ASPECT_RATIOS, AR_QUERY_ALIASES,
    generate_thumbnail, get_image_aspect_ratio,
    normalize_ar_label, ar_float_from_str, ar_query_labels,
)
from perspective import (
    PERSPECTIVE_MIN_AREA_PCT, PERSPECTIVE_MIN_CORNER_GAP_PCT,
    _quad_signed_area, parse_perspective_corners, perspective_is_whole_image,
    solve_linear_system, solve_perspective_coeffs,
    perspective_output_size, perspective_correct,
)
from core import (
    GEMINI_MODEL, GEMINI_PRICING, DEFAULT_GEMINI_PRICING, get_model_pricing,
    CAT_COLORS, CAT_LABELS, MANUAL_TAG_CATEGORIES,
    SQL_PARAM_CHUNK, chunked,
    TAG_PLURAL_STRIP_EXCEPTIONS, normalize_tag_value, clear_ai_tags,
    _shuffle_key, get_db, db_path, favorite_col,
    RUNNING_LOCALLY, admin_required, current_user_id, check_login_required,
    _scope_ids_to_user,
)
from schema import (
    _is_duplicate_column_error, EXPECTED_COLUMNS, missing_columns,
    check_schema, init_db, load_embeddings_seed,
)

# ── Worker / helper modules (tests patch fakes onto these: mod.drive.…) ──────
import drive
import gemini
import tagging
import images_common
import backup
import crop
import sync
from decks_common import run_self_test
import decks_common  # reached as mod.decks_common by test_self_test_locally.py

# ── Route blueprints ─────────────────────────────────────────────────────────
import routes_auth
import routes_search
import routes_tags
import routes_images
import routes_maintenance
import routes_decks
import routes_share
import routes_sync
import routes_account
import routes_analytics


app = Flask(__name__, static_folder='static', static_url_path='/static')
# Railway's proxy terminates HTTPS in front of us; without this, Flask thinks
# every request arrived over plain http and builds http:// URLs (which breaks
# the Google OAuth redirect_uri).
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
CORS(app)
# Signs the login session cookie. MUST be a fixed value set via the
# FLASK_SECRET_KEY Railway env var — falling back to a random one means every
# redeploy invalidates every logged-in session (everyone gets logged out on
# every push). The random fallback only exists so local dev works with zero
# setup.
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or os.urandom(24)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Day 28 security hardening.
#  - HTTPONLY: JavaScript can never read the session cookie (blunts an XSS
#    that would otherwise hand an attacker a live session).
#  - SECURE: the cookie only rides on HTTPS. Turned OFF for local runs
#    (FA_DB_PATH set — the same "am I local, not production" signal the test
#    harness already uses) because the Flask test client and http://localhost
#    dev talk plain HTTP, and a Secure cookie there is silently dropped, which
#    would break every login-gated test and local sign-in.
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = not RUNNING_LOCALLY

# Snapshot of the active DB path for the handful of test scripts that read
# mod.DB_PATH directly. The live source of truth is core.db_path().
DB_PATH = db_path()

app.register_blueprint(routes_auth.bp)
app.register_blueprint(routes_search.bp)
app.register_blueprint(routes_tags.bp)
app.register_blueprint(routes_images.bp)
app.register_blueprint(routes_maintenance.bp)
app.register_blueprint(routes_decks.bp)
app.register_blueprint(routes_share.bp)
app.register_blueprint(routes_sync.bp)
app.register_blueprint(routes_account.bp)
app.register_blueprint(routes_analytics.bp)


# The whole app is login-gated except core.PUBLIC_API_ROUTES and the public
# /api/share/* links. The logic is core.check_login_required(); only its
# REGISTRATION lives here, because @app.before_request needs the `app` object.
# It's path-based, so it covers every blueprint's routes identically.
@app.before_request
def require_login():
    return check_login_required(app)


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

@app.route('/api/config', methods=['GET'])
def config():
    return jsonify({
        'app_name': 'Frame Atlas', 'version': 'V17', 'gemini_model': GEMINI_MODEL,
        # Both safe to expose to any logged-in browser: the OAuth client id
        # is meant to be public (only the client SECRET is sensitive, and
        # that never leaves the server), and the Picker key is restricted
        # server-side (Google Cloud Console) to the Picker API only.
        'google_client_id': os.environ.get('GOOGLE_OAUTH_CLIENT_ID'),
        'google_picker_api_key': os.environ.get('GOOGLE_PICKER_API_KEY'),
    })

# React app shell: any non-API path serves the built frontend (or a real file
# from static/ when one exists). Registered last so it never shadows a route.
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path.startswith('api/'):
        from flask import abort
        abort(404)
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    full_path = os.path.join(static_dir, path)
    if path and os.path.exists(full_path):
        return send_from_directory(static_dir, path)
    return send_from_directory(static_dir, 'index.html')

# ============================================================================
# STARTUP
# ============================================================================
# ONE copy of the boot sequence (Day 43). Railway runs `python app.py`, and
# every test imports this file — both run exactly these lines, in this order.
# Until Day 43 they were written out twice (an `if __name__ == '__main__':`
# copy for Railway, a module-level copy for imports), and every change had to
# be made in both.
crop.start_crop_worker()
init_db(run_self_test=run_self_test)
load_embeddings_seed()
images_common.backfill_palettes()
images_common.backfill_phashes()
images_common.backfill_notes_fts()
images_common.merge_plural_tag_duplicates()
threading.Thread(target=sync.reconcile_drive_changes, daemon=True).start()
backup.start_backup_scheduler()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

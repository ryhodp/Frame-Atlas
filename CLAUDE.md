# CLAUDE.md — Frame Atlas
*This file is automatically read by Claude Code at the start of every session.*

---

## About the Developer

Ryan is a cinematographer/gaffer (Director of Photography). He has **no coding background** — explain everything in plain language, like you're talking to a smart high schooler who has never written code. Avoid jargon. When a technical term is unavoidable, explain it in one sentence using an analogy. Never assume Ryan knows what something means just because it came up before.

---

## Session Initialization Protocol

When Ryan says **"I'm ready for Day X"**, do this before anything else:

1. Read `/docs/2_Frame_Atlas_Build_Timeline.md` — find Day X and understand what's planned
2. Read `/docs/3_Session_Log.md` — find the last entry and understand where we left off
3. Read `/docs/1_Frame_Atlas_PRD.md` — refresh on the overall product vision if needed
4. **Respond with a 2–3 sentence summary** of where we left off and what the immediate first task is
5. **Do not write any code yet** — wait for Ryan to confirm the summary before proceeding

---

## Pre-Coding Rule (Always)

Before writing or modifying any code, pause and think through the task. Present **3–4 multiple choice questions (A, B, C, D)** that surface hidden tradeoffs, edge cases, or design decisions Ryan might not have considered. Wait for his answers before generating any code.

This applies to every feature, no matter how small. The goal is to avoid building the wrong thing.

---

## How to Deliver Code Changes

- **Always deliver complete file replacements** — never partial edits, snippets, or "find this line and change it" instructions. Ryan cannot reliably apply partial edits.
- Every file Claude writes should be the entire file, ready to paste or commit as-is.
- When multiple files change, deliver them one at a time with a clear label for each.

---

## Version Naming Convention

Every major iteration or structural change to the codebase must be labeled:
**Frame Atlas V1, V2, V3**, and so on. Track this incrementally.

---

## End of Session Protocol

When Ryan says **"End chat"**:

1. Update `/docs/3_Session_Log.md` by appending a new entry at the bottom
2. Include: what was built, decisions made, any new technical debt, and the exact starting point for next session
3. **Never overwrite or truncate previous log entries** — only append

---

## Project Overview

**Frame Atlas** is a self-hosted visual reference library for cinematographers. It turns a Google Drive folder of inspiration images into a searchable, AI-tagged tool.

- **Live URL:** `https://frame-atlas-production.up.railway.app`
- **GitHub repo:** `frame-atlas`
- **Deployment platform:** Railway (project: "daring-light," service: "Frame-Atlas")
- **Auto-deploy:** Every push to GitHub triggers a Railway redeploy (~2–3 min for code changes, 3+ min if `requirements.txt` changes)

---

## Tech Stack (Plain English)

| Piece | What it is | Plain English |
|---|---|---|
| Flask | Python backend | The server that handles data and talks to the database |
| React + Vite | Frontend | The visual interface Ryan sees in the browser |
| Tailwind CSS | Styling | Pre-built design classes so we don't write raw CSS |
| SQLite | Database | A single file that stores all images, tags, and metadata |
| Google Drive API | Image source | Pulls images from Ryan's Drive folder |
| Gemini AI | Auto-tagging | Reads each image and writes cinematography tags |
| Railway | Hosting | Keeps the app running on the internet 24/7 |

---

## File Structure

```
frame-atlas/
├── backend/
│   ├── app.py              # Endpoints, tagging, crop worker, sync (Phase 3 splits this)
│   ├── core.py             # get_db/db_path, tag normalisation, taxonomy maps, Gemini constants (V70)
│   ├── schema.py           # init_db + all migrations + check_schema + load_embeddings_seed (V70)
│   ├── drive.py            # Google Drive: service/OAuth clients, folder listing, _Removed, download (V71)
│   ├── gemini.py           # Friend Gemini key encryption (Fernet) + per-user spend tracking (V72)
│   ├── colors.py           # Palette extraction + colour matching (V45)
│   ├── fingerprint.py      # phash + signature, duplicate detection (V45)
│   ├── imaging.py          # Thumbnails + aspect-ratio maths (V45)
│   ├── perspective.py      # Homography solver for perspective crop (V45)
│   └── pdf_export.py       # PDF lookbook layout (V40)
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── Home.jsx    # Main image grid + search
│       │   └── Sync.jsx    # Sync manager UI
│       └── components/     # Reusable UI pieces
├── docs/                   # Planning documents (read at session start)
├── CLAUDE.md               # This file
└── DESIGN.md               # Visual design system
```

---

## Critical Technical Facts

These are hard-won lessons from debugging. Don't second-guess them.

**Database**
- SQLite lives at `/app/data/library.db` on Railway's persistent volume
- Volume is mounted at `/app/data` — NOT `/app` (mounting at `/app` would wipe the compiled frontend)
- **`backend/library.db` was tracked by git until V44/Day 26**, now untracked and gitignored (`*.db`/`*.db-journal`/`*.db-wal`/`*.db-shm`). Checked before removing it: 0 bytes in its only commit, so no real library data was ever actually in git history — but a real one would have put every photo's tags and every user row into git permanently, which is why this needed fixing even though it happened to be harmless in practice
- **Automatic monthly backup (V27), rewritten to run entirely in RAM (V35).** `run_db_backup()` used to write a full temporary copy of the database to `/app/data` before uploading it — but that volume is only 434MB total, so there's rarely room for both the live DB and a temp copy at once (the DB itself was 283MB). A copy dying partway left the scratch file behind, silently eating whatever space was left — this is what crashed the app on July 31, 2026, and was very likely also breaking Select Mode's bulk deletes around the same time (a delete's own DB write needs a little headroom too). Now uses Python 3.11's `Connection.serialize()` — the backup is built via SQLite's own `backup()` API into an in-memory connection, then pulled out as bytes. Nothing touches disk that needs cleaning up
- **Indexes (V43/Day 25).** There were zero anywhere in the database before this — every search, autocomplete keystroke, and tag lookup was a full table scan. Invisible at a few thousand images but grows in a straight line, and autocomplete fires on every keystroke. `init_db()` now runs 5 `CREATE INDEX IF NOT EXISTS` statements on every boot (idempotent, so no self-disabling flag needed like the phash/palette backfills): `images(user_id)`, `tags(image_id)`, `tags(value, image_id)` (the AND-filter subquery every search/select-all/removal-preview runs), `tags(user_id, value)` (autocomplete), `tags(category, value)` (removal preview / bulk-remove)

**Backend module split (V45/Day 27)**
- The pure maths left `app.py` for four flat files beside it — `colors.py`, `fingerprint.py`, `imaging.py`, `perspective.py`. "Pure" is the load-bearing word: bytes/strings/numbers in, numbers out, nothing touching the database, Drive, Flask or the session. 7,337 → 6,626 lines
- **Only pure code could move at first, and this is why.** At the time of the Part 1 split, all `scripts/test_*_locally.py` copied `app.py` — one single file — into a temp directory, string-patched `DB_PATH` inside it, then imported that copy with `backend/` on `sys.path`. Move DB-touching code out and the tests would have kept running while the moved module resolved from the real `backend/` carrying the production `/app/data/library.db` path. Pure modules have no `DB_PATH`, so nothing in the harness needed to change for Part 1.
- **Part 2 (shipped V45 part 2, Aug 26 2026): the harness itself was reworked, unblocking further extraction.** `DB_PATH` in `app.py` is now `os.environ.get('FA_DB_PATH', '/app/data/library.db')` — unset in production, so Railway needed zero config change. All 36 test scripts that touch the database (one more, `test_pdf_export_locally.py`, never did) dropped the copy-into-a-tempdir-and-patch step entirely: each sets `FA_DB_PATH` to its own throwaway path, then loads `backend/app.py` directly via `importlib.util.spec_from_file_location` with a unique module name per load. The three scripts that boot more than one independent app instance per run (`test_schema_guard_locally.py`, `test_self_test_locally.py`, `test_security_hardening_locally.py`) already used a unique spec name per load for this exact reason, so repeated loads of the same real file with a fresh env var each time behave identically to the old repeated loads of distinct temp copies. **Caught before running anything:** the mechanical regex transform applied across all 36 files nearly broke `test_security_hardening_locally.py` — that script built its patched-file path from a variable rather than inline, so the transform rewrote the variable's target to the real `backend/app.py` while leaving the line that WRITES to that path intact, which would have overwritten the live source file the first time the script ran. Fixed by hand before any test executed. Drive, sync, tagging and the crop worker are still in `app.py` — this only removed the blocker, extraction itself is separate future work
- `app.py` imports the names back in rather than referencing modules, so its public surface is unchanged: the test scripts read `mod.color_matches` / `mod.PALETTE_DARK_V` / `mod._hsv` off it directly. Several of those imports are unused *within* `app.py` and a linter flags them — **do not delete them**, they are the module's API and dropping one breaks a test script silently. Verified before the split that no script *reassigns* anything in the moved set (only `get_drive_service`, `get_user_drive_service`, `get_root_folder_id`, `trigger_tagging`, `MediaIoBase*`, `ImageDraw`, `PERSONAL_LIBRARY_CAP` are ever monkeypatched — all stayed put)
- `save_palette()` and `backfill_palettes()` stayed in `app.py` on purpose despite being colour code: they open DB connections
- `Dockerfile` does `COPY backend/ ./` and there is no `.dockerignore`, so new files there ship automatically — but check that if it ever changes

**Backend module split — Phase 3 (Day 28+, one module per session)**
- **Two standing rules:** (1) new modules import from `core.py` and the V45 pure modules, **never from `app.py`** (that would be a circular import); (2) call sites in `app.py` get qualified as code moves, and the ~test scripts that monkeypatch a moved name get repointed — applied as a scripted transform, **eyeballed in the diff before running** (V45 part 2's near-miss is why).
- **Day 28 (V70): `core.py` + `schema.py`.** `core.py` = `get_db()`, `db_path()` (see below), `_shuffle_key()`, `chunked()`/`SQL_PARAM_CHUNK`, `normalize_tag_value()`/`clear_ai_tags()`/`TAG_PLURAL_STRIP_EXCEPTIONS`/`MANUAL_TAG_CATEGORIES`, `CAT_COLORS`/`CAT_LABELS`, `GEMINI_MODEL`/`GEMINI_PRICING`/`get_model_pricing()` — stdlib imports only. `schema.py` = `_is_duplicate_column_error()`, `EXPECTED_COLUMNS`, `missing_columns()`, `check_schema()`, `init_db()`, `load_embeddings_seed()`.
- **`db_path()` replaced the module-level `DB_PATH` string.** `core.db_path()` reads `FA_DB_PATH` **live on every call** instead of snapshotting it at import. This matters because `core`/`schema` are cached in `sys.modules` across the multiple app-boots that `test_schema_guard_locally.py` / `test_self_test_locally.py` / `test_security_hardening_locally.py` do in one process — a snapshot would freeze the first boot's path. `app.py` still exposes `DB_PATH = db_path()` at module scope (re-evaluated per app.py load) because 5 test scripts read `mod.DB_PATH` directly.
- **`run_self_test()` stayed in `app.py`.** It calls `_deck_access()`/`touch_deck()`, which are deck code that stays in `app.py`, and `schema.py` may not import `app.py`. So `init_db()` took one parameter — `init_db(run_self_test=None)` — and `app.py` passes it at both boot sites. `test_self_test_locally.py` still monkeypatches `mod._deck_access` and it still works, because `run_self_test` resolves that name in `app.py`'s own globals.
- `app.py` imports every moved name straight back (`from core import …` / `from schema import …`), same as V45 — so `mod.<name>` keeps resolving and **no test script needed repointing for the move itself**. The one edit was `test_schema_guard_locally.py`, which greps source text for `ALTER TABLE` (now in `schema.py` not `app.py`).
- Every migration in `init_db()` is byte-for-byte the original — verified by diffing the whole function body against pre-change `main`. Only additions: the two `run_self_test` lines and the `rate_limit_hits` table (below).
- **Day 29 (V71): `drive.py`.** The whole Google Drive layer: `get_drive_service()`, `get_user_drive_service()`, `get_user_credentials()`, `get_oauth_flow()`, `get_service_account_email()`, `parse_drive_folder_id()`, `list_images_in_folder()`, `get_root_folder_id()`, `get_or_create_removed_folder()`, `download_drive_file()`, `drive_error_reason()` + constants `REMOVED_FOLDER_NAME`, `PERSONAL_LIBRARY_CAP`, `UPLOAD_SCOPES`. Every function body character-for-character the original. Imports `get_db` from `core` + the Google client libs (`Credentials`, `UserCredentials`, `Flow`, `Request`, `RefreshError`, `build`, `HttpError`, `MediaIoBaseDownload`) — those import lines left `app.py`.
- **This cut followed rule 2 (qualify + repoint), NOT Day 28's re-export.** `app.py` does `import drive` and every call site is qualified (`drive.get_drive_service()`, `drive.PERSONAL_LIBRARY_CAP`, …). The 11 test scripts that monkeypatch a moved name were repointed `mod.<name>` → `mod.drive.<name>` as a scripted transform. `mod.drive` works because `app.py` does `import drive`, so the module object is reachable — tests need no extra import.
- **`MediaIoBaseDownload` is imported in BOTH `app.py` and `drive.py`.** `download_drive_file()` moved (so `drive.py` imports it), but `sync_folder_worker()`, `get_full_image()`, `regenerate_thumbnails()`, `download_image()` still use it directly in `app.py`. `MediaIoBaseUpload` did not move at all. The 3 test scripts that fake the crop/reconcile download path (`test_crop_queue`, `test_perspective_crop`, `run_local_for_browser_check`) now patch **`mod.drive.MediaIoBaseDownload`** in addition to `mod.MediaIoBaseDownload` — the crop worker's download resolves the name in `drive.py`'s namespace now. `test_sync_delete_parity` / `test_personal_library` only exercise `sync_folder_worker` (app.py's own `MediaIoBaseDownload`), so they were not touched on that point.
- **`test_oauth_token_refresh_locally.py` also patched `mod.UserCredentials.refresh`** (the Google class, not a project function) — repointed to `mod.drive.UserCredentials.refresh`. It was the only script referencing a moved *Google library* name.
- `run_db_backup()` / `get_or_create_backups_folder()` / `BACKUP_FOLDER_NAME` / `KEEP_BACKUP_COUNT` deliberately stayed in `app.py` for Day 33 (`backup.py`) — their Drive calls are qualified now (`drive.get_user_drive_service(1)` etc.). `DRIVE_RATE_LIMIT_REASONS` also stayed (it's `bulk_delete_images`'s retry policy, not Drive plumbing).
- `app.py`: **6,136 → 5,944 lines** (−192). `drive.py` is 239 lines.

**Backend module split — Phase 3, Day 30 (V72): `gemini.py`**
- The friend-Gemini-key layer moved out of `app.py` into `backend/gemini.py` (148 lines): `_fernet()`, `encrypt_secret()`, `decrypt_secret()`, `set_user_gemini_key()`, `get_user_gemini_key()`, `record_gemini_usage()` + `ENCRYPTED_PREFIX`. Every function body character-for-character the original (diffed against `HEAD`). Imports `get_db`/`get_model_pricing`/`GEMINI_MODEL` from `core` + `from datetime import datetime`; `Fernet` is still imported lazily *inside* `_fernet()` (so there's no top-level `cryptography` import anywhere — a missing package can't break boot).
- **Followed rule 2 (qualify + repoint), same as Day 29.** `app.py` does `import gemini`; all 9 call sites qualified (`gemini.get_user_gemini_key()`, `gemini.record_gemini_usage()`, `gemini.set_user_gemini_key()`, `gemini.decrypt_secret()`). The 2 `record_gemini_usage` call sites live in the tagging worker / `/api/interpret`, both of which stay in `app.py` until Day 32 — qualified now regardless.
- **Only 2 test scripts touch these names, both hand-edited (no scripted transform):** `test_gemini_keys_locally.py` (`mod.get_user_gemini_key`→`mod.gemini.…`, `mod.record_gemini_usage`→`mod.gemini.…`) and `test_security_hardening_locally.py` (`mod.ENCRYPTED_PREFIX` / `mod.get_user_gemini_key` / `mod.decrypt_secret` / `mod.encrypt_secret` → `mod.gemini.…`). Hand-edited per the standing CLAUDE.md rule — `test_security_hardening_locally.py` is the file V45 part 2's scripted transform nearly overwrote. `mod.gemini` is reachable because `app.py` does `import gemini`.
- **Module name `gemini.py` does NOT collide** with the Google SDK — that's imported as `from google import genai as genai_client` (submodule `google.genai`, never top-level `gemini`).
- New `scripts/test_gemini_locally.py` (25 checks — split wiring, no leaked names on `app.py`, encrypt/decrypt round-trip with a key, wrong-key→None, no-key plaintext fallback, set/get through a real DB + admin env key, `record_gemini_usage` month accumulation).
- `app.py`: **5,944 → 5,828 lines** (−116). `core.py` line 33 still has a stale prose mention of `get_user_gemini_key` in a comment — harmless, left alone.

**CI (V43/Day 25)**
- `.github/workflows/tests.yml` runs on every push/PR: every `scripts/test_*_locally.py` script (Python 3.11, matching Railway's deploy image) plus the pure-logic `.mjs` tests (Node) — **40 Python + 3 `.mjs` as of V72/Day 30** (`test_gemini_locally.py` added Day 30). Every script builds its own throwaway synthetic database, pointed at via `FA_DB_PATH` (V45 part 2), so this needs no fixtures or secrets checked in
- `test_shuffle_locally.py` was skipped with an explained `::warning::` originally — the shuffle skip was removed before CI even shipped, per the workflow file's own comment (the "V45p2 flagged stale" note); the script currently passes and runs like the rest

**Server**
- App runs on port `8080`
- Railway domain is pointed at port `8080` in Settings → Networking
- **`Dockerfile` sets `ENV PYTHONUNBUFFERED=1` (V44/Day 26).** Without it, Python block-buffers stdout whenever it isn't attached to a real terminal — which is always true in a container — so every `print()` after boot (`[tagging]`, `[auth]`, `[crypto]`, `[drive]`, `[migration]` …) sat in a buffer and was **lost outright** if the process restarted or crashed before the buffer filled. Confirmed directly: with this unset, a whole local session's worth of activity produced zero log lines beyond startup. This is very likely part of why the V27 crop failure stayed invisible for weeks, and it made the V44 except:pass audit below pointless on arrival — logging what an error handler discards doesn't help if the log line never reaches Railway. Startup-time `print()`s and werkzeug's own request-log lines were unaffected (different code path); this only ever hid output from `app.py`'s own logging after boot
- **`except: pass` audit (V44/Day 26).** The build timeline's own count of "13" was wrong — the real number was **16**. 13 are `init_db()`'s column-migration blocks, silent on purpose for the routine case (column already exists on every boot after the first); `_is_duplicate_column_error()` now tells that case apart from a genuinely unexpected one (disk full, locked DB) so only the unexpected case logs, keeping normal boots quiet. 3 were outside `init_db()` and genuinely silent: the tagging worker's failure to even mark an image `tagging_status='failed'`, and Drive's error-reason JSON parse — both now log. The 16th (aspect-ratio query parsing, `ar_query_labels()`) was deliberately left silent: verified unreachable (the regex only ever captures digit strings, and the zero-guard runs before the division), and it runs on every autocomplete keystroke — logging it would be pure noise for something that can't happen

**Google Drive**
- Service account email must be explicitly shared on the Drive folder (Share → paste email → Viewer)
- `list_drive_folders()` searches the service account's own Drive root — we hardcode the folder ID instead
- Ryan's Drive folder ID: `1LHPVyo3QjOEcizc1Io2UVjxzX4FQ7yDG`
- **Personal libraries (V17): ALL sync goes through the service account.** Friends share their folder with the robot email and paste the folder link (`/api/sync/connect-folder`). Do NOT try to sync via a user's OAuth token — it's `drive.file`-scoped, and picking a folder in the Google Picker grants access to the folder itself, NOT the files inside it; the old OAuth+Picker sync path (Day 14 Stage 2a) could never see pre-existing images and was removed. User OAuth remains only for the Upload button (creates files, which `drive.file` allows)
- Non-admin libraries: 1,000-image soft cap (`PERSONAL_LIBRARY_CAP`); friend deletes are DB-only (no Drive move — Viewer share) and recorded in `sync_exclusions` so the next sync doesn't re-import them

**Gemini AI**
- Use `google-genai==1.16.0` — NOT `google-generativeai==0.3.0` (that one hits a broken old endpoint)
- Re-tagging an image wipes only AI-written tags (`clear_ai_tags()`); manual categories in `MANUAL_TAG_CATEGORIES` (`my_work`, `misc`) always survive. `my_work` (V15) is Ryan's own-projects category (gaffed / DP'd / photographed) — human-applied only, never in the Gemini prompt
- **Friends' keys are encrypted at rest (V44/Day 26).** `users.gemini_api_key` used to be plain readable text — a leaked `library.db` (the monthly Drive backup, any local copy) meant usable keys billing to a friend's own Google account. Now Fernet-encrypted (`cryptography` package) with the cipher key in its own Railway var, `FA_ENCRYPTION_KEY` — deliberately NOT derived from `FLASK_SECRET_KEY`, since one secret protecting two unrelated things means rotating either one for its own reason silently breaks the other. Stored values carry an `enc:v1:` prefix so encrypted and legacy-plaintext rows are always distinguishable — no migration pass: a plaintext key just reads as-is and re-encrypts the next time it's saved. `encrypt_secret()`/`decrypt_secret()`/`set_user_gemini_key()`/`get_user_gemini_key()` are the only functions allowed to touch the column directly (moved to `backend/gemini.py` in V72/Day 30 — call sites in `app.py` are qualified as `gemini.…`). Missing `FA_ENCRYPTION_KEY` degrades to plaintext storage (logs a warning, never crashes); a wrong or corrupted key decrypts to `None`, never to raw ciphertext — which would otherwise get sent to Google as an API key and fail in a confusing way

**Environment Variables on Railway**
- To confirm a variable is actually set, use the Railway Console tab and run: `echo $VARIABLE_NAME`
- JSON credentials must be single-line when pasting. Generate with: `cat key.json | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)))"`
- Never paste variables with surrounding quotes — they break silently
- `FLASK_SECRET_KEY` MUST be set (Day 14) — it signs login session cookies. Without it, Flask falls back to a random value generated fresh on every boot, which logs everyone out on every single deploy.

**Auth (Day 14 / V13)**
- Whole app is login-gated (`before_request` in app.py) except `/api/health`, `/api/auth/login`, `/api/auth/register`, `/api/auth/me`, `/api/setup`, `/api/setup/status`, and public `/api/share/<token>` links
- First deploy after Day 14: visiting the site shows a one-time Setup screen (sets the admin password + email) — self-disables permanently once used
- Invite-only signup: admin generates single-use invite codes (Invite nav link), friends register with one at `/register?code=...`
- Roles: `admin` (Ryan) vs `user` (friends). Admin-only routes use the `@admin_required` decorator — sync, upload, tagging, bulk tag edit, filmography edit, delete, thumbnail/color regen, duplicate scan
- Per-user data: decks, scenes, bookmarks, favorites (`user_favorites` table), flags (`user_flags` table) are scoped to whoever's logged in. Old `is_favorite`/`is_flagged` columns on `images` are legacy/unused, kept only for the one-time migration backfill
- Personal libraries SHIPPED (V17): every user's images are fully isolated (`images.user_id`). Friends connect their own Drive folder (share with robot email + paste link) and optionally their own Gemini key (V16). New friends see a 3-step setup checklist on the empty Home grid. Image delete is now owner-or-admin (no longer admin-only)
- **Login throttling (V44/Day 26).** There was no limit at all before this — password hashing (werkzeug pbkdf2) protects a *stolen* database, it does nothing about guessing against a *live* login form. `users.failed_login_count` / `login_locked_until` track consecutive failures **per account, never per IP** — deliberate, since an attacker can rotate IPs for free but this app is routinely used from shared networks (V43's own notes cite hotel/set wifi), where IP-based throttling would let one guesser lock out everyone else on that connection. `LOGIN_LOCK_THRESHOLD` (5) failures are free; each one past that doubles the lockout (`LOGIN_LOCK_BASE_SECONDS`=30) up to `LOGIN_LOCK_MAX_SECONDS` (1 hour) — so an account is throttled hard but never bricked outright with no self-service recovery. The lock is checked **before** the password hash — a locked account rejects even the *correct* password, otherwise the throttle would still leak "that one was right" one guess at a time. A successful login resets the counter to 0. Login endpoint now returns `429` (not `401`) with `{locked: true, retry_after_seconds}` when throttled; `LoginPage.jsx` needed no changes since it already renders any non-OK response's `error` string
- **Session cookie flags (V70/Day 28).** `SESSION_COOKIE_HTTPONLY = True` (JS can never read the session cookie) and `SESSION_COOKIE_SECURE = not RUNNING_LOCALLY` (HTTPS-only in production). `RUNNING_LOCALLY = bool(os.environ.get('FA_DB_PATH'))` — Secure is deliberately OFF for local runs because the Flask test client and `http://localhost` dev talk plain HTTP and silently drop a Secure cookie, which would break every login-gated test. `SESSION_COOKIE_SAMESITE` stays `'Lax'` (unchanged since Day 14).
- **Rate limiting on the public auth endpoints (V70/Day 28).** `/api/auth/register` and `/api/auth/forgot-password` have no account to key a throttle on, so they get an **IP** limit: `RATE_LIMIT_MAX` (5) hits per `RATE_LIMIT_WINDOW_SECONDS` (60) → `429`. Hand-rolled (Ryan's call over adding Flask-Limiter — no dependency, no +3 min deploys), same spirit as the V44 login lockout: a `rate_limit_hits(scope, client_ip, hit_at)` table (built in `schema.py`), `_rate_limited(scope)` in `app.py`. IP comes from the **last** `X-Forwarded-For` entry (`_client_ip()`) — the one Railway's proxy appends; earlier entries are client-spoofable. **Fails open** on any DB error (a limiter that 500s the endpoint it guards is worse than the abuse) and **no-ops entirely when `RUNNING_LOCALLY`**, so tests and local dev aren't throttled. Old rows are pruned opportunistically on each check.
- **`/api/auth/forgot-password` is an ADMIN-ONLY recovery path for now.** It still returns the reset token in its JSON response — no email is sent yet. The token is 256-bit, one-time-use, 1-hour expiry, but a token in an HTTP response is a token anyone on the wire or in a log can use. Wire up email delivery (Flask-Mail / Mailgun) before pointing friends at it. Flagged in the Day 28 plan; deferred deliberately.

**Thumbnails**
- Stored as raw JPEG bytes in SQLite (`images.thumbnail_blob`) — no separate thumbnail folder
- Target spec: **800px wide, Pillow quality 85** — `generate_thumbnail(image_data, width=800, quality=85)`. (This line used to say 600px/q75; corrected against the code in V40, when the PDF exporter's output quality depended on knowing the real number. Never upscales — a source narrower than the target stays native)
- **`generate_thumbnail()` must apply EXIF orientation before resizing (V36).** It wasn't, so a phone photo shot sideways got re-saved with the sideways pixels baked in and the rotation tag dropped — the full-res view still looked correct because it streams the untouched Drive original straight through, tag intact, so this bug was invisible there and only showed up in the grid/thumbnail. Same fix the crop path already had; if a future image-processing path resaves pixels, it needs this too
- **Cacheable thumbnail URLs (V43/Day 25).** Base64-in-JSON can never be cached by the browser — there's no URL to remember it by. Measured at real library size: ~6.5MB re-transferred per page of 60, every single visit (2.6s on good 4G, 10.4s on hotel/set wifi, ~26s on weak LTE). `build_image_dict()` now returns `thumbnail: "/api/images/<id>/thumb?v=<md5_checksum>"` instead of a data URI for every authenticated context (search, decks owner/member view, similar-images, favorites/flagged/recent). `GET /api/images/<id>/thumb` serves the raw bytes with `Cache-Control: private, max-age=31536000, immutable` — same owner-or-admin permission check as crop/delete/notes. The `?v=` is never read server-side; it exists purely so a crop (which rewrites `md5_checksum`) changes the URL and forces a fresh fetch, while an unrelated re-tag leaves both untouched
- **The public `/api/share/<token>` view is the one deliberate exception** — it still embeds base64, via `build_image_dict(..., public=True)` threaded through `_fetch_image_dict()` and `_deck_payload()`. There is no login on that route to gate a cacheable URL behind, and it was already the smallest-volume consumer (share links are single-deck, not library-wide grids)
- `frontend/public/sw.js` carves out exactly one exception to its "never cache /api" rule (see Offline support below) for `/api/images/<id>/thumb` — cache-first, same reasoning as its existing `/assets/` branch: the URL is content-addressed, so a cache hit is never stale. This is also what keeps offline deck viewing working now that deck payloads carry URLs instead of embedded bytes — `useOfflineCache.js` still saves the deck JSON verbatim; the SW is what makes the pictures those URLs point to actually available with no connection
- No frontend `.jsx` file needed to change: every consumer already just does `<img src={img.thumbnail}>` or a truthiness check, and a URL string works exactly like a data URI there. Verified via `scripts/test_thumbnail_caching_locally.py` (9 checks) and live in a real browser: a repeat page load fired zero `/thumb` network requests at all (served straight from the browser's own disk cache)

**Colour (V24 knobs; V33 corrected both of them)**
- `colors.share` = fraction of the frame that colour covers. `extract_palette()` always computed it; before V24 it was discarded. Shades merged during dedupe donate their share to the colour that absorbed them, so a palette entry means "this colour family", not one bin
- Palette rank is **vibrance**-ordered on purpose (a small vivid patch outranks a big dull one), which is why coverage — not rank — is what filters out specks of colour
- Hue angle, not `color_distance()`, decides colour closeness. The weighted-RGB metric is green-heavy and rates brown (461) a closer match to red than pink (1633). Always guard on saturation when comparing hues: grey reports hue 0.0, identical to pure red
- **A family may only absorb a bin that belongs in it (V33).** HSV saturation is meaningless once a colour is nearly black — `#020100` reports saturation 1.0 and hue 30°, arithmetically indistinguishable from a vivid orange — so the old "both saturated" merge branch swallowed pure shadow into dark warm entries, and the shadow then DONATED its share. Measured on `Flex 3.jpg`: search reported **54% orange where 9.5% of the frame was orange**, the gap being one `#020100` bin covering 39%. Across the test set, **79% of all phantom coverage was near-black**, not brown. `_is_shadow_or_gray()` now gates every merge in both directions, using the same cut (`PALETTE_DARK_V = 0.12`) as `color_matches()`'s near-black guard. Verified the fix costs nothing: worst palette still accounts for 97.8% of its frame
- **The exactness slider controls hue AND brightness (V33), because for warm colours hue alone physically cannot work.** Brown is not its own hue — brown IS dark orange, within a couple of degrees on the wheel (picked orange `#E08840` 27°, mid brown `#8B5A2B` 29°, near-black brown `#241205` 25°). Dragging exactness to maximum used to discard bright amber and pale gold — colours Ryan calls orange — while keeping every brown down to brightness 0.14. Deliberately **not** a third slider: hue-tightness and brightness-tightness point the same way for every warm colour. `value_tol` defaults to `None` so non-search callers (the V29/V30 duplicate colour gate, calibrated to 0 false positives over 38 cases) are untouched
- The V24 note that "real reds land under 14°, brown/rust sit at 22–27°" is correct **for red** and does not generalise — red's neighbours differ in hue, orange's do not. Don't reuse red's reasoning on the warm end of the wheel
- The dominance slider runs **0.5%–95%**, not 0.5%–40% (V33). The old ceiling was arbitrary: a real photo's biggest single colour reaches 96%, and 16 of 19 reference shots have a colour over 40%, so "is orange the whole shot?" was literally unaskable. **Above 50% dominance is self-enforcing** — nothing else has room to be larger — which is why there is no separate "dominant colour" toggle
- `colors.palette_version` stamps which build of `extract_palette()` wrote a row; `backfill_palettes()` (renamed from `backfill_palette_shares()`) rebuilds anything older than `PALETTE_VERSION` at boot and self-disables. **Bump `PALETTE_VERSION` whenever extraction output changes for the same input** — otherwise the library sits half-old and half-new and colour search is silently inconsistent between two photos that look alike. It also counts images with a missing/corrupt thumbnail as `unrepairable` in the log; those retry every boot forever and used to be invisible
- `scripts/diagnose_color_filter.py` reproduces the original diagnosis against real photos; `scripts/test_v33_color_fix_locally.py` pins the fixes (18 checks)

**Duplicate detection (V29 colour gate; V30 fingerprint rewrite; V34 batch-delete UI)**
- Duplicate Review's UI (not the detection logic above) batch-selects: each group auto-checks every photo except the first (the one Ryan usually keeps), and one confirmation deletes the whole checked batch through the same `/api/images/bulk-delete` endpoint Select Mode uses, rather than confirming photo by photo. Same instant-close-plus-background-toast pattern as V35's Select Mode fix
- Three gates, ALL must agree before two images are called near-duplicates — cheapest first: phash (fast pre-filter) → signature (does it actually look alike?) → palette (is it actually the same colour?). Same order in `find_duplicates()` and `_ingest_image()`, so the Duplicate Review screen and the live upload/clip check can never disagree
- **phash alone is not evidence and never will be, at any grid size.** The difference-hash asks "is this pixel brighter than its right neighbour?" — on a soft, dark, letterboxed frame the answer is "no" almost everywhere, so the hash comes out nearly blank (measured on real moody stills: 5–12 of the old 64 bits set). Two mostly-blank fingerprints are MATHEMATICALLY FORCED to look alike — their distance can never exceed the bits they set between them. Widening to 16×16 did NOT fix this: those same frames set only 16 of 256 bits. That is why unrelated warm letterboxed frames kept grouping
- `compute_signature()` is what actually decides: a contrast-normalised 16×16 grayscale that keeps ACTUAL values, not just their ordering, so a flat frame still describes itself. Measured over 19 of Ryan's real reference photos (171 unrelated pairs): re-saved copies scored 0.004–0.029, unrelated pairs 0.463+ — a 16× separation, cutoff at 0.15. Calibration result: **0 false positives, 0 missed duplicates across 38 duplicate cases**
- phash threshold is deliberately GENEROUS (20/256) because it only nominates candidates. Don't tighten it to "help" — that just starts missing real duplicates while doing nothing about the flat-frame problem
- Signatures are computed lazily and memoised, only for pairs phash nominated — never one per comparison
- Old 64-bit hashes are 16 hex chars, new ones 64. `phash_distance()` reports mismatched lengths as maximally different rather than XOR-ing them into a meaningless (often small) number, so an un-migrated row degrades to "matches nothing", never to "matches the wrong thing". `backfill_phashes()` rebuilds them at boot and self-disables
- Testing note: a flat two-tone fixture is an ADVERSARIAL input here and will make tests flaky — the split lands on a grid boundary and JPEG ringing flips a whole column by ~2 SD (signature 0.004 → 0.17, ~1 run in 8). Fix it with LOW-FREQUENCY shading, never per-pixel noise: JPEG discards high frequencies first, so pixel jitter is destroyed at low quality and randomises the phash instead (measured 23–24, breaking it the other way)

**Sync-delete parity (V30)**
- Sync used to only ever ADD. A photo deleted directly in Drive left a dead row (plus its tags/decks/favourites) forever. `sync_folder_worker()` now removes rows whose Drive file is gone — automatic, no confirmation (Ryan's call)
- Guarded by one rule that must not be removed: if more than HALF the library would vanish in a single pass, skip and report instead. A partial/failed Drive listing is indistinguishable from a real mass-deletion, and this is the difference between "one stale row cleaned up" and "silently wiped every tag Ryan ever wrote"
- Deliberately separate from `reconcile_drive_changes()`, which never deletes — reconciliation runs unattended at boot, deletion only when someone actually triggers a sync of their own folder

**Stale-thumbnail repair (V30)**
- `reconcile_drive_changes()` rebuilds thumbnail/aspect-ratio/phash/palette for any image whose stored `md5_checksum` no longer matches Drive's. Runs at boot (background thread) AND as step 3 of the duplicate scan
- This exists because the V27 background crop worker wrote to `width`/`height`/`crop_box` — columns that have NEVER existed on `images` — and crashed AFTER overwriting the Drive file. Drive held the cropped image (so the full-res inspector looked right) while the DB kept the pre-crop thumbnail (so the home grid still looked uncropped). Fixed at the source in V30; this catches the backlog
- MUST list each user's OWN folder. `get_root_folder_id()` falls back to the hardcoded default folder for anyone with no `sync_settings` row, so a single shared listing would compare a friend's photos against the admin's folder

**Tag normalisation (V30)**
- `normalize_tag_value()` lowercases AND collapses a trailing plural 's', applied at every tag-write site (Gemini tagger, manual editor, bulk apply). `subjects` is explicitly open-ended free text in the prompt, which is exactly where an LLM's singular/plural choice drifts run to run — "car" and "cars" were showing as separate suggestions
- Conservative on purpose: only a bare trailing 's' (not 'es'/'ies', which usually change the stem), skipping `TAG_PLURAL_STRIP_EXCEPTIONS` where the plural IS the natural tag (glass, lens, hands, …)
- `merge_plural_tag_duplicates()` fixes what's already stored — write-time normalisation can't retroactively repair old rows. Only merges variants coexisting on the SAME photo in the SAME category; a lone plural is renamed, not deleted
- "car (Location)" and "car (Objects)" appearing together is CORRECT, not a bug — the taxonomy has `car` as both a location type and a subject, and those are two different facts about a photo

**Select Mode: shared-tag search + bulk delete (V31)**
- "Shared tags" in the bulk tag panel now means the TRUE intersection — `tags_selection_summary()` only returns rows where `cnt == total` (every selected image carries it). Before V31 it returned every tag any selected image had, with a `count/total` fraction, which got unreadable once a selection got large
- Shared tags are grouped by category (canonical order from `/api/tag-categories`) with a search box that appears once there are more than 6 tags. Typing reorders — matching tags float to the top of their category, and categories containing a match jump ahead of categories that don't. Deliberately reorder, not filter — nothing disappears from view
- `POST /api/images/bulk-delete` batches the same rule `DELETE /api/images/<id>` already used (owner-or-admin; admin's own photos move to Drive's `_Removed`, friends' deletes are DB-only + a `sync_exclusions` row). The `_Removed` folder lookup is cached per root folder for the whole batch — a 50-photo delete lists Drive for it once, not 50 times
- Failures are skip-and-continue, not all-or-nothing: one photo blocked by a Drive permission error is reported in `errors` while the rest of the batch still deletes. Ryan's explicit call — a single bad photo shouldn't hold the other 49 hostage
- **The frontend closes the confirm modal and clears the selection instantly, running the delete as a background job reported through a toast (V35)** — same pattern `CropModal.jsx` and `DuplicateReview.jsx` use for their own background work, instead of blocking on the fetch with a "Working…" button. Its error handling is a SEPARATE try/catch from the rest of the bulk-tag panel: if the response fails to parse after Drive has already moved the files and the DB rows are already gone (slow response, network hiccup), the old shared catch block just logged it and left the stale selection/grid untouched — Ryan hit this directly (9 photos already gone from Drive, app still said "9 selected"). On failure now the selection is dropped (no way to know which ids actually went through) and the grid resyncs from the server via `onResync`
- **Drive moves run 5-at-a-time in a thread pool, not sequentially (V36).** A 16-photo batch used to take 10–20+ seconds one move at a time, long enough that the browser sometimes gave up on the request before it finished (a 499 in Railway's logs). Two things had to be handled deliberately: each worker thread builds its OWN Drive service object via `threading.local()` (the underlying HTTP transport isn't safe to share across threads) built once per thread, not once per photo; and the `_Removed` folder is looked up once before any worker starts, rather than racing to create/cache it per-photo. A move that hits Drive's rate limit gets up to 2 retries with backoff before actually counting as failed, so one busy moment doesn't fail an otherwise-working batch. Single-photo delete is untouched — nothing to parallelize with one photo
- Per-image failure reasons and a request-level summary are logged server-side (V36) — added after a "delete keeps failing" report turned out to be 22 of 23 attempts succeeding, with the one failure being the browser closing the connection early on a slow request, not a real server error. Check the logs before assuming the delete logic is broken

**Select all results + library-wide tag cleanup (V32)**
- `build_search_filters()` is now the ONE place the five filter types (chips / natural language / colour+prom+exact / aspect ratio / film) turn into a WHERE clause. `/api/search`, `/api/search/ids` and `/api/tags/removal-preview` all call it. A select-all that returned a different set than the grid is showing would be worse than no select-all, and a second hand-copied version of that logic WILL drift the first time one filter changes. Same rule on the frontend: `buildFilterParams()` in Home.jsx is the only thing that assembles the query string
- Every condition it emits names bare `images` columns, never a table alias, so callers can drop the whole clause inside a `SELECT id FROM images ...` subquery (which is exactly how the removal preview joins it to `tags`)
- "Select all loaded" was a trap: it selected only the thumbnails already in the browser, so a "118 images · 60 loaded" grid silently gave you 60. `/api/search/ids` returns ids instead of forcing the grid to load every page — a few KB of numbers versus tens of MB of base64 thumbnails. NOT admin-gated (friends use Select Mode too), but scoped to the caller's own library like `/api/search`. If it fails, the selection is left ALONE rather than falling back to the loaded set — silently selecting fewer than asked is the exact bug this replaced
- Bulk tag removal across a filter previews the affected photos FIRST (Ryan's explicit call over a confirm box or an undo window). `/api/tags/removal-preview` is read-only and groups by category — "car (Location)" and "car (Objects)" are two different facts (V30), so a multi-category tag can't be wiped by one undifferentiated click. Counts and id lists are always complete; only the pictures are capped (`TAG_REMOVAL_PREVIEW_SAMPLES`), because 600px base64 thumbs are ~40KB each
- Removal itself reuses `POST /api/tags/bulk-remove` (already `@admin_required`, already normalizes through `normalize_tag_value()`). It is styled amber and worded around the tag, deliberately NOT the red used by bulk delete — one takes a label off, the other moves the actual picture to Drive's `_Removed`
- After a removal the grid RE-RUNS the search (`handleBulkMutated`) instead of patching state: untagging the very thing you're filtered by should drop those photos from view, and the server already knows how to work that out
- `SQL_PARAM_CHUNK` / `chunked()`: select-all makes whole-library id lists routine, past what SQLite accepts as `?` placeholders in one statement. Bulk remove, bulk delete, the selection summary and suggestions all batch through it. `count_tags_for_images()` is the shared, chunked counter the summary and suggestions endpoints used to duplicate
- The chip-vs-natural-language distinction is now stated, not just implied by colour. Clicking an autocomplete suggestion makes a `chips` entry (exact tag); typing and pressing ENTER makes an `nlChips` entry, which expands into a GROUP and matches ANY of them — so an NL search for "neon" returns photos with no "neon" tag, and bulk-remove then has nothing to remove. Gold chips carry a `#`, and a plain-language note appears whenever a describe-it search is active
- The V31 strict intersection in `tags_selection_summary()` is CORRECT and stays. What was broken was its silence: the shared-tags panel now always says "only tags on all N selected photos are shown", and the empty case points at the library-wide cleanup button instead of leaving a dead end
- Shift-click range select lives in `frontend/src/selectionRange.js` as a pure function (`rangeIdsBetween`), tested by `scripts/test_selection_range.mjs` — CLAUDE.md's own verification notes say browser automation can't fire these interactions reliably, so the logic has to be reachable from code. The run follows the ORDER THE SERVER RETURNED (the `images` array), not screen position: masonry drops each photo into whichever column is shortest, so two photos that look adjacent may be far apart in the results. Shift only ever ADDS, never unselects
- Tests: `scripts/test_select_all_and_tag_cleanup_locally.py` (47 checks — select-all vs. grid for every filter type, preview grouping/normalization/read-only-ness, removal touching only the named tag and never an image, admin-only, and a selection bigger than one SQL batch)

**DP technical notes + full-text search (V39)**
- 5 nullable TEXT columns directly on `images` — `camera_rig`, `lens`, `lens_filter`, `stop`, `onset_notes` — not a joined table like `filmography`. Any photo can carry them, not just `my_work`
- **The first owner-editable metadata field in this app.** Every other edit endpoint (`edit_tags`, `update_filmography`) is `@admin_required`, full stop, regardless of who owns the photo — verified by reading `admin_required`'s definition, not assumed. `POST /api/images/<id>/notes` deliberately checks `row['user_id'] == session['user_id'] or session.get('role') == 'admin'` instead, Ryan's explicit call: these are facts a friend would know about their own shoot, not an AI guess to curate
- Search is SQLite FTS5 — ships inside Python's `sqlite3` module, no new pip dependency. `notes_fts` mirrors the 5 columns, using `images.id` directly as the FTS5 table's own rowid (no separate join column). Kept in sync via THREE TRIGGERS on `images` (INSERT seeds a row even when everything's NULL, DELETE removes it, UPDATE rewrites it) — not from every write callsite in `app.py` by hand, same reasoning as `build_search_filters()` (V32). The UPDATE trigger uses `AFTER UPDATE OF camera_rig, lens, lens_filter, stop, onset_notes ON images` — valid SQLite syntax that scopes it to fire ONLY when one of those 5 columns changes, so a crop, favorite toggle, or view-log write (all of which touch `images` itself) can't trigger a pointless FTS rebuild
- `backfill_notes_fts()` seeds pre-existing images at boot (the INSERT trigger only covers new rows going forward) and self-disables — a plain set-based `INSERT ... SELECT ... WHERE id NOT IN (...)`, not a per-image Python loop, since there's no Pillow work to do here unlike the palette/phash backfills
- **Raw phrases are never passed straight into FTS5 `MATCH`** — its query syntax gives meaning to `-`, `"`, `*`, `:`. `_fts5_match_query()` quotes every completed token to force literal matching; for the live-typing prefix case (autocomplete), the trailing token is left unquoted with a `*` appended (FTS5 prefix syntax) but is first stripped to alphanumerics only — quoting would kill the wildcard, but leaving it unquoted AND unstripped is what let a query like `weird"-*query` 500 the endpoint (found and fixed, not theoretical)
- **FTS5's MATCH binding only recognizes the table by its real name, not an alias** — `SELECT ... FROM notes_fts n WHERE n MATCH ?` throws `no such column: n` even though `n` works everywhere else in that same query. Verified directly against `sqlite3`, not assumed. Always write `notes_fts MATCH ?`, even inside a query that aliases the table as something else
- Search integration: `build_search_filters()` takes a `notes` param (JSON array of phrases, each AND'd via its own `notes_fts MATCH` condition — same shape as an `nl_groups` entry). `/api/autocomplete` offers a live `type: 'note'` suggestion, but unlike tag/film suggestions (a fixed vocabulary to pick FROM) there's no discrete value to suggest — notes are freeform prose, so the suggestion IS the search: it only appears once the CURRENT typed text already has a real match (`count > 0`), and selecting it just locks in that exact phrase. Deliberately not scoped by `active_chips` co-occurrence the way tag suggestions are — a global per-user count is enough for v1
- Frontend: `noteChips` in `Home.jsx` (amber, 🔧-prefixed, distinct from gold tag chips and violet NL chips) gets the exact same treatment `nlChips` does everywhere — `buildFilterParams`, `hasFilters`, the unfiltered-shuffle check, bookmarks save/restore. Selecting a `type: 'note'` autocomplete entry calls `selectNote()`, mirroring `selectAr()`
- `ImageDetail.jsx`'s "On-Set Notes" section is collapsible, collapsed by default (the panel is already dense), and directly editable when expanded — no separate read/edit toggle like filmography has, since these are short structured fields with no clickable-search behavior to justify one. No frontend ownership gate either: this file doesn't gate tag/filmography editing client-side (both admin-only, enforced purely server-side), so the new section follows that same precedent rather than inventing permission plumbing this codebase doesn't have yet
- `scripts/test_dp_notes_search_locally.py`, 12 checks — trigger sync/scoping (including that an UNRELATED column edit leaves `notes_fts` untouched), owner/admin/rejected permission paths, the backfill, search + autocomplete integration end to end via real HTTP requests

**PDF lookbook export (V40)**
- ALL layout lives in `backend/pdf_export.py`; `GET /api/decks/<id>/export.pdf` in `app.py` only queries rows and hands them over. Two layouts, `?layout=full` (one photo per page + scene title cards, the client pitch doc) and `?layout=grid` (3×2 contact sheet, the crew handout), plus `?include_unsorted=1|0`
- **HAND REPORTLAB RAW JPEG BYTES, NEVER A PIL IMAGE OBJECT.** This is a file-size rule, not style, and it is worth ~9×: given a PIL object reportlab has no compressed stream to reuse so it embeds raw pixels Flate-compressed (lossless, hopeless on photos); given JPEG bytes it copies them in as DCTDecode. Measured on a real 20-frame deck: **13.7MB via PIL vs 1.5MB via bytes**. The first number does not survive an email attachment limit, and emailing this file IS the feature. `_prepare()` therefore reuses the stored blob verbatim whenever that's lossless-safe and only re-encodes when the pixels actually had to change
- The re-encode trigger reads the **EXIF orientation tag**, not a before/after size comparison — orientations 2 and 4 are MIRRORS, which rewrite pixels without changing dimensions, so a size check would call them a no-op and reuse a blob whose pixels were just corrected. (Thumbnails are written EXIF-corrected since V36; this only matters for older rows)
- **Fixed page size, letterboxed — never crop-to-fill, never resize the page to the photo.** Every page is landscape US Letter; images scale to fit with aspect ratio intact and centre on the near-black page (`#141416`), which is what makes leftover space read as intentional margin rather than as bars. Silently re-framing a cinematographer's shots would make the export worse than useless. The timeline's original wording said "full bleed"; this is the deliberate departure from it
- **Thumbnails only — no Drive round trip for full-res.** Ryan's call: judge whether 800px is sharp enough before paying that cost. Note the stored thumbnails are 800px/q85 (`generate_thumbnail`'s actual defaults), not the 600px/q75 the older notes in this file claim
- Read-only on purpose: the endpoint deliberately does NOT call `log_deck_activity()`, which would bump `decks.updated_at` and light up the frontend's "New changes" banner for an export that changed nothing. A test pins this
- Does NOT reuse `_deck_payload()` — that base64-encodes every thumbnail into JSON (pure waste when the bytes go straight into a PDF), and its single global ORDER BY doesn't give correct per-scene ordering
- A single unreadable photo must never take the whole export down: anything that fails to decode is skipped and logged, and a section left with no usable photos emits **no scene title card at all** (no stranded headers). A wholly empty deck still yields a valid 1-page PDF
- `sanitize_filename()` runs before the deck name reaches `Content-Disposition` — a deck named `a/b` or one carrying control characters must not be able to steer the download
- Frontend `ExportModal` in `DeckDetail.jsx` uses the same instant-close-plus-background-toast pattern as `CropModal`/`DuplicateReview` (V35): it reads every value it needs, closes, then downloads inside a bare IIFE that touches NO component state, because by then the component is unmounted. The blob object URL is revoked on a timer, not synchronously after `click()` — Safari cancels the download otherwise. The button is disabled while viewing an offline cached copy
- Fonts are reportlab's built-in Helvetica only. The app's Manrope isn't in the repo as a font file and shipping one was out of scope
- `scripts/test_pdf_export_locally.py` (33 checks, renderer — page counts per layout, grid continuation, note-less photos, corrupt thumbnails, empty sections, the sanitizer) and `scripts/test_pdf_export_endpoint_locally.py` (18 checks, endpoint via the Flask test client — login gate, cross-user 404, param validation, read-only-ness)

**Presentation mode (V41)**
- Entirely frontend — no new endpoint. `PresentationMode.jsx` reads the SAME `deck.scenes` + `deck.images` the deck page already fetched, so it works from the offline cached copy and there's zero extra network round trip to open it
- **Frames fit whole on a black background, never cropped — the same rule as the PDF exporter (V40)**, for the same reason: re-framing a cinematographer's shots to fill a screen shape would make the feature worse than useless. `object-fit: contain`, not `cover`
- **Advancing past the last frame holds there.** No loop, no auto-exit, no end card. Deliberate: in a live pitch you can never accidentally reveal you've run out of material or drop the client into the app UI. `goNext()`/`goPrev()` clamp with `Math.min`/`Math.max`, they never wrap
- **Title cards only when there are 2+ non-empty sections** (scenes with photos, plus Unsorted if it has any) — a one-scene deck opens straight on its first frame instead of making you click past a card that announces the deck's only content. An empty scene contributes nothing at all, same no-stranded-header rule as the PDF
- **Notes visibility remembers your last choice**, not a fixed default — `localStorage['fa.presentation.showNotes']`, first-run default is ON so the toggle is discoverable. Toggled by the on-screen pill or the `N` key
- The running order — scene `sort_order`, photo storyboard order preserved (never re-sorted by id), Unsorted always last, which sections get title cards — lives in `frontend/src/presentationOrder.js` as a pure function (`buildSlides`), not inline in the component. Same reasoning as V32's `selectionRange.js`: this is the one part of the feature that can be silently WRONG rather than visibly broken (a mis-ordered pitch still looks fine on screen), and CLAUDE.md's verification notes say browser automation can't reliably drive keyboard interactions to catch it — so it has to be reachable from code. `scripts/test_presentation_order.mjs`, 20 checks, including a scene dragged above another (V38 reorder) presenting in its new position, and a photo whose `scene_id` no longer matches any scene landing in Unsorted rather than silently vanishing
- **The image source is one function, `slideImageSrc()`.** Ships on the deck's already-loaded ~800px thumbnails (Ryan's call — zero loading pause between frames, same asset the grid has in memory). A neighbour-preloading effect (`useEffect` warming index ±1/+2) is already wired to call it — a no-op today since thumbnails are already in memory, but it means upgrading to full-res-with-preload later is a change to that one function, not a rewrite
- Esc is caught two ways: a `keydown` handler for the normal case, AND a `fullscreenchange` listener for when the browser's native fullscreen swallows the Esc keystroke itself (it exits fullscreen without ever delivering a keydown to the page) — without the second listener, Esc would silently fail to close the presentation about half the time depending on whether real fullscreen engaged
- Present is scoped to the owner's deck page only for V41 — deliberately not on the public `/share/<token>` view. If a future day wants viewers to self-present a shared lookbook, that's new scope on a page with its own separate data fetch, not a checkbox here
- Found and fixed a real bug while verifying this end-to-end: `scripts/run_local_for_browser_check.py` broke the moment V40 added `from pdf_export import ...` to `app.py` — the harness patches `app.py` into a temp directory but never added `backend/` to `sys.path`. **Correction (V42): this note originally claimed the `test_*_locally.py` scripts already had the fix — untrue. Only one did. The other 29 were fixed in V42's own verification pass; see below**

**Client feedback — picks + comments on a share link (V42)**
- `deck_picks` and `deck_comments` key off `deck_image_id`, same as `storyboard_note` — an image can live in more than one deck, and feedback belongs to the one it was left on
- **A pick is `UNIQUE(deck_image_id, viewer_token)` — idempotent per browser.** `viewer_token` is a random id the viewer's OWN browser generates (`crypto.randomUUID()`, `frontend/src/viewerIdentity.js`) and stores in `localStorage`, kept SEPARATE from the display name they type. Retyping the name doesn't fork pick history; two different people who both type "Sarah" don't share one pick slot, because the token — not the name — is what the server actually keys on
- **`decks.feedback_enabled` defaults OFF for anything that existed before this migration, ON for everything created after.** Confirmed with Ryan: a link already sitting in an agency inbox must not start accepting public comments the moment this shipped. The column's own `DEFAULT 0` covers old rows; `create_deck()` explicitly `INSERT`s 1, overriding that default for every new deck
- `_deck_feedback_payload()` is the ONE function both the owner's Feedback panel (`GET /api/decks/<id>/feedback`) and the public share page's own feedback view (`GET /api/share/<token>/feedback`) call — same reasoning as `_deck_payload()` (V23) and `build_search_filters()` (V32): two hand-copied versions of "count the picks" WILL drift the first time one changes. Ranks frames most-picked-first; a frame with zero picks and zero comments is never included, so a deleted comment can't leave a stranded empty entry on a fresh fetch
- Every public write (`POST`/`DELETE /api/share/<token>/picks`, `POST /api/share/<token>/comments`) gates through `_feedback_deck_for_token()`, which checks the token AND `feedback_enabled` together — turning feedback off makes writes 404 immediately, even with a perfectly valid token and a perfectly valid `deck_image_id`. The `GET` still works when feedback is off (returns `enabled: false` and empty), so the frontend can render "not accepting feedback" rather than erroring
- Deleting a comment is owner-only (`DELETE /api/decks/<id>/comments/<id>`) — the pressure valve named in the product plan: the share token is unguessable, but anyone holding it can post
- Picks/comments are cleaned up everywhere `deck_images` rows already get deleted (single-photo removal via `/api/deck-images/<id>`, whole-deck delete) — BEFORE the `deck_images` rows themselves, since the cleanup query joins on `deck_image_id`
- Frontend: the name prompt fires on the FIRST pick or comment, whichever a viewer does first — not a gate in front of the whole page. Picks are optimistic (flipped back on failure); a failed comment leaves the typed text in place with an inline error rather than silently eating it
- **Deleting the only comment on a picks-less frame used to leave a stranded empty row in the owner's Feedback panel** — the backend's `ranked_deck_image_ids` already excludes empty frames on a fresh fetch, but the panel's local state after an optimistic delete didn't re-derive that. `FeedbackPanel.jsx` now computes `visibleIds` by filtering the CURRENT local state on every render, not just trusting the list from the initial load
- **The Share panel's on/off toggle was originally a plain `div` with an `onClick`** — no keyboard access, no accessible role, invisible to the accessibility tree. Found because browser-verification tooling couldn't discover it either; a screen-reader user would hit the identical wall. Now `role="switch"`, `aria-checked`, `tabIndex`, and an `onKeyDown` for Enter/Space
- Found while verifying this end-to-end, unrelated to the feature itself: 29 of the repo's `test_*_locally.py` scripts had been silently `ModuleNotFoundError`-ing since Day 22 (same missing-`sys.path` bug as above), so nobody had actually run them since V40 landed. Fixed in its own commit. One test (`test_shuffle_locally.py`) still fails on an assertion that checks pre-V35 recency-ordering behavior — that's a stale test asserting removed behavior, not a regression; flagged for its own fix rather than bundled in
- `scripts/test_client_feedback_locally.py`, 48 checks — new-deck vs. pre-migration `feedback_enabled` defaults, pick idempotency and un-pick, owner/public payload parity, the enabled-gate blocking writes but not reads, comment deletion, cross-user isolation, and cascade cleanup on both delete paths

**Web clipping (V25)**
- Chrome extension lives in `/extension/` (MV3, load unpacked — see its README)
- `POST /api/clip` takes a base64 data URL, not a URL to fetch: capture happens in the browser, where the page's own cookies and hotlink protection already apply, so images this server could never fetch still work — and video frames, which exist only as canvas pixels, work at all
- Shares `_ingest_image()` with `/api/upload`, so clips get the same phash duplicate check, Drive write, thumbnail, palette and tagging queue
- Admin-only, like upload — both write through user 1's Google connection
- `images.source_url` records the page a clip came from (NULL for syncs/uploads)
- Auth: the session cookie is SameSite-blocked from a `chrome-extension://` origin, so the extension reads it via `chrome.cookies` and echoes it in `X-FA-Session`; `_adopt_session_from_header()` verifies the signature with Flask's own serializer. Not a CSRF hole — a custom header can't be set cross-site, and the value is the cookie the caller needed anyway

**Cropping (V18; detection engine replaced in V26)**
- Detection runs in the BROWSER on full-res pixels (`frontend/src/cropDetectV2.js`), then `POST /api/images/<id>/crop` applies the box server-side. The box travels as percentages 0–100, so it means the same thing at any resolution
- The original v34 engine (`cropDetect.js`, deleted in V26 — recover with `git show 10316f3:frontend/src/cropDetect.js`) decided "is this line chrome?" from BRIGHTNESS plus a one-sided trimmed std. Measured against Ryan's 14-image `Test Photos/To Crop` set it scored **2/14**, failing in both directions on the same image. It is not coming back; don't reintroduce brightness thresholds
- V26 uses ONE statistic: the **median absolute deviation** of each line's luminance. Chrome is whatever is FLAT, whatever colour — so black letterbox, white mats, grey app backgrounds and the IG gutter are all one code path. MAD ignores a minority of outliers at BOTH ends, which is why an icon row (flat background + a few glyphs) correctly reads as chrome and a 9px scrollbar sliver no longer protects a white border
- Threshold is **MAD ≤ 0** (strictly dead flat) and that is load-bearing: at ≤ 1 the genuine dark grass/sky edges of IMG_1068 and IMG_1081 score exactly 1 and get eaten. Looser only ever comes from `level` (the Redetect button). NOTE this inverts v34's Redetect semantics — higher level now strips MORE, not less
- Column stats are measured only within the content ROWS (and vice versa). Measuring a column over the full height includes the letterbox, which is how dark picture edges used to read as pillarbox
- Peeling stops at the first non-flat line — no gap-bridging. Deliberate: an under-crop is one Tighten press away, an over-crop is unrecoverable picture loss
- `detectCropTightened()` is what the UI calls. Auto-tighten runs at MAD ≤ 1 but is double-guarded — it may only touch an edge detection actually peeled, and the flat run must END on its own inside a 2% cap. Without BOTH guards it eats 38–48px of real picture on the IG screenshots
- **Confidence labels were removed entirely, not fixed.** They scored a crop by how sharply the boundary line contrasted with its neighbor — but a dark/moody frame (Ryan's most common material) often reads as nearly as "flat" as the bar it's next to purely because it's dark, so a correct crop routinely got labeled "low confidence." Rather than ship a number that actively misled on the app's most common content, `detectCrop()`/`detectCropTightened()` no longer compute or return one, and the UI shows no confidence badge. If a future version wants this back, it needs a signal relative to that photo's OWN contrast level, not a fixed brightness/texture threshold shared across every image
- Crop is destructive (in-place `files().update()`), so the untouched original is copied into `_Removed` FIRST and a failed backup aborts the crop. Two Drive clients on purpose: the service account finds `_Removed` (it can list), the owner's OAuth writes the backup (a service account has no storage quota, so its `files().create()` always fails)
- Regression harness: `scripts/crop_regression.html` (serve the repo root over HTTP; see the comment in the file). `scripts/test_crop_queue_locally.py` covers the endpoint and the background queue
- **A failed crop used to report a count and destroy the reason (fixed V45/Day 27).** The background worker's `except` stores the message in `_crop_progress` and never prints it, so nothing reaches Railway; the auto-start toast showed only "✗ N images failed to crop"; and `CropModal` then POSTs `/api/crop-progress/reset` about a second later, wiping both `failed` and `active_jobs`. `applyCrops()`'s results screen does render full error text per image, but the auto-start effect fires first on every real batch, so it is unreachable in practice. The toast now carries the deduplicated reasons and holds 30s (not the 4s default) because it is the only surviving record. Same species as the V44 `except: pass` audit, missed by it because it isn't literally `except: pass` — it's catch, store, then delete the store
- **Select-Mode selection used to survive a crop batch (fixed V38).** Selecting photos and running Crop All left Select Mode and the selection stuck on after returning to Home — `<CropModal onClose={() => setCropImages(null)}>` only closed the modal, never touched selection state. `CropModal` now calls `onClose(started)`, where `started` is only true if at least one crop was actually queued (the auto-start effect reaching `'summary'` with a real target, or the manual `applyCrops()` path) — every other close (cancel, Escape, deleting the whole review batch) passes `false`. Home only exits Select Mode on `true`, via the same `toggleTagMode()` that bulk delete's Exit button already used

**Perspective crop (V32)**
- A second crop SHAPE, not a second crop tool: `POST /api/images/<id>/crop` takes an optional `corners` field (four x/y points, percentages 0–100, same resolution-independence as `box`). Absent `corners` = the pre-V32 rectangle path, byte for byte. Old queued jobs and old clients keep working
- Both shapes converge before the destructive write, so the `_Removed` backup-first rule, the two-Drive-client split and the post-write refresh (thumbnail → aspect_ratio → md5_checksum → phash → palette) are the SAME lines for both. Never fork that tail — V27 crashed after overwriting Drive and left the DB holding a pre-crop thumbnail
- The 8 homography coefficients are solved by hand (8×8 Gaussian elimination with partial pivoting) in BOTH `backend/app.py` and `frontend/src/perspective.js`. numpy is deliberately NOT added — it isn't in `backend/requirements.txt`, and touching that file adds 3+ min to every Railway deploy. The two solvers are cross-checked to 9 decimals
- Output size = the **average of each pair of opposite edges**. Sizing off a single edge bakes the perspective back in: on a monitor shot from the left the near edge is far longer, so it stretches the result while the far edge squashes it
- Quads are validated BEFORE queuing: convex only (a rectangle photographed from any angle is always convex, so a dented quad isn't a perspective view of anything), no bow-ties, no collinear/coincident points, ≥1% of frame area. Mirrored/reverse-wound quads are ALLOWED — they produce a mirrored result and the live preview shows exactly that
- Detection is rectangle-only. Tighten/Redetect and `cropDetectV2.js` are untouched — MAD line-flatness has no meaning for a tilted quad
- `scripts/test_perspective_crop_locally.py`, 57 checks

**Scene reordering (V38)**
- Scenes already had a `sort_order` column (used since decks/scenes shipped) — only a way to change it was missing. `POST /api/decks/<id>/scenes/reorder` mirrors the pre-existing `POST /api/decks/<id>/reorder` (photo storyboard order): full ordered id list required, owner-only, rejects unless the submitted id set is EXACTLY the deck's current scene ids, writes `sort_order` from list position, calls `touch_deck()` with no activity-log entry (reordering is deliberately silent in the activity feed, same as photo reorder)
- Drag-to-reorder in `DeckDetail.jsx` shares its drop target (the scene card) with an EXISTING, unrelated drag: photo tiles dropped into a scene use `dataTransfer` type `text/plain` (the deckImageId). A scene-reorder drag uses a distinct type, `application/x-scene-reorder`, checked via `e.dataTransfer.types.includes(...)` (readable on `dragover`/`drop`, unlike `getData`) so the two drags can never be confused. If a future drag interaction is added to this view, give it its own `dataTransfer` type too rather than overloading `text/plain`
- The Unsorted section is structurally excluded from reordering — it's rendered outside the `scenes.map()` that wires up scene-drag handlers, and stays pinned first
- The "⊞ Storyboard" button (existing since Day 12/V11 — reorders photos WITHIN a scene) was renamed to "↕ Reorder Photos" and given a filled/prominent style. Ryan didn't know the feature existed; the old label read as an export or a view toggle, not an action
- `scripts/test_scene_reorder_locally.py` covers the endpoint

**Offline support (V23, fixed; thumbnail caching added V43)**
- `frontend/public/sw.js` caches the app shell so Frame Atlas opens with no connection; deck DATA comes from IndexedDB (`useOfflineCache.js`). The service worker never caches `/api` — a stale `/api/auth/me` would show the wrong account — with ONE deliberate exception: `/api/images/<id>/thumb` (see Thumbnails above), cache-first since the URL is content-addressed by checksum
- Cache lookups MUST pass `{ignoreVary: true}`: flask-cors sets `Vary: Origin`, and an ES-module request carries an `Origin` header the worker's own precache fetch doesn't — without it the JS bundle misses the cache and the app never boots offline (the stylesheet, being no-cors, matched fine and hid the problem)
- Cached responses are replayed with rebuilt headers; the Flask dev server emits a doubled `Date` that the module loader rejects outright
- `AuthContext` remembers the last signed-in user in localStorage so a dropped connection isn't treated as a logout. UI hint only — the server still checks every request
- Deck edits stamp `decks.updated_at` via `log_deck_activity()`; `reorder` has no activity entry so it calls `touch_deck()` itself

**API Endpoints (complete)**
- `/api/images` — all images
- `/api/images/<id>/thumb` — GET (V43), the cacheable thumbnail URL every authenticated context now uses instead of embedded base64; `?v=` is a cache-busting checksum, ignored server-side. Owner-or-admin, same as crop/delete/notes
- `/api/clip` — POST (V25), browser-extension clipping; see above
- `/api/search` — AND-filter tag search; optional `seed` param (V14) switches the unfiltered grid to a deterministic shuffled order; any active filter ignores the seed and stays newest-first. **V35: dropped the "seen in the last 7 days sorts below unseen" bucket** — once most of a library has been viewed recently (Ryan's case: 3,496 of 3,499 images), that bucket swallows almost everything and the same tiny unseen leftover keeps winning the top slots every day, so the shuffle stopped looking random. It's a straight seeded shuffle now, no recency weighting. Optional `ar` param (V15) filters by aspect-ratio bucket (e.g. `ar=2.39:1`) — every image snaps to its nearest standard format via `normalize_ar_label()`, same math as the tile labels. Optional `prom` + `exact` params (V24) tune the `color` filter: `prom` is the minimum percent of the frame the colour must cover (default 6; the UI calls it DOMINANCE and spans 0.5–95), `exact` is 0–100 strictness (default 60) covering BOTH hue (≈15° at default) and, since V33, brightness. Absent params take those defaults, so pre-V24 bookmarks come back tighter than they were saved — and tighter again after V33, since the same numbers now buy a real brightness filter too
- `/api/search/ids` — GET (V32), same params as `/api/search`; returns `{ids, total}` for EVERY match, powering "Select all N results". Shares `build_search_filters()` with `/api/search` so the two can never disagree
- `/api/decks/<id>/export.pdf` — GET (V40), owner-only, `?layout=full|grid` + `?include_unsorted=1|0`; returns a PDF lookbook as a file attachment. Read-only — writes nothing, logs no activity
- `/api/decks/<id>/feedback` — GET (V42), owner-only, ranked picks + comments for the deck. `/api/decks/<id>/feedback-enabled` — POST, owner-only, `{enabled}` toggle. `/api/decks/<id>/comments/<id>` — DELETE, owner-only
- `/api/share/<token>/feedback` — GET (V42), public, no login; `X-FA-Viewer` header (or `?viewer_token=`) marks `picked_by_me`. `/api/share/<token>/picks` — POST/DELETE, public; `/api/share/<token>/comments` — POST, public. All four 404 unless the deck's `feedback_enabled` is on
- `/api/tags/removal-preview` — GET (V32), admin-only, `value` + the same filter params; read-only preview of which photos would lose a tag, grouped by category, with thumbnails. The removal itself goes through `/api/tags/bulk-remove`
- `/api/views/log` — POST (V14), body `{image_ids: [...]}`; upserts per-user `image_views` rows (`last_seen_at`, `seen_count`). Frontend batches viewed tiles and flushes only on tab-hide/page-leave so the shuffle order never shifts mid-visit
- `/api/autocomplete` — tag suggestions, frequency-sorted; also returns film matches (title/director/DP) and (V15) aspect-ratio bucket suggestions (`type: 'ar'`) when the query looks like a ratio ("9:16", "2.35") or an alias ("scope", "vertical", "square")
- `/api/sync/status` — current sync state; V17: only the sync's owner (or admin) sees filenames/errors, everyone else gets a bare `{in_progress, yours: false}`
- `/api/sync/connect-folder` — POST (V17), body `{folder: "<pasted link or ID>"}`; parses the folder ID, verifies the service account can see it (friendly `not_shared` 403 naming the robot email if not), saves it to `sync_settings`, returns folder name + image count
- `/api/account/setup-status` — GET (V17): robot email, folder connected?, image count + cap, Gemini key saved? — powers the Home setup checklist and Account page
- `/api/tag-progress` — tagging progress
- `/api/tag-progress/stream` — SSE stream for live progress UI
- `/api/analytics` — dashboard rollups: totals, tag counts by category, growth by month
- `/api/views/<favorites|flagged|recent>` — filtered image lists (recent takes `?days=` and `?limit=`)
- `/api/flags/clear-all` — POST, unflags everything (never deletes)
- `/api/models` — Gemini diagnostic, KEPT on purpose (Day 13 decision): first-stop check when auto-tagging mass-fails (`/api/debug*` removed Day 13 as planned)
- `/api/setup`, `/api/setup/status` — one-time admin bootstrap (Day 14), self-disables after first use
- `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`, `/api/auth/register` — session login + invite-code signup (Day 14)
- `/api/admin/invite-codes` — GET/POST/DELETE, admin-only invite code management (Day 14)

---

## Design System

See `DESIGN.md` for the full visual specification — colors, typography, spacing, and component patterns.

The short version: dark cinematic UI, warm gold accent (`#D9A441`), Manrope font, image-first layout.

---

## Infrastructure Roadmap

Railway (now) → Fly.io (Day 16, saves ~$60/year) → Self-hosted Ugreen NAS via Docker + Tailscale (future, $0/month)

Migration to NAS only requires one script to remap Drive file IDs to local filenames in SQLite. All tags and metadata stay intact.

---

## Docs Folder

All planning documents live in `/docs/`:
- `1_Frame_Atlas_PRD.md` — full product spec
- `2_Frame_Atlas_Build_Timeline.md` — day-by-day build plan
- `3_Session_Log.md` — session history and current state
- `Frame_Atlas.html` — visual design reference (open in browser)

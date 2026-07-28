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
│   └── app.py              # All server logic and API endpoints
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

**Server**
- App runs on port `8080`
- Railway domain is pointed at port `8080` in Settings → Networking

**Google Drive**
- Service account email must be explicitly shared on the Drive folder (Share → paste email → Viewer)
- `list_drive_folders()` searches the service account's own Drive root — we hardcode the folder ID instead
- Ryan's Drive folder ID: `1LHPVyo3QjOEcizc1Io2UVjxzX4FQ7yDG`
- **Personal libraries (V17): ALL sync goes through the service account.** Friends share their folder with the robot email and paste the folder link (`/api/sync/connect-folder`). Do NOT try to sync via a user's OAuth token — it's `drive.file`-scoped, and picking a folder in the Google Picker grants access to the folder itself, NOT the files inside it; the old OAuth+Picker sync path (Day 14 Stage 2a) could never see pre-existing images and was removed. User OAuth remains only for the Upload button (creates files, which `drive.file` allows)
- Non-admin libraries: 1,000-image soft cap (`PERSONAL_LIBRARY_CAP`); friend deletes are DB-only (no Drive move — Viewer share) and recorded in `sync_exclusions` so the next sync doesn't re-import them

**Gemini AI**
- Use `google-genai==1.16.0` — NOT `google-generativeai==0.3.0` (that one hits a broken old endpoint)
- Re-tagging an image wipes only AI-written tags (`clear_ai_tags()`); manual categories in `MANUAL_TAG_CATEGORIES` (`my_work`, `misc`) always survive. `my_work` (V15) is Ryan's own-projects category (gaffed / DP'd / photographed) — human-applied only, never in the Gemini prompt

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

**Thumbnails**
- Stored as base64 blobs in SQLite, served as data URIs (no separate thumbnail folder or `/thumbnails/` route)
- Target spec: 600px wide, Pillow quality 75

**Colour (V24)**
- `colors.share` = fraction of the frame that colour covers. `extract_palette()` always computed it; before V24 it was discarded. Shades merged during dedupe donate their share to the colour that absorbed them, so a palette entry means "this colour family", not one bin
- Palette rank is **vibrance**-ordered on purpose (a small vivid patch outranks a big dull one), which is why coverage — not rank — is what filters out specks of colour
- Hue angle, not `color_distance()`, decides colour closeness. The weighted-RGB metric is green-heavy and rates brown (461) a closer match to red than pink (1633). Always guard on saturation when comparing hues: grey reports hue 0.0, identical to pure red
- `backfill_palette_shares()` runs at boot, rebuilds any palette with a NULL share, and self-disables. Search falls back to hue-only matching for rows it hasn't reached yet

**Duplicate detection (V29 colour gate; V30 fingerprint rewrite)**
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
- Confidence labels are currently unreliable (dark artwork reads "low" even on a perfect crop) — do not treat them as a safety signal yet
- Crop is destructive (in-place `files().update()`), so the untouched original is copied into `_Removed` FIRST and a failed backup aborts the crop. Two Drive clients on purpose: the service account finds `_Removed` (it can list), the owner's OAuth writes the backup (a service account has no storage quota, so its `files().create()` always fails)
- Regression harness: `scripts/crop_regression.html` (serve the repo root over HTTP; see the comment in the file). `scripts/test_crop_locally.py` covers the endpoint, 23 checks

**Offline support (V23, fixed)**
- `frontend/public/sw.js` caches the app shell so Frame Atlas opens with no connection; deck DATA comes from IndexedDB (`useOfflineCache.js`). The service worker never caches `/api` — a stale `/api/auth/me` would show the wrong account
- Cache lookups MUST pass `{ignoreVary: true}`: flask-cors sets `Vary: Origin`, and an ES-module request carries an `Origin` header the worker's own precache fetch doesn't — without it the JS bundle misses the cache and the app never boots offline (the stylesheet, being no-cors, matched fine and hid the problem)
- Cached responses are replayed with rebuilt headers; the Flask dev server emits a doubled `Date` that the module loader rejects outright
- `AuthContext` remembers the last signed-in user in localStorage so a dropped connection isn't treated as a logout. UI hint only — the server still checks every request
- Deck edits stamp `decks.updated_at` via `log_deck_activity()`; `reorder` has no activity entry so it calls `touch_deck()` itself

**API Endpoints (complete)**
- `/api/images` — all images
- `/api/clip` — POST (V25), browser-extension clipping; see above
- `/api/search` — AND-filter tag search; optional `seed` param (V14) switches the unfiltered grid to a deterministic shuffled order — images the user viewed in the last 7 days sort below unseen ones; any active filter ignores the seed and stays newest-first. Optional `ar` param (V15) filters by aspect-ratio bucket (e.g. `ar=2.39:1`) — every image snaps to its nearest standard format via `normalize_ar_label()`, same math as the tile labels. Optional `prom` + `exact` params (V24) tune the `color` filter: `prom` is the minimum percent of the frame the colour must cover (default 6), `exact` is 0–100 hue strictness (default 60, ≈15°). Absent params take those defaults, so pre-V24 bookmarks come back tighter than they were saved
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

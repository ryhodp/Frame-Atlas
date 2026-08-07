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
- **Automatic monthly backup (V27), rewritten to run entirely in RAM (V35).** `run_db_backup()` used to write a full temporary copy of the database to `/app/data` before uploading it — but that volume is only 434MB total, so there's rarely room for both the live DB and a temp copy at once (the DB itself was 283MB). A copy dying partway left the scratch file behind, silently eating whatever space was left — this is what crashed the app on July 31, 2026, and was very likely also breaking Select Mode's bulk deletes around the same time (a delete's own DB write needs a little headroom too). Now uses Python 3.11's `Connection.serialize()` — the backup is built via SQLite's own `backup()` API into an in-memory connection, then pulled out as bytes. Nothing touches disk that needs cleaning up

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
- **`generate_thumbnail()` must apply EXIF orientation before resizing (V36).** It wasn't, so a phone photo shot sideways got re-saved with the sideways pixels baked in and the rotation tag dropped — the full-res view still looked correct because it streams the untouched Drive original straight through, tag intact, so this bug was invisible there and only showed up in the grid/thumbnail. Same fix the crop path already had; if a future image-processing path resaves pixels, it needs this too

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

**Perspective crop (V32)**
- A second crop SHAPE, not a second crop tool: `POST /api/images/<id>/crop` takes an optional `corners` field (four x/y points, percentages 0–100, same resolution-independence as `box`). Absent `corners` = the pre-V32 rectangle path, byte for byte. Old queued jobs and old clients keep working
- Both shapes converge before the destructive write, so the `_Removed` backup-first rule, the two-Drive-client split and the post-write refresh (thumbnail → aspect_ratio → md5_checksum → phash → palette) are the SAME lines for both. Never fork that tail — V27 crashed after overwriting Drive and left the DB holding a pre-crop thumbnail
- The 8 homography coefficients are solved by hand (8×8 Gaussian elimination with partial pivoting) in BOTH `backend/app.py` and `frontend/src/perspective.js`. numpy is deliberately NOT added — it isn't in `backend/requirements.txt`, and touching that file adds 3+ min to every Railway deploy. The two solvers are cross-checked to 9 decimals
- Output size = the **average of each pair of opposite edges**. Sizing off a single edge bakes the perspective back in: on a monitor shot from the left the near edge is far longer, so it stretches the result while the far edge squashes it
- Quads are validated BEFORE queuing: convex only (a rectangle photographed from any angle is always convex, so a dented quad isn't a perspective view of anything), no bow-ties, no collinear/coincident points, ≥1% of frame area. Mirrored/reverse-wound quads are ALLOWED — they produce a mirrored result and the live preview shows exactly that
- Detection is rectangle-only. Tighten/Redetect and `cropDetectV2.js` are untouched — MAD line-flatness has no meaning for a tilted quad
- `scripts/test_perspective_crop_locally.py`, 57 checks

**Offline support (V23, fixed)**
- `frontend/public/sw.js` caches the app shell so Frame Atlas opens with no connection; deck DATA comes from IndexedDB (`useOfflineCache.js`). The service worker never caches `/api` — a stale `/api/auth/me` would show the wrong account
- Cache lookups MUST pass `{ignoreVary: true}`: flask-cors sets `Vary: Origin`, and an ES-module request carries an `Origin` header the worker's own precache fetch doesn't — without it the JS bundle misses the cache and the app never boots offline (the stylesheet, being no-cors, matched fine and hid the problem)
- Cached responses are replayed with rebuilt headers; the Flask dev server emits a doubled `Date` that the module loader rejects outright
- `AuthContext` remembers the last signed-in user in localStorage so a dropped connection isn't treated as a logout. UI hint only — the server still checks every request
- Deck edits stamp `decks.updated_at` via `log_deck_activity()`; `reorder` has no activity entry so it calls `touch_deck()` itself

**API Endpoints (complete)**
- `/api/images` — all images
- `/api/clip` — POST (V25), browser-extension clipping; see above
- `/api/search` — AND-filter tag search; optional `seed` param (V14) switches the unfiltered grid to a deterministic shuffled order; any active filter ignores the seed and stays newest-first. **V35: dropped the "seen in the last 7 days sorts below unseen" bucket** — once most of a library has been viewed recently (Ryan's case: 3,496 of 3,499 images), that bucket swallows almost everything and the same tiny unseen leftover keeps winning the top slots every day, so the shuffle stopped looking random. It's a straight seeded shuffle now, no recency weighting. Optional `ar` param (V15) filters by aspect-ratio bucket (e.g. `ar=2.39:1`) — every image snaps to its nearest standard format via `normalize_ar_label()`, same math as the tile labels. Optional `prom` + `exact` params (V24) tune the `color` filter: `prom` is the minimum percent of the frame the colour must cover (default 6; the UI calls it DOMINANCE and spans 0.5–95), `exact` is 0–100 strictness (default 60) covering BOTH hue (≈15° at default) and, since V33, brightness. Absent params take those defaults, so pre-V24 bookmarks come back tighter than they were saved — and tighter again after V33, since the same numbers now buy a real brightness filter too
- `/api/search/ids` — GET (V32), same params as `/api/search`; returns `{ids, total}` for EVERY match, powering "Select all N results". Shares `build_search_filters()` with `/api/search` so the two can never disagree
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

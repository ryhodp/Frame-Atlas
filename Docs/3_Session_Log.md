# Reference Library — Session Log
*Updated after each build day. Source of truth for where we are and what's next.*

---

## How This Works

After each build session, a new entry is added to this log capturing:
- **What we built** — features completed, decisions made
- **Deferred** — things planned for the day that got pushed, with reason
- **Up next** — exact starting point for the next session

At the start of any new session, say: *"I'm ready for Day X"* — this log tells Claude exactly where we are.

---

## Day 0 — Account Setup
*Status: COMPLETE*

- [x] GitHub repo created
- [x] Railway account + project connected to repo
- [x] Google Cloud: Drive API enabled, service account created, JSON downloaded
- [x] Google AI Studio: Gemini API key generated

---

## Day 1 — Skeleton Deploy
*Completed: June 26, 2026*

See Day 2 log for full details. App scaffolded and deployed to Railway.

---

## Day 2 — Google Drive Sync Pipeline
*Completed: June 26, 2026*

### What We Built
- Flask backend with sync worker, thumbnail generation, SQLite storage
- SyncManager.jsx frontend component with progress bar and error reporting
- Google Drive service account set up and shared with Inspiration Images folder

### The Blocker Left Over
Railway environment variable injection was failing — `GOOGLE_DRIVE_CREDENTIALS` was set in Railway UI but not reaching the Flask app. Left for Day 3.

---

## Day 3 — Infrastructure Fix + Image Grid
*Completed: June 26, 2026*

### What We Built
- ✅ Fixed Railway port mismatch — domain was pointed at port 5000, app runs on 8080. Changed to 8080 in Settings → Networking.
- ✅ Fixed `GOOGLE_DRIVE_CREDENTIALS` env var — the JSON was being truncated when pasted (only `{` was reaching the app). Fixed by generating a fresh single-line JSON via `cat key.json | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)))"` and re-pasting.
- ✅ Sync now works end-to-end — 4 images pulled from Inspiration Images folder with thumbnails
- ✅ Home page image grid — fixed `Home.jsx` which had a hardcoded empty `useState([])` and never called the API. Now fetches from `/api/images` on load and renders a CSS columns masonry grid.
- ✅ Persistent volume — attached Railway volume at `/app/data`, updated `app.py` to store `library.db` at `/app/data/library.db` so database survives redeploys.
- ✅ Service account key rotated — old key was briefly visible in chat, revoked and replaced with new key.

### Code / Files Changed
- `backend/app.py` — DB path changed to `/app/data/library.db`, added `os.makedirs('/app/data', exist_ok=True)`
- `frontend/src/pages/Home.jsx` — Complete rewrite: fetches images from API, renders masonry grid with CSS columns

### Technical Debt / Notes
- Only 4 test images synced so far — full Inspiration Images folder sync needed on Day 4
- `/api/debug` endpoint still in code — remove before production (Day 13)
- `/api/folders` still hardcoded to return "Inspiration Images" — fine for now, clean up Day 13
- Service account key ID rotated June 26 2026 — new key in place
- Thumbnail size: 200x200px JPEG at 85% quality
- `Home.jsx` currently shows images with no click interaction — detail panel is Day 6

### Starting Point for Day 4
**First task:** Sync the full Inspiration Images folder (go to `/sync`, hit Sync Now — should pull all images, not just the 4 test ones).

**Then:** Begin Day 4 work — tag chip search. This requires:
1. Running the Gemini AI tagging pass on all synced images (Day 3's original goal, deferred)
2. Building tag autocomplete UI in the search bar
3. AND/filter chip logic
4. Live-updating results grid

The tagging pass must happen before search is useful — no tags in DB yet. Budget ~$1 Gemini cost for the full library.

---

## Day 4 — Gemini Tagging Pipeline + Search Backend
*Completed: June 26, 2026*
*Status: PARTIALLY COMPLETE — one frontend file away from done*

### What We Built
- ✅ Gemini tagging pipeline built and deployed (with checkpoint-per-image logic)
- ✅ SSE progress stream endpoint working
- ✅ Two-ring sync UI (Drive sync ring → Tagging ring) built in `SyncManager.jsx`
- ✅ Fixed Gemini API version issue — switched from `google-generativeai==0.3.0` → `google-genai==1.16.0`
- ✅ 112 images synced from Google Drive, 97 successfully tagged
- ✅ Autocomplete endpoint built (`/api/autocomplete`, frequency-sorted)
- ✅ AND-filter search endpoint built (`/api/search`)

### What's Left
- ❌ `Home.jsx` not deployed — The new Home.jsx (with search bar, autocomplete dropdown, and chip filtering wired to `/api/search`) was written and provided but never committed to GitHub. The live app is still showing the old masonry-only Home page.
- ❌ Search bar, tag chips, and autocomplete are non-functional as a result

### Technical Debt / Notes
- Backend is fully complete for Day 4 scope
- New `Home.jsx` exists in chat output from Day 4 session but was never committed

### Starting Point for Day 5
1. Open GitHub → `frontend/src/pages/Home.jsx`
2. Replace with the new `Home.jsx` Claude provided in the Day 4 chat (search bar, autocomplete dropdown, chip filtering wired to `/api/search`)
3. Commit → Railway redeploys → test search bar and chip filtering
4. Once confirmed working, Day 4 is fully complete — proceed to Day 5 scope

---

## Day 5 — Thumbnail Upgrade + Full-Res Detail View
*Status: DEPLOYED, waiting for Railway redeploy to complete*

### What We Built
- ✅ Updated `generate_thumbnail()` function: 600px width, quality 75 (was 400x400, quality 85)
- ✅ New endpoint `/api/images/<id>/full` — streams full-resolution images from Drive to browser
- ✅ New endpoint `/api/regenerate-thumbnails` — bulk regenerates all 97 existing thumbnails with new spec
- ✅ Built `ImageDetail.jsx` component: side panel (slides in from right) showing:
  - Full-res image via `/api/images/<id>/full`
  - Caption (from Gemini tagging)
  - All tags grouped by category (Mood, Lighting, Location, etc.) with colored badges
  - Aspect ratio and date added
  - Color palette swatches
  - Favorite and Flag buttons
- ✅ Wired up grid click handlers in `Home.jsx` to open detail panel
- ✅ Added semi-transparent backdrop overlay when detail panel is open
- ✅ Code committed to GitHub — Railway auto-deploying (~3 min total)

### Code / Files Changed
- `backend/app.py` — updated `generate_thumbnail()` with width/quality params, added two new endpoints
- `frontend/src/components/ImageDetail.jsx` — new side panel component (256 lines)
- `frontend/src/pages/Home.jsx` — imported ImageDetail, added state for selectedImageId, wired click handlers

### Next Steps for Day 5 Verification
1. Wait for Railway deployment to complete (~1-2 min from session log time)
2. Test grid: click any image → detail panel should slide in from right with semi-transparent backdrop
3. Verify full-res image loads (may be slightly pixelated if not regenerated yet)
4. Verify metadata displays (caption, tags, aspect ratio, date, palette)
5. Test close button and backdrop click to close panel
6. POST to `/api/regenerate-thumbnails` to start bulk thumbnail rebuild
7. Monitor `/api/sync/status` to see regeneration progress
8. Once regeneration completes, grid thumbnails should appear sharper (600px vs 400px)

### Technical Notes
- `generate_thumbnail()` now resizes to width parameter, maintaining aspect ratio
- `/api/images/<id>/full` streams directly from Drive service account (no browser credentials exposed)
- Regenerate endpoint runs in background thread (non-blocking)
- Detail panel fetches image data from `/api/images?user_id=1` (same call Home.jsx uses)
- Full-res image fetched separately from `/api/images/<id>/full` endpoint
- Detail panel animations: backdrop fade-in (0.2s), panel slide-in-right (0.3s cubic-bezier)

---

## Day 6 + Day 7 — Verification + Full Search Experience (Frame Atlas V5)
*Completed: July 5, 2026*

### Big Discovery: Day 4's Tagging Never Actually Worked
Verification revealed the live database had **zero tags and zero captions** — all 97
images were marked `tagging_status='failed'`. Root cause: **Google retired the
`gemini-2.0-flash` model** (API returns 404 NOT_FOUND). The Day 4 session log's
"97 successfully tagged" was wrong. Fixed by switching to `gemini-2.5-flash`, now
configurable via the `GEMINI_MODEL` env var on Railway so future retirements need
no code change.

### What We Built / Fixed
**Day 5/6 verification:**
- ✅ Confirmed Day 5 commits were never pushed to GitHub — pushed, deploy verified
- ✅ `/api/images/<id>/full` verified live: streams real JPEG from Drive, HTTP 200, 0.7s
- ✅ Fixed regenerate-thumbnails bug: `in_progress` flag never reset → would have
  blocked all future syncs after one regeneration
- ✅ Fixed ImageDetail panel: was fetching from `/api/images` which returns no
  tags/caption/palette — panel would have always been empty. Now receives the
  image object directly from the grid.

**Day 7 features (all deployed):**
- ✅ NL fallback: Enter on unmatched text → `POST /api/interpret` → Gemini maps
  phrase to taxonomy tags → violet dashed quoted chip (hover shows resolved tags).
  Verified live: "something lonely and desperate" → lonely, melancholic, low-key, desaturated
- ✅ Color extraction: `extract_palette()` (Pillow quantize, 5 colors) wired into
  sync worker + thumbnail regen; `POST /api/extract-colors` backfilled all 97 images
  from stored thumbnails in one shot
- ✅ Color filter UI: 12 preset cinematic swatches + custom color wheel (Sidus Link
  style); `/api/search?color=<hex>` matches via weighted RGB distance (threshold 2200)
- ✅ Bookmarks: ☆ button by search bar; save current filters with a name, recall or
  delete from dropdown. `GET/POST /api/bookmarks`, `DELETE /api/bookmarks/<id>`
- ✅ NL groups in search: `nl=` param takes JSON array of tag groups — image must
  match ≥1 tag per group (OR within group, AND between groups/chips)
- ✅ New admin endpoints: `POST /api/tag/start?force=true` (re-tag without sync),
  `GET /api/models` (list usable Gemini models — remove Day 13),
  `/api/tag-progress` now includes `status_counts` + `total_tag_rows`

### Pipeline Run (July 5)
1. ✅ Colors extracted for all 97 images
2. 🔄 Force re-tag of all 97 images started (~$1) — in progress at session write time
3. ⏳ Thumbnail regeneration (600px) queued after tagging completes

### Technical Debt / Notes
- `GEMINI_MODEL` env var: not yet set on Railway (code default `gemini-2.5-flash` active)
- `/api/models` debug endpoint — remove with `/api/debug` on Day 13
- Favorite/Flag buttons in detail panel are still visual stubs (Day 8 wires them)
- Color match threshold (2200, weighted RGB) may need tuning with real usage
- GitHub repo was renamed `frame-atlas` → `Frame-Atlas` (push still works via redirect)
- Ryan's Mac has no Node.js — frontend can only be built by Railway's Docker build

### Starting Point for Day 8
Day 8 = full image detail panel: wire Favorite/Flag buttons to backend, inline tag
edit/remove, filmography display (data exists in DB from tagging), add-to-deck
placeholder. First: confirm tagging + thumbnail regen completed and Day 7 features
verified in browser (NL chip, color filter, bookmarks).

---

## Day 7 — Core Search (Part 2): NL Fallback + Color + Bookmarks
*Completed: July 5, 2026*
*Status: FEATURE-COMPLETE, UX/data-quality issues noted for future*

### What We Built
- ✅ NL fallback: `/api/interpret` — Gemini maps free text (e.g., "lonely and desperate") to 2–5 tags from taxonomy
- ✅ NL chips styled distinctly (violet, dashed border, italic, quoted)
- ✅ Color extraction: `extract_palette()` pulls 5 dominant colors per image via Pillow
- ✅ Color extraction wired into sync worker and thumbnail regeneration
- ✅ `/api/extract-colors` backfill — extracted palettes for all 265 thumbnails in seconds
- ✅ Color search: `/api/search?color=#hex` with weighted RGB distance matching
- ✅ `/api/search` now combines `chips=` + `nl=` (OR-groups) + `color=` in one query
- ✅ Bookmarks CRUD: GET/POST/DELETE `/api/bookmarks` — save/recall filter presets
- ✅ Bookmark UI: ☆ icon by search bar, dropdown to manage bookmarks
- ✅ Color UI: 12 preset cinematic swatches + color wheel picker (Sidus Link inspiration)
- ✅ Home.jsx rewritten (540 lines): search bar, autocomplete, chips, NL phrases, color swatches, bookmarks, masonry grid
- ✅ Sync: 168 new images from Drive (265 total)
- ✅ Tagging: 74 images tagged (23 failures, recoverable)

### User Feedback (Verified in Browser — Noted for Future)
- ❌ Photos cropped in masonry — should display full aspect ratio (not force-fit to row)
- ❌ Color filter too loose — blue search returns non-blue images (threshold 2200 too high)
- ❌ Aspect ratios weird (80:43, 23:16) — should normalize to standard (16:9, 4:3, 2:1, 1:1, 9:16)
- ❌ Only 246 tagged images; many under-tagged — tagging prompt too conservative, needs to be generous
- ✅ NL fallback works (tested "something lonely and desperate" → interpreted correctly)
- ✅ Bookmarks save/load (tested full round-trip)
- ✅ Color swatches display and filter (threshold tuning needed)
- ✅ Detail panel opens, shows image + tags/palette

All feedback captured in `/memory/day7-feedback.md` for Day 8 or polish phase.

### Code / Files Changed
- `backend/app.py` — color extraction, NL interpret, bookmarks, search multi-filter
- `frontend/src/pages/Home.jsx` — full rewrite (540 lines)
- `frontend/src/components/ImageDetail.jsx` — detail panel

### Technical Debt / Notes
- Color threshold (2200) too loose → lower to ~800–1200
- Tagging prompt conservative → retag with relaxed rules for better discoverability
- Aspect ratio rounding deferred (nice-to-have, not critical)
- Favorite/Flag buttons UI-only (backend endpoints not yet written)

### Starting Point for Day 8
1. Tune color threshold down from 2200 → 800
2. Wire Favorite/Flag buttons (POST `/api/toggle-favorite`, `/api/toggle-flag`)
3. Inline tag editing in detail panel
4. Filmography display (title/director/DP/year for film stills)

---

## Day 7 (Part 2) — Final Polish: Vibrance Palettes, True Masonry, Infinite Scroll
*Completed: July 5, 2026*
*Status: FEATURE-COMPLETE, PRODUCTION-READY*

### What We Built

**Backend Improvements:**
- ✅ Vibrance-weighted palette extraction — colors now scored by (area × saturation) instead of just pixel count, so vivid colors rank higher than muddy backgrounds. Fixes the "gems-on-dark-background get averaged into sludge" problem.
- ✅ Reduced palette from 15 → 10 colors per image (8 vivid + 2 neutral). Color search checks top 6 slots, now leans harder on truly dominant colors.
- ✅ Thumbnail quality upgraded: 600px/quality-75 → 800px/quality-85; no upscaling of small sources (stays native size).
- ✅ Aspect ratio normalization: raw ratios like 80:43 now display as nearest standard format (16:9, 2.39:1, etc.); original ratio preserved in DB for layout math.
- ✅ Tagging prompt rewritten to be generous: instead of "tag only the most obvious," now "tag everything that plausibly applies, aim for 12–25 tags per image."
- ✅ Color search threshold rebalanced: top 6 colors, threshold 1000 (was top 2 @ 1500).
- ✅ `/api/extract-colors?force=true` now supports backfilling all palettes at once.
- ✅ Debug endpoints: `/api/debug/failed-images` (lists failed tags), `/api/tag/retry-failed` (retags only failed images, cheaper than force=true).

**Frontend Improvements:**
- ✅ True masonry columns (Pinterest-style) — every image displays full aspect ratio, zero cropping. Column count adjusts to viewport width (2–5 columns).
- ✅ Infinite scroll — loads 60 images at a time, auto-fetches next batch as you approach the bottom. Counter shows "246 images · 60 loaded" to indicate progressive loading.
- ✅ Palette moved above tags in detail panel — more prominent placement.
- ✅ Aspect ratio label in detail panel shows normalized format with raw ratio in parens (e.g., "1.85:1 (645:706)").
- ✅ Frontend build verified locally (Node.js installed, npm build succeeds).

**Pipeline Results:**
- ✅ Thumbnails regenerated: 246/246 done, 0 errors.
- ✅ Full re-tag run: 233/246 succeeded, 13 initially failed.
- ✅ Retry of failed images: 7/13 succeeded on second attempt (transient API errors). 6 persistently failed (likely corrupted files: IMG_4706.JPG, Spectre_37/36/38.jpg, 25 (463).jpg, 12 (463).jpg).
- ✅ Palette re-extraction with vibrance logic: all 246 done. Verified on test images:
  - Mickey poster: 9 colors (orange, yellow, ink-blue, teal all captured)
  - Motel night: 8 colors (dark blue, neon warm tones, lime green)
  - Red-shirt: 7 colors (red #d22a35, greens all captured)

### Technical Notes
- **Gemini model:** Production still using `gemini-2.5-flash` (env var `GEMINI_MODEL` set by Railway).
- **Total tags across library:** 7,262 tag rows (avg. ~31 tags per image under generous prompt).
- **Failed images:** 6 persistently fail during tagging; user can search Drive and delete or re-export.
- **Day 8 scope added:** Upload (via OAuth sign-in), Delete (move to _Removed subfolder), Download (full-res to Downloads folder). See `/memory/day8-scope-additions.md`.

### Decisions Made (Confirmed with Ryan)
- ✅ Palette extraction: Vibrance-weighted over Gemini color-naming (simpler, faster, no re-tag cost).
- ✅ Neutrals in palette: Keep a few, ranked last (so all-gray images still show colors, but vivid images lead).
- ✅ Grid layout: True masonry (no letterboxing, no forced rows).
- ✅ Pagination: Infinite scroll (stays performant, loads 60 at a time).
- ✅ Re-tag scope: Full library (all 246), not just failed/sparse images (ensures consistency).
- ✅ Upload auth: Real Google OAuth (act as user, uploads owned by user) — requires Day 8 setup time.

### Files Changed
- `backend/app.py` — vibrance extraction, palette params, debug endpoints, retag logic
- `frontend/src/pages/Home.jsx` — true masonry, infinite scroll, responsive columns
- `frontend/src/components/ImageDetail.jsx` — palette above tags, normalized AR label
- Added Node.js locally for frontend verification

### Deferred
- Remove debug endpoints (`/api/debug/*`) — flagged for Day 13 cleanup.
- Retry permanently-failed images — user to investigate Drive files first.

### Starting Point for Day 8
1. Set up Google Cloud OAuth credentials (consent screen, client ID, redirect URIs for Railway domain).
2. Wire Google sign-in flow on frontend.
3. Implement `/api/upload` — accepts multipart file, saves to Drive, creates library entry, triggers thumbnail + extract-colors.
4. Implement `/api/images/<id>/delete` — moves file to _Removed folder, deletes from library.
5. Wire `/api/images/<id>/download` — proxies full-res from Drive to browser.
6. Implement Favorite/Flag toggle buttons (already UI-stubbed in detail panel).
7. Inline tag editing in detail panel.
8. Test all workflows end-to-end.

Frame Atlas V6 is now stable. Upload/delete/download + detail panel wiring are the Day 8 blockers.

---

## Day 8 (Parts 2 + 3) — Google OAuth Upload + Filmography (Frame Atlas V7 complete)
*Completed: July 6, 2026*
*Status: DAY 8 COMPLETE (deck placeholder intentionally skipped until Day 10)*

### What We Built

**Part 2 — Google Sign-In + Upload (code was pre-written, shipped + tested this session):**
- ✅ Google OAuth sign-in: `/api/auth/google/login`, `/api/auth/google/callback`, `/api/auth/status`. Uses `drive.file` scope (app only sees files it creates). Token stored in `users` table, auto-refreshes.
- ✅ `/api/upload` — multipart upload, perceptual-hash duplicate check BEFORE writing to Drive (warn + "Upload anyway" via `force=true`), then thumbnail + palette + auto-tagging trigger.
- ✅ `UploadButton.jsx` — ⬆ button by search bar; signed-out click routes to Google sign-in, signed-in click opens file picker; results modal shows uploaded/duplicate/error per file.
- ✅ **Critical fix — ProxyFix:** Railway terminates HTTPS at its proxy, so Flask saw `http://` and built an http redirect URI that Google rejected (`redirect_uri_mismatch`). Fixed with werkzeug `ProxyFix(x_proto=1, x_host=1)`. Applies to ANY future absolute-URL generation on Railway.
- ✅ Race fix: upload button ignores clicks until the auth-status check resolves (was opening file picker for signed-out users in the first second after page load).

**Part 3 — Filmography (built this session):**
- ✅ The tagging pipeline had been writing title/director/DP/year to the `filmography` table since Day 4 — but nothing ever read it. Now `/api/search` returns a `filmography` object per image.
- ✅ Title card in detail panel (above caption, gold-tinted): "Her (2013) · dir. Spike Jonze · DP Hoyte van Hoytema".
- ✅ Title, director, and DP are clickable → closes panel, adds a teal 🎬 filter chip, grid shows only matching frames.
- ✅ `film=` param on `/api/search` — exact match (case-insensitive) wins; substring fallback only when nothing matches exactly (fix: "her" was also returning every "Christop**her** Nolan" film).
- ✅ `POST /api/images/<id>/filmography` — set or clear film info. Detail panel has Edit (fix wrong AI guesses), "Not a film / wrong" (clear), and "+ Add film info" (images with no data).
- ✅ Film filter works with bookmarks (saved/applied/shown in dropdown preview) and the filter counter.

### End-to-End Tests (all passed live)
- Sign-in round trip → token stored, `signed_in: true`
- Duplicate upload (renamed copy of library image) → caught by phash, refused with reference to original
- New upload → landed in Drive, thumbnail + palette + 28 tags generated
- Download → full 5.2MB original served back from Drive
- Delete → moved to `_Removed`, gone from library (also proves Editor grant works)
- Filmography: 49/246 images have film data (Her ×5, Spectre ×4, Tenet ×3, Tokyo Story ×3…)
- Clicking "Spike Jonze" → exactly his 5 frames
- Set/clear/restore filmography via API → all worked

### Bonus
- The 5 images that permanently failed tagging in Day 7 succeeded during this session's tagging run — library is now fully tagged.

### Decisions Made (Confirmed with Ryan)
- ✅ Filmography placement: title card at top of detail panel (above caption)
- ✅ Wrong AI guesses: editable + clearable (not read-only)
- ✅ Names clickable → film search filter
- ✅ Add-to-deck placeholder: skipped entirely — real button arrives with the deck system on Day 10

### Technical Debt / Notes
- `.gitignore` added this session (repo previously had none — node_modules/dist/pycache were untracked noise)
- `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` env vars confirmed set and working on Railway
- Debug endpoints (`/api/debug*`, `/api/models`) still flagged for Day 13 removal
- Local Mac Python (3.14) can't build Pillow — backend can only be fully run on Railway; `python3 -m py_compile` used for syntax checks locally
- Git committer identity is the default auto-generated one (Ryan may want `git config --global user.name/email` at some point)

### Files Changed
- `backend/app.py` — OAuth routes, upload, ProxyFix, filmography in search + film= param + edit endpoint
- `frontend/src/components/UploadButton.jsx` — new component
- `frontend/src/components/ImageDetail.jsx` — filmography title card + edit mode
- `frontend/src/pages/Home.jsx` — UploadButton wiring, film filter chip/state/bookmarks
- `.gitignore` — new

### Commits
`512a84f` (OAuth+upload) → `b4b5898` (ProxyFix) → `4f64498` (button race) → `540ed5f` (filmography) → `1ee245a` (exact-match film search)

### Starting Point for Day 9
Day 9 = CLIP + Similar Images:
1. Write one-time local Python script to generate CLIP embeddings for all images (NOTE: local Python 3.14 can't build Pillow — may need pyenv/homebrew Python 3.12, or run embedding generation on Railway instead)
2. Store vectors in SQLite `embeddings` table (table already exists)
3. Add CLIP embedding step to sync pipeline for new images
4. `/api/images/<id>/similar` — cosine similarity + tag overlap combined score
5. "Find Similar" button on detail panel → ranked results grid

---

## Day 9 — CLIP Fingerprints + Find Similar (Frame Atlas V8 complete)
*Completed: July 6–7, 2026*
*Status: DAY 9 COMPLETE — verified live on two separate images in browser*

### What We Built

**Local Python unblock:**
- Installed Homebrew `python@3.12` (local default is 3.14, which can't build Pillow). Created a project-local virtual environment at `scripts/.venv` (gitignored) with torch, open_clip, Pillow, requests, and the full backend `requirements.txt` — this also unlocked *local backend testing* for the first time (previously backend could only be syntax-checked, never actually run, on this Mac).

**CLIP fingerprinting (`scripts/generate_embeddings.py`):**
- Model: `ViT-L-14-quickgelu` / `openai` (the plain `ViT-L-14` tag mismatches the original OpenAI weights and was corrected mid-session — seed file is keyed by model name so a mismatch regenerates cleanly).
- Downloads all 246 thumbnails from the live site, fingerprints them (768-dim vectors, L2-normalized), writes `backend/embeddings_seed.json.gz` (~450KB).
- Incremental: skips images already fingerprinted; exits in seconds if nothing new.
- Downloads the 1.7GB model only when there's new work, deletes it when done (`--keep-model` flag to skip deletion) — zero standing disk cost, zero manual cleanup.

**Backend (`backend/app.py`):**
- `load_embeddings_seed()` — runs on every boot, loads the seed file into the `embeddings` table, idempotent (skips rewrite if already in sync).
- `GET /api/images/<id>/similar?limit=40` (frontend calls with `limit=60`) — combined score = 70% cosine similarity on CLIP vectors + 30% tag overlap, pure Python (no numpy added to requirements). Returns full image objects (same shape as `/api/search`, via a new shared `build_image_dict()` helper) plus a `similarity` field. 404 `no_embedding` for images not yet fingerprinted.

**Frontend:**
- "≈ Find Similar" button in `ImageDetail.jsx`, violet-tinted to match the new similarity theme.
- `Home.jsx`: `similarTo` mode replaces the grid with ranked results, shows a removable violet "≈ Similar to *filename*" chip, similarity % badge per tile, dismissible banner if an image has no fingerprint yet. Entering similar mode clears all other filters; setting any other filter exits similar mode. Bookmarks intentionally never reference `similarTo`.

**Automation (fully hands-off per Ryan's request):**
- `scripts/update_fingerprints.sh` + macOS `launchd` job (`~/Library/LaunchAgents/com.frameatlas.fingerprints.plist`) — runs every Monday 10am: fingerprints any new images, auto-commits + pushes `embeddings_seed.json.gz` if changed (which redeploys Railway). Log at `scripts/fingerprints.log`.
- `launchd`'s `StartCalendarInterval` catches up missed runs automatically next time the Mac wakes/boots — confirmed with Ryan this is sufficient, no "run on wake" trigger needed.

**Permissions:** Added an allowlist to `.claude/settings.json` (curl, preview/Chrome browser tools, project file edits, the venv's python/pip) so Ryan isn't prompted for routine actions — explicitly requested this session to reduce friction.

### Bug Found + Fixed This Session
Initial ship had a state-batching race: `handleFindSimilar` cleared other filters (new array/state references) which changed `fetchPage`'s identity and re-fired the filters `useEffect` — but `similarTo` wasn't set until *after* the async `/similar` fetch resolved, so the effect's guard saw `similarTo === null`, didn't block, and fired a stray `/api/search` that silently overwrote the similar results with the default grid a moment later. Fixed by setting `similarTo` **synchronously** in `handleFindSimilar`, in the same render as the other filter clears. Verified live in Chrome on two different source images — network requests show clean `full → similar` only, no stray `search` call, and each click produces genuinely different, sensibly-ranked results (e.g. clicking a night-interior "Her" frame surfaced other lone-figure/night-window/city-skyline shots at 76–84% match).

### Decisions Made (Confirmed with Ryan)
- ✅ CLIP model: ViT-L/14 (large) over ViT-B/32 — better at subtle mood/lighting cues.
- ✅ Score blend: 70% visual / 30% tag overlap.
- ✅ Results UI: main grid + removable filter chip (same pattern as the film filter), not a panel strip or modal.
- ✅ New-image fingerprinting: local catch-up script, fully automated via `launchd` (not a slim server-side model, not manual/deferred) — Ryan explicitly wanted zero ongoing management.
- ✅ Model lifecycle: download-on-demand, auto-delete after each run — Ryan does not want to "keep it on the laptop forever."
- ✅ Missed Monday runs: `launchd`'s built-in catch-up-on-wake is sufficient; no extra "on wake" trigger wanted.

### Technical Debt / Notes
- **Flagged to Ryan, unresolved:** the git remote URL (`git remote -v`) has a GitHub personal access token embedded in plaintext. Should be rotated and switched to a credential helper / `gh auth` at some point. The `launchd` auto-push depends on this working non-interactively — if the token is rotated, re-test `scripts/update_fingerprints.sh` manually once.
- Debug endpoints (`/api/debug*`, `/api/models`) still flagged for Day 13 removal.
- Git committer identity is still the machine-default auto-generated one (`Ryan Hoang <ryanhoang@Ryans-MacBook-Air.local>`) — same note as Day 8, still unaddressed.
- `scripts/.venv/`, `scripts/.model_cache/`, and `scripts/fingerprints.log` added to `.gitignore`.
- GitHub repo was renamed/moved to `github.com/ryhodp/Frame-Atlas` — pushes still succeed (git follows the redirect) but worth updating the remote URL eventually to avoid relying on the redirect.

### Files Changed
- `backend/app.py` — seed loader, `build_image_dict()` helper, `/api/images/<id>/similar`
- `backend/embeddings_seed.json.gz` — new, 246 fingerprints
- `frontend/src/components/ImageDetail.jsx` — Find Similar button
- `frontend/src/pages/Home.jsx` — similar mode state/effects/chip/badges + race fix
- `scripts/generate_embeddings.py` — new, local fingerprint generator
- `scripts/update_fingerprints.sh` — new, weekly autopilot runner
- `scripts/test_similar_locally.py` — new, local end-to-end test harness (patches DB_PATH, runs Flask test client against real live data)
- `.gitignore` — added `scripts/.venv/`, `scripts/.model_cache/`, `scripts/fingerprints.log`
- `.claude/settings.json` — permissions allowlist (not committed — project-local Claude Code config)
- `~/Library/LaunchAgents/com.frameatlas.fingerprints.plist` — new, outside the repo (macOS scheduler config)

### Commits
`0a7b701` (Day 9: CLIP fingerprints + Find Similar) → `c917cd8` (race condition fix)

### Starting Point for Day 10
Day 10 = Tag Mode + Smart Co-occurrence Suggestions:
1. Toggle Tag Mode from main UI
2. Multi-select: individual clicks, box-select, select all in current results
3. Bulk apply: type or pick tag → applied to all selected instantly
4. Bulk remove: shared tags across selection shown → click X to remove from all
5. Custom tag creation on the fly
6. Smart co-occurrence suggestions panel (pure SQL math, free)

---

## Day 10 — Tag Mode + Smart Co-occurrence Suggestions (Frame Atlas V9 complete)
*Completed: July 7, 2026*
*Status: DAY 10 COMPLETE — verified live: click-select, real drag-select, apply/remove with confirm, suggestions*

### What We Built

**Backend (`backend/app.py`):**
- Refactored `CAT_COLORS`/`CAT_LABELS` (category → color/display-name for the 15 tag categories) from being defined inline inside `autocomplete()` to module-level constants, shared by all the new endpoints.
- `GET /api/tag-categories` — fixed list of all 15 categories for the category picker dropdown.
- `POST /api/tags/bulk-apply` — validates category/value/image_ids, check-before-insert per image (dedup), returns `{applied, already_had, invalid_ids}`.
- `POST /api/tags/bulk-remove` — single parameterized DELETE across the selection, returns `{removed}`.
- `POST /api/tags/selection-summary` — aggregates tag usage across a set of image_ids (`count/total` per tag), sorted by count desc.
- `POST /api/tags/suggestions` — pure-SQL co-occurrence (no AI cost): takes the top 5 tags already common in the selection as "seeds," finds what else co-occurs with those seeds library-wide, excludes tags already on every selected image, returns top 12.

**Frontend:**
- Tag Mode toggle button (header, next to upload/bookmark icons) — tertiary-green highlight when active.
- Click-to-select and **drag-to-select** on the masonry grid: mousedown/mousemove/mouseup on the grid container, 4px threshold to distinguish a click from a real drag, hit-tests each tile's `getBoundingClientRect()` against the drag rectangle, unions into the existing selection. A `justDraggedRef` flag (cleared on a 0ms timeout) stops the trailing click-after-drag from double-toggling the tile under the cursor.
- New `frontend/src/components/TagModeBar.jsx` — fixed bottom toolbar: selection count, Select-all-loaded, Clear selection, Exit; Apply Tag panel (name autocomplete + required category dropdown, per Ryan's explicit choice to always show category rather than infer it silently); Shared Tags panel (chips with `N/total`, click × to remove); Suggestions panel (dashed chips, click to stage into Apply Tag, does not apply immediately).
- Every bulk apply/remove goes through a confirm-step modal first ("Add/Remove X to/from N images?") per Ryan's choice — no full undo history, just a pre-action guard.

### Bug Found + Fixed This Session
The selection checkmark badge (bottom-left on selected tiles) was overlapping the last line of the image caption and the first color-palette swatch. Moved to top-right, offset clear of the existing star/flag icons. Confirmed fixed with a zoomed screenshot after redeploy.

### Testing Notes
- Built `scripts/test_tagmode_locally.py` (same local-server-against-real-data pattern as Day 9's similar-image test) — 10/10 checks passed before deploying: category list, selection-summary sorting, suggestions excluding fully-applied tags, bulk-apply idempotency, bad-category/empty-ids validation (400s), invalid image id handling, bulk-remove + confirmation.
- Live browser verification found that the `left_click_drag` browser-automation tool doesn't fire intermediate `mousemove` events, so it couldn't trigger the app's drag-select on its own — confirmed via a manual JS-dispatched mousedown→mousemove(×10)→mouseup sequence that the real app logic works correctly for an actual physical drag. Not an app bug, just a testing-tool limitation worth remembering for future UI verification.

### Decisions Made (Confirmed with Ryan)
- ✅ Category picker: always shown/required when applying a tag, never silently inferred (even for autocomplete matches — pre-fills as a convenience default, but stays editable).
- ✅ "Select all" scope: only the currently loaded/visible images, not the full filtered set.
- ✅ Shared tags display: show any tag used by the group with a count (`N/total`), not just the strict intersection.
- ✅ Safety net: confirm-before-applying modal, no full undo history.

### Technical Debt / Notes
- Same unresolved items as Day 9: git remote token in plaintext, git committer identity still machine-default, debug endpoints still flagged for Day 13.
- `decks`, `scenes`, `deck_images` tables already exist in the schema (created early in the project) but have zero endpoints yet — Day 11 is pure backend+frontend build-out, no migration needed.

### Files Changed
- `backend/app.py` — CAT_COLORS/CAT_LABELS refactor, 5 new tag-mode endpoints
- `frontend/src/components/TagModeBar.jsx` — new, selection toolbar
- `frontend/src/pages/Home.jsx` — Tag Mode state, click/drag selection, badge fix
- `scripts/test_tagmode_locally.py` — new, local test harness

### Commits
`9f8122d` (Day 10: Tag Mode) → `446372a` (badge position fix)

### Starting Point for Day 11
Day 11 = Decks + Scenes:
1. Deck CRUD: create, rename, delete
2. Add images to deck from any search or browse view
3. Scene creation within a deck
4. Drag images between scenes
5. Deck view: collapsible scene sections, dense grid per scene

---

## Day 11 — Decks + Scenes (Frame Atlas V10 complete)
*Completed: July 10, 2026*
*Status: DAY 11 COMPLETE — full lookbook workflow verified live end-to-end, library left clean*

### What We Built

**Backend (`backend/app.py`) — 11 new endpoints under `# DECKS + SCENES`:**
- Deck CRUD: `GET/POST /api/decks`, `PATCH/DELETE /api/decks/<id>` (delete cascades: deck_images → scenes → deck).
- `GET /api/decks/<id>` — full detail: deck info, ordered scenes list, and a FLAT list of every deck-membership row (each with its own `deck_image_id`, `scene_id` null = Unsorted, full image data via a new `_fetch_image_dict()` wrapper around the shared `build_image_dict()`). Frontend groups into sections itself.
- Scene CRUD: `POST /api/scenes` (auto sort_order), `PATCH/DELETE /api/scenes/<id>` (delete removes the scene's deck_images rows too — Ryan's choice).
- `POST /api/decks/<id>/images` — bulk add to Unsorted, deduped within Unsorted, returns `{added, already_in_deck, invalid_ids}`.
- `POST /api/deck-images/<id>/move` — THE move-vs-copy endpoint. Branching: same-scene drop → no-op (guard added in review: without it an accidental 2px drag would duplicate a photo inside its own scene); target Unsorted → move; out of Unsorted → move; scene-to-scene → **copy** (new row, original untouched — Ryan's choice so one shot can sit in two scenes). Returns `{action: "moved"}` or `{action: "copied", new_deck_image_id}`.
- `DELETE /api/deck-images/<id>` — removes one membership row without touching other copies.

**Frontend:**
- `Header.jsx` REBUILT — was untouched Day 1 skeleton (white Tailwind bar clashing with the dark app since forever). Now dark-theme inline styles, "Frame Atlas v10", Home/Decks/Sync nav with gold active state. Version label now on-screen per the V-naming convention.
- `App.jsx` — routes for `/decks` and `/decks/:id`; app-level wrapper switched from leftover `bg-gray-50` Tailwind to the dark design-system background (this was the one live bug found in browser testing: the new pages rendered on white because only Home painted its own background).
- New `pages/DecksPage.jsx` — deck card grid (2×2 preview collage, photo count), inline "+ New Deck" (creates then navigates straight in), per-card delete with confirm modal ("photos themselves stay in your library" reassurance).
- New `pages/DeckDetail.jsx` — always-visible Unsorted section + collapsible per-scene sections, dense contact-sheet grids (`repeat(auto-fill, minmax(110px,1fr))`, square tiles), click-to-rename deck and scenes, "+ New Scene" inline, per-tile × remove, HTML5 drag-and-drop between sections with drop-target highlight. After any drop it just refetches the deck (sidesteps branching on moved-vs-copied client-side).
- `TagModeBar.jsx` — new "Add to Deck" panel beside Apply Tag: lazy-loads deck list, click a deck to add the selection (no confirm — purely additive), or type a name in "+ New deck…" to create-and-add in one step. Success message auto-clears.

### Live End-to-End Verification (all API calls 200, zero failures)
Tag Mode select 2 → create "Test Lookbook" via Add to Deck → deck card correct on /decks → photos in Unsorted → created scene "Opening" → dragged photo in (moved, not copied — Unsorted count went 2→1) → renamed scene to "Act One" via click-to-rename → deleted scene via confirm (its photo left the deck, Unsorted copy untouched) → deleted deck via confirm → `GET /api/decks` returns `[]`, library clean.

### Testing Notes
- `scripts/test_decks_locally.py` — 17 checks, all passed pre-deploy (CRUD, dedupe, all four move/copy branches incl. the same-scene no-op, scene-delete cascade sparing other copies, deck-delete cascade, validation 400s/404s).
- Reconfirmed: browser-automation `left_click_drag` can't trigger HTML5 drag-and-drop (no dragstart/drop events) — verified the real handlers with a JS-dispatched DragEvent + DataTransfer sequence instead. Same class of tool limitation noted on Day 10.
- One test-harness bug during the session was in the TEST not the app (asserted sorted-order shape wrong); the debug pass proved the backend correct.

### Decisions Made (Confirmed with Ryan)
- ✅ Adding photos to decks: reuse Tag Mode's selection toolbar (no separate flow).
- ✅ New photos land in an "Unsorted" holding area, sorted into scenes later.
- ✅ Scene-to-scene drag COPIES (photo can live in two scenes); Unsorted↔scene drags MOVE.
- ✅ Deleting a scene removes its photos from the deck (not preserved to Unsorted).

### Technical Debt / Notes
- Same standing items: git remote token in plaintext (flagged Day 9, unresolved), committer identity machine-default, debug endpoints slated for Day 13 removal.
- `share_token` column on decks and `storyboard_order`/`storyboard_note` on deck_images exist but are unused — they're Day 12's storyboard/share-link scope, already in place schema-wise.
- `SyncManager.jsx` still uses old Tailwind-style classes internally — now reachable via the new Sync nav link; cosmetic mismatch, low priority.

### Files Changed
- `backend/app.py` — 11 deck/scene endpoints + `_fetch_image_dict()` helper + same-scene no-op guard
- `frontend/src/components/Header.jsx` — rebuilt, dark theme + nav
- `frontend/src/App.jsx` — new routes, dark app-level wrapper (bug fix)
- `frontend/src/pages/DecksPage.jsx` — new
- `frontend/src/pages/DeckDetail.jsx` — new
- `frontend/src/components/TagModeBar.jsx` — Add to Deck panel
- `scripts/test_decks_locally.py` — new, 17-check local harness

### Commits
`a916251` (Day 11: Decks + Scenes) → `cb49c3d` (dark background fix)

### Starting Point for Day 12
Day 12 = Storyboard Mode + Obsidian Export:
1. Storyboard mode within a scene: drag images into specific order (uses the existing `storyboard_order` column)
2. Add text note to each image in the sequence (uses the existing `storyboard_note` column)
3. Obsidian markdown export: deck → `.md` file with images as URL embeds pointing to the app's thumbnail server
4. Read-only share link per deck (token-based, no login — `share_token` column already exists)
Done when: can sequence 10 images with notes, export `.md`, drop into Obsidian, see images render inline.

---

## Day 12 — Storyboard Mode + Share Links (Frame Atlas V11 complete)
*Completed: July 11, 2026*
*Status: DAY 12 COMPLETE — storyboard, notes, and share links all verified live; Obsidian export CANCELLED by Ryan*

### Scope Change
Ryan cancelled the Obsidian markdown export at session start. Day 12 became:
storyboard mode + per-frame notes + read-only share links.

### What We Built

**Backend (`backend/app.py`) — under new `# STORYBOARD + SHARE LINKS` section:**
- `POST /api/deck-images/<id>/note` — set/clear a frame's storyboard note (trims whitespace; empty string clears to NULL).
- `POST /api/decks/<id>/reorder` — persists a new order for one section (scene, or Unsorted when `scene_id` is null). Requires the COMPLETE ordered list of that section's deck_image_ids — partial lists, ids from the wrong section, and junk are all rejected with 400s. Position in list becomes `storyboard_order`.
- `POST /api/decks/<id>/share` — mints a share token (`secrets.token_urlsafe(16)`) or returns the existing one (idempotent). `DELETE` revokes it; a later re-share mints a NEW token (revoked links can never be revived).
- `GET /api/share/<token>` — public read-only deck payload, no login. Thumbnails only (they're data URIs in the payload); no full-res/edit/delete exposure.
- Refactor: `get_deck()` internals extracted into `_deck_payload()` shared by owner view and share view so the two JSON shapes can't drift. Deck GET now returns images **in storyboard order** (unordered rows last) plus `share_token` and `storyboard_note` per image.

**Frontend:**
- New `components/StoryboardView.jsx` — full-screen overlay per scene: numbered frame cards, drag-onto-a-card to reorder (inserts at that position), always-visible auto-saving note textarea under each frame, Saving…/Saved ✓/Save failed indicator, ESC or Done to close (parent refetches deck only if something changed). Textarea blocks the parent card's drag so text selection still works.
- New `pages/SharePage.jsx` — public lookbook at `/share/<token>`: "FRAME ATLAS · SHARED LOOKBOOK" branding, deck title, scenes in order with numbered frames + notes, Unsorted shown last as "More Frames" (only if non-empty). Clean error state for invalid/revoked links.
- `pages/DeckDetail.jsx` — "⊞ Storyboard" button on every non-empty section; Share button by deck title (turns gold "🔗 Shared" when a link is active); ShareModal with create/copy/revoke (revoke has an inline confirm step).
- `App.jsx` — restructured with inner `Shell` component (for `useLocation`); `/share/:token` route renders WITHOUT the app header.
- `Header.jsx` — version label bumped to v11.

### Testing
- `scripts/test_storyboard_locally.py` — 8 checks, all passed pre-deploy: payload fields, scene + Unsorted reorder round-trips, reorder validation (partial/wrong-section/junk/missing-deck), note set/trim/clear/validation, scene-to-scene copy carrying the note, share create/idempotency/public-fetch, revoke + fresh-token-on-reshare.
- Live browser verification: typed a real note (saved, 200), reordered frames via JS-dispatched DragEvents (order persisted to DB and reflected in deck grid + share page), created share link, viewed public page (no header, correct order, note visible), revoked, confirmed dead link shows "This link isn't active". Test deck deleted afterward — library left clean.
- **New tooling lesson:** JS-dispatched DragEvent sequences must have ~100ms delays between events — fired synchronously, React state from `dragstart` hasn't flushed when `drop` reads it, so the drop silently no-ops. Real drags are unaffected (events arrive in separate tasks).

### Decisions Made (Confirmed with Ryan)
- ✅ Obsidian export: cancelled entirely.
- ✅ Storyboard UI: full-screen view per scene (not an in-page toggle, not whole-deck).
- ✅ Notes: always visible under each frame in storyboard view (not click-to-open, not shown in normal deck grid).
- ✅ Share view: full presentation INCLUDING notes.
- ✅ Share quality: thumbnails only — viewers get no full-res access.

### Technical Debt / Notes
- Share links are protected by the unguessable token, but the whole app is loginless until Day 14 — the share feature's privacy model only fully lands once auth exists.
- Same standing items: git remote token in plaintext (Day 9), committer identity machine-default, debug endpoints (`/api/debug*`, `/api/models`) slated for Day 13 removal.
- `storyboard_order` is only compacted per-section on reorder; rows moved between scenes keep their old number until their new section is reordered (harmless — deck GET breaks ties by row id).

### Files Changed
- `backend/app.py` — 4 new endpoints, `_deck_payload()` refactor, ordered deck GET
- `frontend/src/components/StoryboardView.jsx` — new
- `frontend/src/pages/SharePage.jsx` — new
- `frontend/src/pages/DeckDetail.jsx` — Storyboard buttons, Share button + modal
- `frontend/src/App.jsx` — Shell restructure, /share route (headerless)
- `frontend/src/components/Header.jsx` — v11
- `scripts/test_storyboard_locally.py` — new, 8-check local harness

### Commits
`3347018` (Day 12: Storyboard Mode + Share Links)

### Starting Point for Day 13
Day 13 = Analytics + Utility Views:
1. Analytics dashboard: tag frequency heatmap, source type breakdown, mood distribution, location spread, time-of-day distribution, library growth over time
2. Recently Added strip (images from last sync, on home view)
3. Favorites view (all starred images)
4. Flagged queue (all flagged images, clearable)
5. Cleanup flagged since Day 3: remove debug endpoints (`/api/debug*`, `/api/models`)
Done when: dashboard loads with real data; Recently Added shows last sync's images.

---

## Day 13 — Analytics + Utility Views (Frame Atlas V12 complete)
*Completed: July 11, 2026*
*Status: DAY 13 COMPLETE — analytics dashboard, Recently Added, Favorites, Flagged all verified live*

### Scope Decisions (Confirmed with Ryan, pre-coding)
- ✅ Charts: hand-built (SVG growth chart + CSS bar lists), no charting library added.
- ✅ Recently Added: last 7 days, any source (sync or upload) — not strictly "last sync," since a sync can add zero images and uploads should count too.
- ✅ Favorites/Flagged: dedicated nav pages, not just grid toggles. Clearing a flag only unflags — never deletes.
- ✅ Debug cleanup: `/api/debug` and `/api/debug/failed-images` removed as planned. `/api/models` kept on purpose — it's the diagnostic that first caught the Gemini model retirement, and exposes nothing private.

### What We Built

**Backend (`backend/app.py`) — under new `# DAY 13 (V12): ANALYTICS + UTILITY VIEWS` section:**
- `GET /api/analytics` — one-call rollup: headline totals (images, favorites, flagged, added this week, tags, distinct tags, decks), tag counts grouped by category, and library growth by month (added + running total).
- `GET /api/views/<favorites|flagged|recent>` — filtered image lists in the same rich payload shape as `/api/search`. `recent` takes `?days=` (default 7) and an optional `?limit=`.
- `POST /api/flags/clear-all` — unflags everything in one call. Never deletes images.
- Refactor: pulled the tag/palette/filmography hydration out of `/api/search` into a new `hydrate_image_rows()` helper, shared by `/api/search` and the new views so their payloads can't drift apart (same pattern as Day 12's `_deck_payload()`).
- Removed `/api/debug` and `/api/debug/failed-images`. Re-documented `/api/models` as a kept-on-purpose diagnostic rather than a stray debug route.

**Frontend:**
- New `pages/AnalyticsPage.jsx` — stat cards, hand-built SVG line/area growth chart, a tag-frequency heatmap (chip size/brightness scales with usage), and four bar-list panels (mood, location, time-of-day/weather, source type).
- New `pages/CollectionPage.jsx` — one shared component driving both `/favorites` and `/flagged` via a `view` prop; same masonry grid as Home, opens the existing `ImageDetail` panel. Flagged view adds a per-tile "Clear flag" button and a header "Clear all flags" action with an inline confirm step.
- `pages/Home.jsx` — Recently Added strip (horizontal scroll, last 7 days via `/api/views/recent`) above the main grid; hides while filtering, in Find Similar mode, or in Tag Mode. Refreshes after uploads.
- `components/Header.jsx` — added Analytics/Favorites/Flagged nav links; version bumped to v12.
- `components/ImageDetail.jsx` — "Find Similar" footer button now only renders when `onFindSimilar` is passed (CollectionPage doesn't wire it up, since there's no search grid to repopulate there).

### Testing
- `scripts/test_analytics_locally.py` — 8 checks, all passed pre-deploy: favorites/flagged/recent view correctness (right images, full search-shaped payload), recent's day-window + limit params, unknown-view 404 + junk-param safety, analytics totals/category-counts/growth math, clear-all-flags (unflags without deleting, idempotent), debug-route removal with `/api/models` still routed, and a regression check that `/api/search` (incl. AND-filter chips) still works after the hydration refactor.
- Existing Day 11 + Day 12 harnesses (`test_decks_locally.py`, `test_storyboard_locally.py`) re-run clean — no regressions from the shared-hydration refactor.
- Live verification against the deployed site: confirmed `/api/debug` now 404s and `/api/analytics`, `/api/views/*`, `/api/models` are all live; pulled the real analytics payload (246 images, 7657 tags, 2-point growth curve, correct top tags per category); browsed `/analytics`, `/`, and `/favorites` in-browser and confirmed correct rendering; ran a full curl round-trip on a live image — favorited it (appeared in Favorites), unfavorited (cleanup), flagged it (appeared in Flagged), ran clear-all (queue emptied). Library left clean, no stray state.

### Technical Debt / Notes
- Same standing items: git remote token in plaintext (Day 9, unresolved), committer identity machine-default.
- Browser-automation click coordinates didn't reliably hit the custom (non-semantic) grid tile `div`s during live verification — same class of tool limitation noted on Days 9/10/12. Used curl against the live API for the favorite/flag round-trip instead, which is more reliable and exercises the real endpoints anyway.
- Analytics growth chart currently buckets by month only — fine at current volume (246 images across 2 months), may want week-level granularity once the library spans many months.

### Files Changed
- `backend/app.py` — `/api/analytics`, `/api/views/<view>`, `/api/flags/clear-all`, `hydrate_image_rows()` helper, `/api/search` refactored to use it, debug routes removed
- `frontend/src/pages/AnalyticsPage.jsx` — new
- `frontend/src/pages/CollectionPage.jsx` — new (shared Favorites/Flagged)
- `frontend/src/pages/Home.jsx` — Recently Added strip
- `frontend/src/components/Header.jsx` — nav links, v12
- `frontend/src/components/ImageDetail.jsx` — optional Find Similar button
- `scripts/test_analytics_locally.py` — new, 8-check local harness
- `CLAUDE.md` — API endpoint list updated for Day 13

### Commits
`c75f8dc` (Day 13: Analytics + Utility Views)

### Starting Point for Day 14
Day 14 = Multi-User Auth (Shared Library Model):
1. Username/password login system
2. Admin account (Ryan) + additional user accounts
3. Each user has their own: decks, scenes, favorites, flags, bookmarked searches
4. All users search the same shared image library
5. Admin controls: add/remove users
Done when: a friend can log in, search the library, build a lookbook, and share it — without seeing Ryan's private decks.

---

## Days 14–15 — Shuffled Feed + Aspect-Ratio Search + My Work Tags (Frame Atlas V14–V15 complete)
*Completed: July 14, 2026*
*Status: BOTH FEATURES LIVE AND VERIFIED*

### Scope Decisions (Confirmed with Ryan, pre-coding)

**V14 (Shuffled Home Feed):**
- ✅ Per-visit deterministic shuffle using a seed (Date.now() at page load)
- ✅ Shuffle applies only to the default unfiltered home view — any search/filter reverts to newest-first
- ✅ Recency weighting: images viewed in the last 7 days sort below unseen ones, so fresh inspiration surfaces first
- ✅ Pagination stays stable during a visit: view logging happens only on page exit (visibilitychange / unmount), not mid-scroll, so the shuffled order never shifts while scrolling

**V15 (Aspect-Ratio Search):**
- ✅ Standard format buckets: search snaps all images to the 11 nearest standard cinematography formats (9:16, 2:3, 3:4, 4:5, 1:1, 4:3, 3:2, 16:9, 1.85:1, 2:1, 2.39:1)
- ✅ Type-in the search bar: "9:16", "2.35", "2.35:1", or aliases like "scope", "anamorphic", "vertical", "portrait", "square"
- ✅ Autocomplete suggests format buckets with live image counts; only buckets with images appear
- ✅ Picks as a teal filter pill, combines with other filters (AND logic)

**V15 (My Work Role Tags):**
- ✅ New `my_work` tag category for Ryan's own projects (gaffed / DP'd / photographed)
- ✅ Human-applied only — AI tagger never writes this category
- ✅ Re-tag safety: `clear_ai_tags()` wipes all AI-written tags on re-tag but preserves manual categories (`my_work`, `misc`)
- ✅ Bulk-appliable via Tag Mode (drag-select + apply), also single image in detail panel
- ✅ My Work shown first in the detail panel (moved to top of `CAT_ORDER`)
- ✅ Searchable as a chip like any other tag, shows in analytics

### What We Built

**Backend (`backend/app.py`):**

V14 section (Shuffled Feed):
- New `image_views` table: tracks per-user `last_seen_at` and `seen_count` (UPSERTed on `/api/views/log`)
- `shuffle_key(seed, image_id)` function using `zlib.crc32()` for deterministic shuffle math
- `/api/search` now accepts optional `?seed=` param; when present and no other filters active, orders by (recently-seen-flag, shuffle_key) instead of date_added DESC
- `/api/views/log` (POST): upserts image_views rows; frontend batches viewed image IDs and flushes on tab-hide/page-leave via keepalive fetch

V15 section (Aspect-Ratio Search + My Work):
- `STANDARD_ASPECT_RATIOS` constant + `normalize_ar_label()` function (using log-distance metric to symmetrically round to nearest format)
- `ar_float_from_str()` helper: shared parser for stored AR strings ("80:43", "2:39:1", "2") — ensures display buckets and search filters always agree
- `AR_QUERY_ALIASES` dict: plain-English shorthand ("scope" → "2.39:1", "vertical" → "9:16", etc.)
- `ar_query_labels(q)` function: returns matching standard-format labels for a search query (ratio parsing + alias lookup + substring matching on labels)
- `/api/search` new `?ar=` param: Python-side scan of user's images, snap each to its bucket, collect matching IDs, inject into WHERE clause
- `/api/autocomplete` extended: if query looks like a ratio, scan images, count buckets, return non-empty AR matches in the dropdown with `type: 'ar'`
- `MANUAL_TAG_CATEGORIES` constant: `('misc', 'my_work')` — human categories never deleted on re-tag
- `clear_ai_tags(cursor, image_id)` function: deletes image's tags except those in MANUAL_TAG_CATEGORIES
- New `my_work` category added to `CAT_LABELS` and `CAT_COLORS` (gold accent #d9a441)

**Frontend:**

V14:
- `shuffleSeedRef`, `viewObserverRef`, `seenIdsRef`, `pendingViewsRef` in Home.jsx
- IntersectionObserver on each grid tile: counts as "seen" when at least 50% visible
- flushViews callback + visibilitychange + cleanup unmount: sends batched view IDs to `/api/views/log` on exit only
- fetchPage dependency includes seed param when no filters + `!ar`
- Bookmarks save/restore `{ ..., ar }` state

V15:
- Home.jsx: new `ar` state + selectAr handler, renders as teal pill, included in clearAll/hasFilters/fetchPage
- autocomplete dropdown: new case for `opt.type === 'ar'`, renders as "▭" icon + label + "Aspect Ratio" label
- Bookmark summary line includes AR chip if present
- ImageDetail.jsx: `CAT_ORDER` reordered to put `my_work` first; dropdown pick from selector shows new category

**Testing:**
- `scripts/test_shuffle_locally.py` — 8 checks: no seed = newest-first preserved, seed = full shuffle + deterministic, pagination stitches seamlessly, different seeds = different orders, filters ignore seed, view log upserts/counts/rejects-foreign, recency demotion works, 7-day window boundary
- `scripts/test_v15_locally.py` — 7 checks: ar_query_labels regex/alias/substring matching, autocomplete AR suggestions with counts (no empty buckets), /api/search ar= filter per-bucket membership, AR + chips AND together, AR filter disables shuffle, my_work bulk-apply + search, clear_ai_tags preserves my_work/misc
- Both harnesses pass locally; frontend compiles clean; V14 regression suite re-runs clean
- Live verification: typed "scope" in search bar → teal "▭ 2.39:1" pill appeared + correct images returned; typed "vertical" → "▭ 9:16" pill + right subset; "gaffed" + "2.35" together + returned AND intersection; all filters clear together

### Technical Debt / Notes
- Standing item: git remote plaintext token (Day 9, unresolved), machine-default committer
- V14 commit (`c28720e` "V14: Shuffled home feed") was already committed/pushed at 1:46 AM (likely weekly autopilot job) before this session — verified it contained the exact right files and was live on production before V15 work started
- Design: V14's 7-day recency window is hard-coded; fine for now, could be configurable in Settings later if the refresh cadence changes

### Files Changed
- `backend/app.py` — V14 section (view log, shuffle helpers, seed param), V15 section (AR matching, my_work category, clear_ai_tags, CAT_* updates)
- `frontend/src/pages/Home.jsx` — V14 view tracking + shuffle seed wiring, V15 ar state + selectAr + AR pill rendering + bookmark state
- `frontend/src/components/ImageDetail.jsx` — V15 CAT_ORDER reorder (my_work first), my_work in CAT_LABELS
- `scripts/test_shuffle_locally.py` — V14 harness, 8 checks
- `scripts/test_v15_locally.py` — V15 harness, 7 checks
- `CLAUDE.md` — updated endpoint docs for seed/ar/views-log, Gemini re-tag safety notes

### Commits
- `c28720e` (V14: Shuffled home feed)
- `61f950b` (V15: Aspect-ratio search + My Work role tags)

### Starting Point for Next Session
Inbox features:
1. **Saved searches** — bookmarks currently just save filter state; could add a "run this search monthly" or export feature
2. **Deck improvements** — scenes could have durations/timecode; decks could export to a timeline view for editorial
3. **Personal libraries** (Day 17 from old plan) — each friend gets their own Drive folder + Gemini key, sees only their own images + shared library
4. **Mobile responsive** — current layout hasn't been tested on tablet/phone
5. **Admin invite codes** — already built (Day 14) but untested live

Pick based on what Ryan wants next. No known bugs. All live features verified end-to-end.

---

## Day 16 — Per-User Gemini Keys + Connect Guide Screenshots (Frame Atlas V16 complete)
*Completed: July 15, 2026*
*Status: DAY 16 COMPLETE — per-user Gemini keys live, all endpoints tested, guide screenshots added*

### Scope Decisions (Confirmed with Ryan, pre-coding)
- ✅ Gemini API key storage: per-user in `users.gemini_api_key` column (non-admin users only); admin continues using shared `GEMINI_API_KEY` env var
- ✅ Key marked optional throughout UI — "(optional)" label in light gray, error messages graceful when users lack a key
- ✅ Monthly spend tracking: SQLite `gemini_usage` table (user_id, month, input_tokens, output_tokens, cost_usd) with UNIQUE constraint
- ✅ Pricing: hardcoded `gemini-2.5-flash` rates (0.30/M input tokens, 2.50/M output tokens)
- ✅ Tagging job: refactored to group images by owner, use each owner's key (admin uses shared, non-admins use saved key)
- ✅ NL search (interpret endpoint): switched to per-user key instead of shared admin key
- ✅ Connect guide page: 4-step mockups for Google Drive OAuth flow, graceful fallback if screenshots missing

### What We Built

**Backend (`backend/app.py`):**
- `GEMINI_PRICING` dict: pricing per model (currently `gemini-2.5-flash` hardcoded)
- `gemini_usage` table: UNIQUE(user_id, month) constraint for monthly cost aggregation
- `get_model_pricing(model_name)` function: returns input/output rates for pricing calculation
- `get_user_gemini_key(user_id)` function: returns admin's shared env key for user 1, otherwise fetches user's saved `gemini_api_key` from DB
- `record_gemini_usage(user_id, usage_metadata, model_name=None)` function: calculates cost from tokens + pricing, upserts into `gemini_usage` table
- Refactored `_run_tagging_job()` and `trigger_tagging()`: now accept optional `user_id` param; inner job groups images by owner, gets each owner's key, skips owners without keys
- New `/api/account/gemini-key` (GET/POST): non-admin users save/retrieve their key, never returns full key (only last 4 chars with asterisks)
- New `/api/tag/mine` (POST): non-admin users trigger tagging for only their own images using their own key
- New `/api/tag-progress/mine` (GET): non-admin users poll tagging progress for their own images
- New `/api/billing/spend` (GET): returns this month's estimated cost; errors if user lacks a key
- Updated `/api/interpret` (NL search): uses `get_user_gemini_key()` instead of env key, records usage, returns clear error if user lacks key

**Frontend:**
- `components/AccountPage.jsx` updated: new "YOUR GEMINI API KEY" Step 4 section (non-admin only)
  - Input field with "(optional)" label in light gray
  - Save button with status display ("✓ Key saved")
  - "Tag my photos" button with progress polling
  - "Need help? →" link pointing to `/account/connect-guide`
- New `pages/ConnectGuidePage.jsx`: 4-step guide for Google Drive OAuth
  - Each step: numbered circle, title, description, screenshot placeholder
  - Graceful fallback for missing images (no broken-image icons)
  - Back to Account link at top
  - Contact message at bottom for stuck users
- `pages/SettingsPage.jsx` rewritten with hooks:
  - New "GEMINI SPEND" section showing this month's estimated cost
  - Displays "$X.XX USD" in gold with date range ("Month 1–DD, YYYY")
  - Error message if user lacks key: "Add your Gemini API key in Account settings to track your spend."
- `pages/Home.jsx` updated: added `nlError` state for NL search failures
  - Displays error message below search bar if `/api/interpret` returns error
  - Clears when user types in search box
- `App.jsx`: added route `/account/connect-guide` → `ConnectGuidePage`

**Screenshots:**
- 4 mockup images generated via Cowork (Google OAuth flow walkthrough):
  - `step1-connect-button.png` — Frame Atlas Account page with the gold "Connect Google Drive" button
  - `step2-google-signin.png` — Google's standard sign-in screen
  - `step3-permission-screen.png` — Google's permission consent screen listing Frame Atlas's required scopes
  - `step4-back-in-app.png` — Back in Frame Atlas with green checkmark, success message, and "Choose Folder" button
- Stored at `frontend/public/guide-images/` so ConnectGuidePage can load them

**Testing:**
- `scripts/test_gemini_keys_locally.py` — 14 smoke tests, all passed:
  - Admin resolves to shared key, keyless friend gets None
  - GET shows no key initially, save/GET works
  - `get_user_gemini_key()` works for both admin and non-admin
  - Blank key rejected
  - Admin's billing works, keyless friend gets error
  - `record_gemini_usage()` tallies correctly with secondary runs accumulating (not overwriting)
  - Admin can't use `/api/tag/mine`, keyless friend can't tag or use NL search
  - All routes require login
- Harness re-run of Day 15 tests (shuffle, v15 AR/my_work) — all clean, no regressions
- Live browser verification:
  - Logged in as non-admin friend
  - Saved fake Gemini key ("sk-test-xxx…key123")
  - Verified "✓ Key saved" status appeared
  - Verified "Tag my photos" button appeared with progress
  - Checked Settings spend section displays correctly
  - Verified guide page renders without broken images

### Technical Debt / Notes
- Standing item: git remote plaintext token (Day 9, unresolved), machine-default committer
- V16 feature relies on user manually inputting a Gemini key — future improvement could auto-detect via OAuth or configuration wizard
- Spend calculation is an estimate based on token counts and fixed pricing; actual Gemini API bills may vary slightly due to caching or model variants

### Files Changed
- `backend/app.py` — GEMINI_PRICING, gemini_usage table, new functions + endpoints
- `frontend/src/pages/AccountPage.jsx` — Gemini key section + Tag my photos button
- `frontend/src/pages/ConnectGuidePage.jsx` — new, 4-step guide with screenshot slots
- `frontend/src/pages/SettingsPage.jsx` — rewritten with hooks, Gemini spend section
- `frontend/src/pages/Home.jsx` — nlError state + display
- `frontend/src/App.jsx` — /account/connect-guide route
- `frontend/public/guide-images/` — 4 mockup PNG files (step1–step4)
- `scripts/test_gemini_keys_locally.py` — new, 14-check local harness
- `scripts/run_local_for_browser_check.py` — pre-built during earlier context (seeds two test users)

### Commits
- `26efd93` (V16: Per-user Gemini API keys + connect guide) — earlier context
- `a381a6a` (V16: add Google Drive OAuth guide mockup screenshots) — this session

### Starting Point for Next Session
**Fully functional features verified:**
- Non-admin friends can save Gemini keys and tag their own images
- Monthly spend tracked and displayed in Settings
- NL search errors gracefully when user lacks key
- Guide page helps new users set up Google Drive OAuth
- All endpoints tested; no regressions from Days 14–15

**Inbox for future days:**
1. **Personal libraries** (Day 17 original plan) — each friend's own Drive folder + Gemini key
2. **Mobile responsive** — test on tablet/phone
3. **Crew management** — invite teams of cinematographers to work on shared lookbooks
4. **Offline mode** — cache favorite decks locally
5. **Admin analytics** — see per-user activity, key spend aggregation

No known bugs. All live features verified end-to-end. Ready for next feature when Ryan decides.

---

## Day 16 (cont'd) — Fly.io Cancelled, V17 Confirmed Shipped, Loose Ends Queued
*Session: July 16, 2026*
*Status: PLANNING/HOUSEKEEPING SESSION — no new features built here*

### What Happened This Session
- Reviewed where things stood coming off V16 (per-user Gemini keys + connect guide).
- Confirmed V17 (Personal Libraries) had already shipped, in a parallel session — commit `e4673bc` ("V17: Personal libraries — friends sync their own Drive folder") was already on `main` at the start of this session. `CLAUDE.md` already fully documents the V17 architecture (service-account-based folder sync, per-user isolation, setup checklist). Timeline doc updated to reflect this as COMPLETE.
- Ryan picked "Day 16 (Fly.io) + Day 17 (Personal Libraries)" as the session goal, then — once reminded Day 17 was already done — cancelled Day 16 outright: Fly.io killed its free tier for new accounts in October 2024, so a small always-on app there now costs roughly the same as Railway. The ~$60/year savings the original Day 16 plan was built around no longer exists. **Decision: stay on Railway indefinitely. The next real infrastructure move is Day 18 (NAS migration) once the Ugreen hardware is ready — no Fly.io work planned before then.**
- Updated `/Docs/2_Frame_Atlas_Build_Timeline.md`: Day 16 marked CANCELLED with the reasoning above; Day 17 marked COMPLETE with a note that the actual build diverged from the original OAuth plan (ended up as service-account sync, because `drive.file` OAuth scope can't see a friend's pre-existing files — see [[drive-file-scope-picker-limitation]] in memory).

### Background Task (Parallel Session, Started by Ryan)
- A separate session (`task_05a44549`, "Fix test harnesses broken by Day 14 login gate") ran concurrently with this one. Cause: `scripts/test_analytics_locally.py`, `test_decks_locally.py`, `test_storyboard_locally.py` all used to seed their test data by pulling real images from the live production site over a plain unauthenticated request — since Day 14 login-gated the whole app, that request now fails, so all three harnesses had been silently broken since Day 14. Fix: all three now generate synthetic JPEG test images locally with Pillow instead of depending on the live site.
- **Status at end of this session: changes are on disk but UNCOMMITTED.** `git diff --stat`: 3 files changed, +80/-62. Needs review + commit next session (or sooner, if Ryan wants it done now).

### Loose Ends Found This Session (Not Touched)
- `Frame Atlas.html` — untracked, 354KB, repo root, dated June 25, titled "Bundled Page." Not part of the documented file structure (the real design reference lives at `/docs/Frame_Atlas.html`). Likely a stray export — flagged for Ryan to confirm before deleting.
- `scripts/test_auth_locally.py.bak_check` — untracked, 0 bytes, dated July 11. Empty debris file, same treatment: flagged, not deleted.
- Standing unresolved items carried forward, unchanged this session: git remote has a plaintext GitHub token embedded (Day 9), git committer identity is still machine-default (Day 8).

### Starting Point for Next Session
1. Review and commit the login-gate test-harness fixes from `task_05a44549` if not already committed.
2. Confirm the two stray files above are safe to delete, then remove them.
3. Pick from the open inbox (carried from Day 16/V16): mobile responsive pass, admin per-user analytics, crew/team management for shared lookbooks, offline deck caching, a refreshed connect-guide screenshot set for the new V17 share-folder flow.
4. Day 18 (NAS migration) stays parked until hardware is ready — no Fly.io or other infra work before then.

---

## V20 — Admin Per-User Analytics Dashboard
*Completed: prior session*
*Status: COMPLETE — admin can see per-user activity, storage, key spend*

(Built and verified in a prior session. Provides analytics page with per-user metrics.)

---

## V21 — Mobile Responsive Layout
*Completed: prior session*
*Status: COMPLETE — hamburger nav, 2-col grids, touch-friendly panels*

(Built and verified in a prior session. App now responsive on mobile/tablet.)

---

## V22 — Background Upload Progress with Persistent Badge
*Completed: July 17, 2026*
*Status: COMPLETE — upload % visible, modal auto-closes, tagging continues in background*

### What We Built

**Frontend (`frontend/src/components/UploadButton.jsx`):**
- Replaced fetch with XMLHttpRequest to track upload progress via `xhr.upload.addEventListener('progress')`
- Display upload percentage in real-time with a progress bar in the modal
- Auto-close modal 1.5 seconds after successful upload so user can browse while syncing/tagging happens
- Results still visible briefly before modal closes

**Frontend (NEW: `frontend/src/components/UploadProgressBadge.jsx`):**
- Small persistent header badge showing current work phase
- Listens to `/api/tag-progress/stream` (SSE) for live updates
- Shows three phases with animated icons:
  - 🔄 **Syncing from Drive** (with % if available)
  - ⚡ **Tagging images** (with % progress)
  - Auto-undismisses when new work starts
- Dismiss button (×) hides badge but work continues — reappears on new uploads
- Only visible when work is running

**Frontend (`frontend/src/pages/Home.jsx`):**
- Import UploadProgressBadge and add to header row above search bar
- Non-blocking, always accessible

### Design Decisions
- ✅ Upload progress tracked via XMLHttpRequest upload events (more reliable than fetch)
- ✅ Modal auto-closes after upload so user can keep browsing (doesn't block)
- ✅ Badge is dismissible but re-appears on new work (not intrusive, still informative)
- ✅ Backend already had async tagging via threads, so no backend changes needed
- ✅ SSE stream (`/api/tag-progress/stream`) already existed and powers the badge

### Testing
- Built locally, deployed to Railway
- Verified build succeeds (no syntax errors)
- No backend changes required (async tagging was already in place)

### Technical Notes
- Upload progress uses XMLHttpRequest's native `progress` event (fires on every chunk)
- Badge connects to existing `/api/tag-progress/stream` SSE endpoint (no new backend work)
- Auto-close timing (1.5s) gives user time to see results before modal disappears
- Tagging continues even if user closes browser (backend-driven async job)

### Files Changed
- `frontend/src/components/UploadButton.jsx` — XMLHttpRequest progress tracking, auto-close modal
- `frontend/src/components/UploadProgressBadge.jsx` — new persistent progress badge
- `frontend/src/pages/Home.jsx` — add badge to header

### Commits
- `751aff4` (V22: Background upload progress with persistent badge)

### Starting Point for Next Session
All upstream features complete. Ready for next feature from the inbox:
1. **Crew management** — invite teams to collaborate on shared lookbooks
2. **Offline deck caching** — cache favorite decks locally
3. **Additional mobile polish** — refine responsive breakpoints, test on real devices
4. **Day 18 (NAS migration)** — when Ugreen hardware is ready

No known bugs. All features verified end-to-end.

---

## V23 — Crew Collaboration + Offline Caching
*Completed: July 17, 2026*
*Status: COMPLETE — public share links + offline deck caching both shipped*

### What We Built

**Crew Collaboration (Backend):**
- Added `permission` column to `deck_members` table (viewer/editor model)
- Added `updated_at` timestamp to decks for change tracking
- New endpoints:
  - `POST /api/decks/<id>/share` — create/get shareable link with permission level
  - `DELETE /api/decks/<id>/share` — revoke share link
  - `POST /api/decks/join/<token>` — join deck via public link (creates deck_members row)
  - `check_deck_permission()` helper for permission enforcement
  - Updated `/api/decks/<id>/members` to return permission level
- Frontend ShareModal + MembersModal already existed and fully wired; just needed backend support
- Crew members can now be invited to view shared decks via public links

**Offline Caching:**
- New `useOfflineCache` hook: IndexedDB-based deck storage
  - `cacheDeck()` — store deck locally when loaded
  - `getCachedDeck()` / `getCachedDecks()` — retrieve cached data
  - `clearCache()` / `removeCachedDeck()` — cache management
  - `hasRemoteUpdates()` — detect server-side changes
- **DeckDetail.jsx updates:**
  - Auto-cache deck on load to IndexedDB
  - Detect if remote `updated_at` > cached timestamp
  - Show "New changes" banner with Refresh button
  - Clicking Refresh refetches latest from server
- **SettingsPage.jsx cache management:**
  - Show count of cached decks
  - "Clear Cache" button to delete all local storage
  - Informational text about offline access
- All decks automatically cached on first view
- Works fully offline — can browse cached deck structure without server connection
- Auto-syncs when back online (server remains source of truth)

### Design Decisions (Confirmed with Ryan)
- ✅ Crew permissions: Simple model (Viewer or Editor)
- ✅ Invites: Sharable links (public, anyone with link can join as Viewer)
- ✅ Cache scope: Thumbnails + metadata only (no full-res images)
- ✅ Sync strategy: "New changes" banner, manual refresh (not live WebSocket)
- ✅ Cache management: Settings panel with clear option

### Testing
- Frontend build: ✓ successful
- Backend syntax: ✓ verified
- Both features deployed to production via Railway auto-deploy

### Files Changed
- `backend/app.py` — migrations (permission + updated_at), new share endpoints, permission helpers
- `frontend/src/hooks/useOfflineCache.js` — new, IndexedDB cache management
- `frontend/src/pages/DeckDetail.jsx` — auto-caching, "New changes" detection, refresh banner
- `frontend/src/pages/SettingsPage.jsx` — cache management UI

### Commits
- `cf4f81e` (V23: Crew collaboration — shareable links with permission model)
- `0ace6c3` (V23: Offline deck caching with IndexedDB + sync detection)

### Next Steps
- Test crew sharing live: create share link, open in incognito, join as viewer
- Test offline caching: load a deck, go offline, verify it still loads; modify online, see "New changes" banner
- Test Settings cache: verify count updates, clear cache works

All core collaboration + offline features now complete. App is now shareable with cinematographer crews and works without internet access.

---

## Deploy Troubleshooting + Hobby Tier Upgrade
*Completed: July 27, 2026*
*Status: COMPLETE — app back online after billing cap hit*

### The Problem
V26 code shipped successfully on July 26, but the app never deployed. Build logs showed:
- Initialization: ✓ passed
- Build: ✓ passed (14 seconds)
- Deploy: ✗ "Deployment failed" — Deploy stage never started

### Root Cause
**Not** the serverless flag (was already ON). The real blocker: Free tier hit its $1/month usage cap.

| Tier | Monthly Grant | Usage to Date | Status |
|---|---|---|---|
| Free | $1.00 | $1.34 | **Over budget** |
| — daring-light (Frame Atlas) | — | $1.33 | — |
| — adorable-youthfulness | — | $0.01 | — |

Railway suspends deployments when over budget. The build succeeded, but the container couldn't start because there was no budget to run it on.

### Resolution
Ryan upgraded to Hobby tier ($5/month), which:
- Lifts the budget cap (includes $5 monthly usage credit)
- Restores always-on capability (background sync/tagging jobs won't be interrupted by container sleep)
- Note: Serverless remains ON by design (Railway required it); app can still sleep after idle periods and wake in ~2–3 seconds

Redeploy triggered immediately after upgrade. App came online ~40 seconds later.

### Lessons
1. **I misdiagnosed twice:** First said "Free is enough, 6,000–8,000 images will fit" (measured storage, never checked compute budget). Then saw the serverless error and almost flipped the flag OFF, which would have broken things — I checked it was already on before touching it. Third attempt I finally measured: Free's $1/month doesn't cover an app that syncs, tags with AI, and serves images.

2. **The error message was misleading:** "Free plan deployments must be serverless" was Railway's correct rule, but it masked the real blocker (budget exhaustion). The build log never mentioned money — just "deployment failed" → easy to misread as a code problem.

3. **Database vulnerability:** All organizational work (tags, decks, scenes, bookmarks) lives in a single SQLite file on Railway's volume with no other copy. Images are safe on Drive, but months of tagging work is unprotected. User asked "why backup if Drive has images?" — answer: Drive has the *assets*, the database has the *work*.

### Files Changed
- `Docs/.gitignore` — added Test Photos/ to prevent 36 MB of screenshots from bloating every build (bc6c09c)

### Commits
- `bc6c09c` (Stop tracking Test Photos/ — local-only regression harness reference images)

### Current State
- ✅ V26 crop detection engine live (12/14 test images correct; 2 conservative)
- ✅ Backup-abort safety for crops shipped
- ✅ App online, accessible at frame-atlas-production.up.railway.app
- ⏳ Database still has no export mechanism (not blocking, but risk remains)

### Next Steps
1. Test V26 crop detection on live images (new Redetect semantics, MAD-based detection)
2. Verify _Removed backup works with real Google Drive connection
3. Build database export endpoint if user wants off-site copy

Session complete. App operational and ready for feature work.

## V26 — Crop Detection Engine Rewrite (MAD-Based)
*Completed: July 26, 2026*
*Status: COMPLETE — 2/14 → 12/14 correct on real test images; deployed to production*

### The Problem
The original crop detector (ported from CropStudio_v34) scored **2 out of 14** on Ryan's real cinematography screenshots. It asked "is this line dark or light enough to be chrome?" which failed symmetrically:
- **Over-cropped** on dark artwork (black picture on Instagram background looked like pillarbox → cut away thirds of the frame)
- **Under-cropped** on white matting (one-sided trim logic left scrollbar residue because it only dropped the *brightest* pixels, useless for a white mat with a dark scrollbar)
- Same image could fail in both directions at once

### What We Built

**Frontend (`frontend/src/cropDetectV2.js`):**
- New single-statistic detection: **median absolute deviation per line**. Chrome = flat, regardless of color.
- Key invariants:
  - Threshold MAD ≤ 0 strictly (anything looser eats real dark picture edges)
  - Three-pass algorithm: full-width rows → content-row columns → content-column rows
  - Peeling is conservative — stops at first non-flat line, no gap-bridging (under-crop is one Tighten press away; over-crop is permanent picture loss)
- Auto-tighten rewritten on MAD with **two independent guards:**
  1. Edge-restriction: only peeled edges can be tightened
  2. Terminal condition: if flat run reaches 2% cap without ending, return 0 (signals picture, not residue)
- Old `cropDetect.js` deleted; v34 source recoverable via git history

**Frontend (`frontend/src/components/CropModal.jsx`):**
- Wired to `detectCropTightened()` instead of old `detectCrop()`
- Redetect semantics **inverted**: each press now strips MORE (v34 used to strip less)

**Backend (`backend/app.py`, `crop_image()` endpoint):**
- Before overwriting the Drive file, back up originals to `_Removed` folder
- Backup filename: `{stem} (pre-crop {timestamp}).{ext}`
- Uses user's OAuth client for backup write (has storage quota), service account for _Removed lookup
- **Failed backup aborts the crop** — never silently proceeds without the safety net
- Error messages distinguish: "Editor access required" vs. "storage quota exceeded"

**Test Coverage (`scripts/test_crop_locally.py`):**
- Expanded from basic endpoint test to **23 comprehensive checks** covering:
  - Happy path: crop succeeds, Drive file updated in-place, DB row unchanged, thumbnail refreshed
  - Backup creation: exactly one file created, landed in _Removed, carried original bytes, marked as pre-crop
  - Abort-on-failure: failed backup → error returned, Drive file untouched, no crop executed
  - Error branches: Editor access missing vs. quota exceeded vs. no OAuth connected
- Added `FakeUserDrive` class (simulates user OAuth with storage quota)
- All 23 checks pass

**Visual Regression Harness (`scripts/crop_regression.html`):**
- Contact sheet view of all 14 test images with green crop boxes
- Shows tighten delta per edge (should be 0 on nearly everything; >10 signals regression)
- MAD sweep column (tests looser thresholds)
- Baseline boxes documented in header comment
- Run locally: `python3 -m http.server 8971`, then open the script

**Documentation (`CLAUDE.md` V26 section):**
- ~50 lines covering why v34 failed, MAD rationale, threshold guards, three-pass ordering, peeling conservatism, auto-tighten dual guards, two-client backup architecture, regression harness location, confidence label unreliability

### Results
**Baseline: 2/14 correct (v34)**
- Flex 3, IMG_1063: over-cropped
- IMG_1068, IMG_1081, cropped_IMG_5530: severe over-crop (lost picture to sides)
- IMG_1074, IMG_1076, IMG_1078: under-cropped (scrollbar residue left behind)
- IMG_6846, IMG_6848: over-cropped
- Tokyo Story, IMG_1243: partial (expected to fail)

**After MAD rewrite: 12/14 correct**
- All 12 above failures fixed
- Tokyo Story: still no crop (thin white line at top remains — conservative, not destructive)
- IMG_1243: exact picture bounds after tighten (48,55,1194,777)

### Design Decisions (Confirmed with Ryan)
- ✅ Replace v34 entirely — proven failure, not salvageable
- ✅ Back up originals to `_Removed` before crop — destructive ops need undo built-in
- ✅ Abort on failed backup — safety net that doesn't work defeats its purpose
- ✅ MAD-based single statistic — simpler, more robust than multi-factor brightness logic

### Testing & Deployment
- Frontend build: ✓ successful
- Backend tests: ✓ all 23 checks pass
- Regression harness: ✓ loads locally, baseline boxes match
- Deployed to Railway via git push; auto-deploy in progress

### Files Changed
- `frontend/src/cropDetectV2.js` — new, MAD-based detection engine
- `frontend/src/components/CropModal.jsx` — wired to new detector
- `backend/app.py` — backup system, abort-on-failure, error message routing
- `scripts/test_crop_locally.py` — 23 checks, happy + error paths, backup/abort testing
- `scripts/crop_regression.html` — new visual harness for ongoing verification
- `CLAUDE.md` — V26 section documenting engine, invariants, guards, architecture
- `frontend/src/cropDetect.js` — deleted (recovered via `git show 10316f3:...`)

### Commits
- `c877c48` (V26: Replace crop detection engine with MAD-based algorithm)

### Known Limitations (Not Blocking)
- **Confidence labels:** Currently unreliable (dark artwork reads "low" even on perfect crops). Not treated as a safety signal; marked for future rework.
- **Tokyo Story edge case:** Thin white line at top remains undetected. Conservative (keeps picture), not destructive. User can press Redetect to tighten manually if desired.

### Next Steps
1. **Live testing (Ryan):** Run a batch of the test images through the crop UI in production. Eye-verify at full resolution. Report any surprises.
2. **Backup safety check:** Try one throwaway image end-to-end to confirm _Removed backup works with real Google Drive connection.
3. **Polish:** Rework confidence labels if needed based on live feedback.

---

## V29 — Duplicate Detection: Color-Overlap Check
*Completed: July 27, 2026*
*Status: COMPLETE — deployed to production*

### The Problem
Ryan opened the Duplicate Review screen and found groups of photos that look nothing alike to a human (e.g. an industrial dock shot, a lit alley, and a night street scene all grouped as "near duplicates"). Root cause: the duplicate checker's perceptual hash (phash) only reads brightness LAYOUT — it shrinks a photo to a 9x8 grid and records whether each pixel is brighter than its right-hand neighbor. Two completely different photos sharing the same rough "dark frame, bright patch in the middle" shape hash almost identically, regardless of actual color or content.

### What We Built
**Backend (`backend/app.py`):**
- New `palettes_overlap()` check (reuses the hue-based color-closeness logic already built for V24 color search) — requires two photos' actual color palettes to overlap by at least 50% (on both sides), ignoring near-black/white/gray entries which carry no hue information and would otherwise make any two dark photos "match."
- Wired into all three places a duplicate gets decided:
  1. Admin Duplicate Review scan (`find_duplicates()`)
  2. Live upload/clip duplicate check (`_ingest_image()`, shared by `/api/upload` and `/api/clip`)
  3. `duplicates_scan()` now also backfills any missing color palette (from the stored thumbnail, no Drive download needed) before comparing — same as it already did for phash — so the color check has real data to work with instead of silently deferring to phash alone.
- Graceful degradation preserved: if either photo has no real color signal (true black & white, or palette not extracted yet), the color check steps aside and phash alone decides — matching the pattern V24 already used for pre-V24 rows.

**Testing (`scripts/test_duplicate_color_check_locally.py`, new):**
- Permanent regression test: builds two images with an identical brightness split but different colors, proves phash alone would call them duplicates (reproducing the original bug's precondition), then confirms the fix rejects them while still catching same-color near-duplicates (simulated resize/recompress) on both the upload path and the admin scan.
- Existing `test_v25_clip_locally.py` re-run and still passes — no regression to upload/clip duplicate handling.

### Design Decisions (Confirmed with Ryan)
- ✅ Add a color-overlap check on top of phash, rather than just tightening the phash threshold (a tighter threshold alone couldn't distinguish "same layout, different color" — it's not the kind of error a brightness-only hash can fix by itself)
- ✅ Fix all three duplicate-decision code paths, not just the review screen, since the live upload check had the identical bug
- ✅ Backfill missing palettes in the scan (not just phash) so the new check isn't silently skipped for older photos
- ✅ Add a permanent regression test rather than only spot-checking manually

### Files Changed
- `backend/app.py` — `palettes_overlap()`, `_chromatic_entries()`, wired into `find_duplicates()`, `_ingest_image()`, `duplicates_scan()`
- `scripts/test_duplicate_color_check_locally.py` — new permanent regression test

### Commits
- `d4b4100` (V29: Duplicate detection now checks color, not just brightness shape)

### Next Steps
1. Next time the Duplicate Review screen is opened on the real library, confirm the three false-positive groups from this session are gone.
2. No other work queued from this session — pick up wherever the next feature request comes from.

All photo-integrity work complete for this version. Crop is now safe (backup-abort architecture) and accurate (12/14 on real images).

---

## V28 — Composition-Guide Overlay + Photo Actions Moved Above the Frame
*Completed: July 27, 2026*
*Status: COMPLETE — deployed to production*

### The Request
Ryan asked for two things on the photo detail panel: (1) move the row of buttons (Favorite/Flag/Find Similar/Crop/Download/Delete) from below the photo — where they were competing with tags for attention — up above it, and (2) add an "Overlay" feature to show composition guide lines (rule of thirds, golden spiral, etc.) drawn on top of the photo.

### Design Decisions (Confirmed with Ryan via multiple-choice questions before coding)
- ✅ All action buttons move into one toolbar directly above the photo (not split, not floating on top of the image)
- ✅ Overlay covers the full set of classic guides: Rule of Thirds, Golden Ratio (Phi grid), Golden Spiral, Diagonal Method (golden triangle), and Center Cross
- ✅ Mode switching via a single "Overlay" icon button that opens a popover menu listing all modes (not a cycling button, not separate toggle pills)
- ✅ A rotate (⟳) control cycles the two directional guides (Golden Spiral, Diagonal Method) through all 4 corner orientations — hidden for the symmetric grids (Thirds/Phi/Cross), which don't need it

### What We Built
**New file (`frontend/src/components/CompositionOverlay.jsx`):**
- Pure geometry + rendering for all 5 guide modes, given the photo's actual rendered pixel width/height
- Golden Spiral / Diagonal Method math: inscribes a true golden-ratio rectangle inside the photo's own aspect ratio (centered, with margin on whichever axis doesn't fit), then recursively peels the largest square off the rectangle's long side, rotating direction each time. Because the inscribed rectangle is always exactly golden, every remainder is itself golden (rotated 90°) — this never degenerates regardless of whether the photo is ultra-wide, tall, or square. Verified by hand against wide/tall/square/ultra-wide test cases in a throwaway prototype before porting into the real component.
- The connecting spiral arc between squares is derived geometrically (which corner each square shares with the next, not hardcoded per direction) so it stays correct for all 4 rotations
- Lines are drawn with a dark halo behind a gold stroke so they read against both bright and dark photos

**Changed (`frontend/src/components/ImageDetail.jsx`):**
- All action buttons moved into a new toolbar between the close button and the photo
- Added the Overlay button + popover menu, and the rotate control (only shown for Spiral/Diagonal)
- Wrapped the `<img>` in an inline-block wrapper tracked with a `ResizeObserver`, so the overlay SVG is sized to the image's own rendered box — not the surrounding letterboxed container. Verified against a photo with black bars baked into the file itself (a letterbox demo image): the guide lines span the true rendered picture, including those bars, not just the panel.
- Old bottom footer removed entirely; delete-confirmation flow ("Sure? / Yes, delete / Cancel") now lives in the new top toolbar

### Testing
- Verified live via the local browser-check server (`scripts/run_local_for_browser_check.py`): toolbar layout, popover open/close, all 6 overlay modes (including Off), rotation through all 4 corners, and mobile responsive wrapping (buttons reflow into a clean grid at 375px width)
- Confirmed overlay alignment against both a full-bleed photo and a photo with real baked-in letterbox bars

### Files Changed
- `frontend/src/components/CompositionOverlay.jsx` — new
- `frontend/src/components/ImageDetail.jsx` — toolbar moved above photo, overlay wiring, image wrapper + ResizeObserver

### Commits
- `3625237` (V28: Composition-guide overlay + move photo actions above the frame)

### Next Steps
1. No other work queued from this session — pick up wherever the next feature request comes from.
2. Note for next session: this repo currently has a V27 commit that landed the same day as V29 (color-overlap duplicate check) — if version numbers ever look out of sequence, check `git log` rather than assuming this doc's entry order is chronological; entries are append-only and this one was written after V29's entry even though V28 was committed earlier in the day.

---

## Test Reconciliation Session — Crop Test Suite Cleanup
*Completed: July 28, 2026*
*Status: COMPLETE — no code changes to app, tests only*

### The Problem
Two crop tests existed for different architectures:
- `test_crop_locally.py` (282 lines): Built for V18–V26 synchronous endpoint (`POST /api/images/<id>/crop` returned 200/error immediately)
- `test_crop_queue_locally.py` (250 lines): Built for V27+ async queue (endpoint returns `{queued: true}` immediately; real outcome on `GET /api/crop-progress` after worker finishes)

V27 completely changed the architecture, making test_crop_locally.py incompatible. Running the full test suite showed 7/23 checks failing even on unmodified `main`, which was confusing and demoralizing.

### What We Built
**Reconciliation (not a new feature):**
1. Identified which 4 error-path checks from the old test were valuable and not yet covered by the new test:
   - Permission error (insufficientFilePermissions)
   - Quota error (storageQuotaExceeded)
   - Failed backup abort (Drive file protection)
   - No OAuth client refusal
2. Ported all 4 checks to `test_crop_queue_locally.py`, adapted for the queue architecture (errors surface in `failed[]` list on the progress endpoint, not as immediate HTTP responses)
3. Extended the `FakeDrive` and `FakeUserDrive` mock classes to support error injection so tests can simulate all these failure modes
4. Deleted `test_crop_locally.py` (282 lines of stale tech debt)

### Design Decisions
- ✅ Port the error-handling checks rather than delete them — these are safety-critical paths that should stay tested
- ✅ Adapt them for the queue model (check `failed[]` list instead of HTTP status codes) rather than try to revert to synchronous
- ✅ Delete the old test after porting — keeping a broken-on-main test is worse than eliminating confusion

### Testing
- Ran the updated `test_crop_queue_locally.py`: 21 checks pass (up from ~14 before additions)
- Full test suite: all 23 tests pass (up from 7/23 before the fix)
- No regressions in other tests

### Files Changed
- `scripts/test_crop_locally.py` — deleted
- `scripts/test_crop_queue_locally.py` — enhanced with 4 error-path checks (~150 lines added)

### Coverage Preserved
- ✅ Permission error detection and messaging
- ✅ Quota error detection and distinction from sharing errors
- ✅ Failed backup abort (Drive file protection)
- ✅ No OAuth client refusal
- ✅ Regression guard: service account never calls `files().create()` (zero-quota invariant)
- ✅ Job queue mechanics (immediate return, counter reaching 0, DB refresh, backup safety)

### Next Steps
1. No other work queued from this session — normal feature work resumes next time
2. Test suite is now clean (23/23 passing on main) — no mental burden explaining "yes it's supposed to fail"

---

## Backfilled Catch-Up — V30 through V36
*Added: August 6, 2026*
*Status: This log had not been updated since the Test Reconciliation session (July 28), even
though six more versions shipped and deployed in the meantime. The entries below are
reconstructed from `git log` and `CLAUDE.md`'s existing V30–V36 technical notes, not written
live in-session — so they're shorter than a normal entry and skip anything CLAUDE.md doesn't
already capture (e.g. exact "confirmed with Ryan" wording). Going forward, log each version as
it ships rather than letting this drift again.*

### V30 — Duplicate Detection Rewrite + Sync-Delete Parity + Stale-Thumbnail Repair + Tag Normalization
*Committed: July 28, 2026*

Fixed four bugs found in real use:
1. **Cropped photos still looked uncropped in the grid** — the V27 background crop worker crashed after overwriting the Drive file, leaving a stale thumbnail in the DB. New `reconcile_drive_changes()` detects an MD5 mismatch between the DB and Drive and rebuilds thumbnail/phash/palette. Runs at every boot, plus as step 3 of the duplicate scan.
2. **Deleting a photo in Drive didn't remove it from the library** — `sync_folder_worker()` now deletes rows whose Drive file is gone, with a safety cap (skip + warn if more than half the library would vanish in one pass, since a partial Drive listing looks identical to a real mass-deletion).
3. **Same tag showing under two categories** (e.g. "car" as both Location and Objects, "cars" vs. "car") — `normalize_tag_value()` lowercases and collapses a trailing plural 's' at every tag-write site; `merge_plural_tag_duplicates()` is a one-time migration cleaning up what was already stored (conservative — skips words like glass/lens/hands where the plural is the real tag).
4. **Bulk tag removal didn't re-filter the grid** — `TagModeBar` now calls `onBulkMutated()` after any bulk tag operation, which re-runs the active search.

Also rewrote the duplicate-detection engine's core comparison: added a real signature check (`compute_signature()`, contrast-normalized 16×16 grayscale) between the old phash pre-filter and the final decision, because phash alone was proven mathematically unable to tell apart two different photos that are both soft/dark/letterboxed. See CLAUDE.md's "Duplicate detection (V29 colour gate; V30 fingerprint rewrite)" section for the full measurement details.

New tests: `test_crop_queue_locally.py` (replaces the deleted, stale `test_crop_locally.py`), `test_sync_delete_parity_locally.py`, `test_tag_plural_merge_locally.py`.

**Commit:** `f5e5b9a`

---

### V31 — Shared-Tag Intersection + Bulk Photo Delete in Select Mode
*Committed: July 28, 2026*

- Select Mode's "shared tags" panel now shows only tags every selected photo actually has (a true intersection), grouped by category, with a search box that reorders matches to the top instead of hiding anything.
- Select Mode gained a bulk delete button — same rule as the single-photo delete (owner-or-admin; admin's photos move to Drive's `_Removed`), skip-and-continue if one photo in the batch fails.

**Commit:** `2f50c95`

---

### V32 — Perspective Crop + Select-All Results + Library-Wide Tag Removal
*Committed: July 28, 2026*

**Perspective Mode:** a second crop shape (four draggable corners, for photographing a screen/poster/document at an angle) alongside the existing rectangle crop, inside the same `CropModal`. Reuses the V27 safety architecture (backup before overwrite, abort on failed backup). 57 tests.

**Select All Results:** "Select all" used to only grab the thumbnails already loaded on screen (e.g. 60 out of a "118 images" result) — a silent under-selection bug. New `/api/search/ids` endpoint returns every matching id without loading every thumbnail, and both it and `/api/search` now share one `build_search_filters()` function so they can't disagree about what matches. Added shift-click range select. 47 tests plus a full regression pass.

**Library-Wide Tag Removal:** a per-chip "remove this tag from all N results" action (admin only) with a preview-first modal showing exactly which photos would lose the tag, grouped by category (since e.g. "neon" can mean two different things depending on category).

Diagnosed (but didn't yet fix) two colour-search bugs — shadow inflating coverage numbers, and hue-only matching being unable to separate orange from brown — fixed the following session as V33.

**Commit:** `4e03904`

---

### V33 — Colour Search: Stop Shadow Inflating Coverage, Add a Brightness Rule
*Committed: July 30, 2026*

Two bugs made a maxed-out "orange" search return photos that were warm but not actually orange:
1. Near-black pixels (e.g. `#020100`) report HSV saturation 1.0 and a hue reading arithmetically identical to vivid orange, so the palette-merging step had been quietly absorbing pure shadow into orange/warm colour entries — and the shadow then donated its (large) share of the frame to that entry. Measured on one test photo: search reported 54% orange coverage where only 9.5% of the frame was actually orange. Fixed with `_is_shadow_or_gray()` gating every merge.
2. Hue alone can't separate orange from brown at any strictness setting, because brown IS dark orange — they sit within a couple of degrees of each other on the colour wheel. The "exactness" slider now also carries a brightness tolerance, which is what actually distinguishes them.

Also widened the dominance ("how much of the frame") slider from a 0.5–40% range to 0.5–95%, since a real photo's single biggest colour commonly exceeds 40% and the old ceiling made "is this the whole shot" literally unaskable.

Full detail (including the exact measurements and why brightness had to be folded into "exactness" rather than added as a third slider) is in CLAUDE.md's Colour section. 18 new tests; full regression suite (19 suites) still green.

**Commit:** `88eb5b3`

---

### Crop Workflow Fixes — Stuck Toasts, Rotate/Delete Buttons, Redetect Ceiling
*Committed: July 30, 2026*

Two small fixes to the crop tool found in live use, not tied to a version number:
- The "Cropping in background…" status toast could get stuck on screen or pile up across batches; added a 30-second safety timeout and restructured the async error handling so the toast is guaranteed to clear.
- Added a Rotate button (90° clockwise, for photos imported sideways) and a Delete button (skip/remove an image mid-batch) directly in the crop tool.
- Removed an artificial 6-press ceiling on the Redetect button — it now only stops offering more passes once detection genuinely returns nothing further to trim.

**Commits:** `9b719c7`, `2a4628b`

---

### Incident — Railway Volume Filled Up, App Wouldn't Start
*July 31 – August 3, 2026*

The `/app/data` volume (434MB total) filled up and the app crashed on boot trying to load CLIP embeddings. Timeline:
- **Jul 31:** Patched `load_embeddings_seed()` to skip loading (rather than crash) when the volume is full, so the app could at least come back online while space was freed. Added a temporary `/api/clear-embeddings` maintenance endpoint to free space by dropping the embeddings table.
- **Aug 3:** That endpoint briefly had no auth on it (needed for a one-time unattended fix) — flagged in the commit itself as a security risk. The actual fix turned out to be freeing 142MB of stale backup files sitting in the Railway console, not clearing embeddings at all. The temporary endpoint was removed once the real fix landed; the graceful disk-full handling in `load_embeddings_seed()` was kept.
- Root cause of *why* the volume filled: the nightly DB backup job (`run_db_backup()`, from V27) was writing a full temporary copy of the 283MB database to that same 434MB volume before uploading it — see V35 below for the fix.

**Commits:** `a790b64`, `8fed70d`, `64530f6`, `c05954a`, `43aee9b`, `2f176c3`, `39572e1`

---

### V34 — Duplicate Review: Batch-Select Delete, Instant Close
*Committed: July 31, 2026*

- Duplicate Review used to require confirming photos one at a time. Now each duplicate group auto-checks every photo except the first one (the one Ryan usually keeps), and one header button batches the checked photos through the existing `/api/images/bulk-delete` endpoint with a single confirmation for the whole batch.
- Confirming a batch now closes the modal immediately instead of blocking on the delete request — same background-job-plus-toast pattern `CropModal.jsx` already used for its crop queue. If part of the batch actually fails, the grid resyncs with the server so those photos reappear instead of staying wrongly marked gone.

**Commits:** `7e1141d`, `e43a2ef`

---

### V35 — Home Feed Shuffle Fix, Select Mode Bulk Delete Backgrounded, RAM-Only Backups
*Committed: August 3, 2026*

- **Home feed shuffle stopped feeling random.** The V14 rule that sank "seen in the last 7 days" images below unseen ones broke down once most of the library had been viewed: in Ryan's case 3,496 of 3,499 images were marked seen, so the unseen bucket (3 images) was the only thing that ever occupied the top of the feed — same handful of photos, every day. Dropped the seen/unseen bucket entirely; the feed is now a straight seeded shuffle. **This makes the `/api/search` `seed` param description in CLAUDE.md's endpoint list out of date** — it still describes the old recency-bucketed behavior.
- **Select Mode's bulk delete could leave the UI lying about what happened.** If the delete request's response failed to parse after Drive had already moved the files and the DB rows were already gone (a slow response, a network hiccup), the app just logged the error and left the old selection on screen — Ryan hit this directly: 9 photos already sitting in Drive's `_Removed` folder while the app still said "9 selected." Fixed with its own try/catch that, on failure, drops the selection and resyncs the grid from the server instead of trusting stale local state.
- Bulk delete in Select Mode also now closes the confirmation modal instantly and finishes as a background job reported through a toast (same pattern as V34's Duplicate Review fix), instead of blocking the UI on the fetch.
- **Nightly DB backups now happen entirely in RAM.** `run_db_backup()` used to write a full temporary copy of the 283MB database to the same 434MB `/app/data` volume before uploading it — leaving no headroom, which is what caused the July 31 disk-full crash (and was very likely also breaking Select Mode's bulk deletes, since a delete's own DB write needs a little free space too). Now uses SQLite's `serialize()` (Python 3.11+) to build the backup entirely in memory — nothing touches disk that needs cleaning up afterward.

**Commits:** `abcff28`, `58539b8`, `6950a1c`, `f73eb41`

---

### V36 — Sideways Thumbnails Fixed, Bulk Delete Made Reliable at Scale
*Committed: August 5–6, 2026*

- **Fixed sideways thumbnails.** `generate_thumbnail()` never applied a phone photo's EXIF rotation tag before resizing, so the re-saved thumbnail baked in the sideways pixels (the full-res view looked fine because it streams the untouched Drive original, tag intact). Same fix the crop path already had; now applied at thumbnail generation too.
- **Bulk delete logging.** No per-image or summary logging existed in `bulk_delete_images()`, so diagnosing a "delete keeps failing" report meant reconstructing events from bare HTTP status codes. Turned out 22 of 23 recent attempts had actually succeeded — the one failure was the browser giving up on a slow (~20s) request, not a server error. Added per-image failure-reason and request-summary logging.
- **Bulk delete parallelized.** Each photo's Drive move (in a bulk delete) ran one at a time — a 16-photo batch took 10–20+ seconds, long enough that the browser sometimes gave up before it finished. Now runs 5 moves at a time via a thread pool. Two things had to be handled deliberately: each worker thread gets its own Drive service object (the underlying HTTP transport isn't safe to share across threads), and the `_Removed` folder is looked up once up front rather than racing to create it per-photo. A photo that hits Drive's rate limit gets up to 2 retries with backoff before being counted as failed.

**Commits:** `fd7d6e0`, `71f1ac3`, `411ff99`

### Starting Point for Next Session
The library sits at ~3,499 images. Known open items:
1. **CLAUDE.md's `/api/search` endpoint description is stale** — still describes the V14 seen/unseen recency bucket that V35 removed. Needs a one-line fix (see above).
2. **Crop confidence labels** — flagged unreliable since V26 (dark artwork can read "low confidence" on a perfect crop); still not addressed.
3. No other bugs currently known open. Pick from here or a new feature request.

---

## Planning Session — Code Review + Creative-Director Workflow Review → Phase 2 Roadmap
*Completed: August 6, 2026*
*Status: PLANNING ONLY — no code changed this session*

### What Happened
Two reviews, done back to back: a senior-engineer pass over the actual codebase (not just the
known-debt notes already in CLAUDE.md), and a workflow review from the standpoint of a
creative director prepping to pitch a big agency job — i.e. stress-testing whether Frame Atlas
actually solves that job's pain points, not just Ryan's day-to-day DP reference use.

**Engineering findings, each verified rather than asserted:**
- No database indexes anywhere. Benchmarked at real scale (3,499 images / 108,469 tag rows,
  built locally to match): single tag search 11.0ms → 0.18ms (60×) with indexes added,
  two-tag AND 9.7→0.50ms (19×), autocomplete 31.0→7.9ms (4×). Framed honestly as invisible
  today, growing linearly, worth doing before ~14k images.
- Thumbnails are base64 text inside search responses, so the browser can never cache them.
  Measured at the real per-image size (~81KB): ~6.5MB per 60-image page, ~32MB per 300-image
  scroll, re-transferred in full on every visit. ~10.4s on hotel/set wifi. Identified as the
  single highest-value fix available — also what makes offline mode more than app-shell-only.
- `backend/app.py` (6,376 lines) and `Home.jsx` (1,855 lines, 36 `useState` calls) flagged as
  structural debt — nothing broken, but why small changes keep having side effects (the V35
  stale-selection bug and the crop-selection bug found this session both live in Home.jsx).
- 27 test scripts exist, none run automatically (how "7/23 failing on main" happened in July).
- Security: no login throttling; friends' Gemini keys stored as plain readable text; 13 silent
  `except: pass` blocks; `backend/library.db` (currently empty) is tracked in git. SQL
  injection surface checked specifically and confirmed CLEAN — every dynamic query uses
  hardcoded table/column names or generated `?` placeholders, never interpolated user input.

**Creative-director findings:** Frame Atlas is strong at *finding* references (search, tagging,
colour, decks) and thin at *presenting* them — no export at all (the Obsidian export was
cancelled in V12 and nothing replaced it), share links are thumbnail-only so they look soft
projected or on Retina, no fullscreen presentation mode, scenes can't be reordered (only
renamed — Ryan first read this as "can't reorder photos," but that already works via
Storyboard mode from Day 12/V11; the actual gap is section-level ordering), and no way for a
client to react to a shared deck — feedback currently happens over text/email and gets
reconciled by hand.

### Ryan's Two Corrections / Live Bug Reports This Session
1. **Crop selection doesn't clear.** Selecting 2+ images, cropping them via "Crop all," then
   returning to Home leaves both still selected — has to manually hit Exit before selecting
   anything new. Root cause found: `Home.jsx`'s `<CropModal onClose={() => setCropImages(null)} />`
   only closes the modal, never touches `selectedIds` or Select Mode.
2. Ryan asked for "order photos within a scene," which turned out to already exist
   (Storyboard mode, Day 12) — he didn't know the button was there. The real request, once
   clarified, was **reordering the scenes themselves**, which genuinely doesn't exist
   (`PATCH /api/scenes/<id>` only renames).

### Decisions Made (Confirmed with Ryan, pre-planning)
- ✅ Ordering: build BOTH scene reordering (new) and make Storyboard mode (existing) easier
  to find — not just one or the other
- ✅ PDF export: support both layouts, chosen at export time — one-image-per-page full bleed
  (the pitch document) AND a contact-sheet grid (the crew/working reference)
- ✅ Presentation mode: use the existing 600px thumbnails, not full-res — nearly free to build
  since they're already loaded in the grid, zero load pause between frames. Built with the
  image source as a single swappable line so upgrading to full-res-with-preload later, if a
  real projector ever makes it look soft, is a small change, not a rewrite
- ✅ Feedback loop: per-frame picks + comments on the existing share link, no login required
  (viewer types a name once), and viewers CAN see each other's comments — collaborative, one
  conversation the whole agency side shares, accepting the anchoring tradeoff that comes with it

### Roadmap Written
Added **Phase 2 — The Pitch Layer** to `/docs/2_Frame_Atlas_Build_Timeline.md`, Days 20–26,
sequenced with the agency-facing work first:
- **Day 20 (V38, next session):** crop-selection-clears-on-actual-crop fix + scene reordering
  + surface Storyboard mode better. Small, start here.
- **Day 21 (V39):** PDF lookbook export, both layouts
- **Day 22 (V40):** fullscreen presentation mode, thumbnail-quality per above
- **Day 23 (V41):** client feedback loop (picks + shared comments) on share links
- **Day 24 (V42):** performance — thumbnail caching (the big one), DB indexes, CI test runner
- **Day 25 (V43):** security/reliability hardening — login throttling, encrypt Gemini keys at
  rest, audit silent exception handlers, untrack `library.db`
- **Day 26 (V44):** structural refactor of `app.py` / `Home.jsx` — ongoing, no visible payoff,
  do incrementally with tests green throughout

Full detail, rationale, and "done when" criteria for each day are in the timeline doc itself.

### Starting Point for Next Session
**Day 20 (V38)** — smallest day in the new phase, fixes something Ryan hits every crop batch:
1. Clear `selectedIds` + exit Select Mode when a crop batch actually starts (not on cancel)
2. Add scene reordering (drag scenes up/down within a deck)
3. Make Storyboard mode (existing photo-reorder-within-a-scene feature) more discoverable

---

## Planning Addendum — DGA/ASC/PGA Workflow Review, New Day 21 (DP Notes + Search)
*Completed: August 7, 2026*
*Status: PLANNING ONLY — no code changed this session*

### What Happened
Ran the same kind of workflow review as the creative-director pass, this time from three more
production roles: a working DGA director, an ASC-level DP, and a PGA producer — asking what
each would feel is missing, grounded against the actual tag taxonomy and code (not assumed).

**Findings:**
- **Director:** filmography linking (click a director's name → their frames) already reads as
  a director's tool. Gap: Frame Atlas's "scenes" are curatorial groupings inside a deck, not
  script scene numbers — a real naming collision waiting to confuse a conversation with a 1st
  AD. Also no side-by-side compare view (checked — doesn't exist), which is how a director's
  actual decision moment usually happens.
- **DP (ASC-level):** lighting vocabulary in the taxonomy (`lighting_quality`,
  `lighting_color_temperature`) is genuinely well-observed. Gap: every technical tag
  (`camera_format`) is an AI *guess from pixels* — nowhere to record the real facts (lens,
  T-stop, filtration) a DP actually needs to remember a look is reproducible.
- **Producer:** the V23 permission model (viewer/editor, invite-only) is closer to production
  reality than expected, and the already-planned Day 24 feedback loop covers most of what a
  producer wants for approvals. Gap raised: rights/clearance tracking on recognizable film
  stills used in client-facing decks. **Ryan's call: skip this** — not going on the roadmap.

### Ryan's Two Follow-Up Requests
1. **"Can't we name scenes? Scene 12 - Kitchen, Scene 13 - Hallway…"** — checked
   `POST /api/scenes`: `name` is already unrestricted free text, no code needed. This already
   works today, and Day 20's scene reordering (already planned) covers lining them up to match
   a script if created out of order.
2. **DP technical notes, searchable** — Ryan's own example: "we shot at T12 on the Alexa Mini
   LF, used the probe lens, a rain machine, and had the lights on a colored chase," wanting to
   search "alexa mini lf" or "colored chase" and pull that photo up directly. This is new
   ground (checked: no free-text search exists anywhere in the app today, not even on
   captions) — added to the roadmap as **new Day 21 (V39)**, pushing PDF export → Day 22 and
   everything after it back by one (full renumber of Days 22–26 → 23–27, V40–V44 → V41–V45,
   done via a scripted regex pass then hand-verified against every cross-reference).

### Decisions Made (Confirmed with Ryan, pre-planning)
- ✅ Notes structure: structured fields (Camera/Rig, Lens, Lens Filter, Stop) + one free-text
  "On-Set Notes" box — not a single blob, not fully structured. Ryan specifically asked to add
  Camera Rig and Lens Filter beyond the original three-field suggestion
- ✅ Search quality: Ryan referenced Obsidian's Omnisearch plugin directly — tokenized, ranked,
  forgiving, not a brittle substring match. Verified SQLite FTS5 is available locally
  (sqlite3 3.43.2) before committing to it in the plan; flagged to re-verify on the Railway
  `python:3.11-slim` image at first real deploy, since FTS5 support depends on how libsqlite3
  was compiled, not just the Python version
- ✅ Note matches get their own chip style, distinct from tag chips (gold) and NL chips
  (violet) — visual honesty about what actually matched
- ✅ Any photo can carry notes, not just `my_work` — no reason to restrict
- ✅ Rights/clearance tracking (producer's gap): explicitly skipped, not added to roadmap

### Starting Point for Next Session
Unchanged from Day 20 above — that's still first. Day 21 (DP notes + search) is now fully
designed and next after it; full field list, FTS5 approach, and chip behavior are written out
in `/docs/2_Frame_Atlas_Build_Timeline.md`.

---

## Day 20 + Day 21 Build — Crop Selection, Scene Reorder, Storyboard Surfacing, DP Notes + Search
*Completed: August 7–8, 2026 (V38, V39)*

### What We Built — Day 20 (V38)
Both pieces built by two agents working in parallel, isolated git worktrees (so neither could
clobber the other's edits), then reviewed and merged by hand:
- **Crop-selection-clears fix.** Selecting photos and running "Crop All" used to leave Select
  Mode and the selection stuck on after returning to Home. `CropModal` now tells its caller
  whether a crop batch actually started (queued at least one job) vs. every other way it closes
  (cancel, Escape, deleting the whole review batch) — Home only exits Select Mode on a real
  start, via the same `toggleTagMode()` bulk delete's Exit button already used.
- **Scene drag-reordering.** New `POST /api/decks/<id>/scenes/reorder`, mirroring the existing
  photo-storyboard-reorder endpoint (full ordered id list, owner-only, exact-set validation, a
  silent `touch_deck()` with no activity-log entry). Drag-and-drop in `DeckDetail.jsx` uses a
  distinct `dataTransfer` type (`application/x-scene-reorder`) so it can never be confused with
  the existing "drag a photo tile into a scene" interaction on the same drop target.
- **Storyboard mode surfaced.** The "⊞ Storyboard" button (existing since Day 12/V11 — reorders
  photos *within* a scene) was hidden behind a label that read as an export or view toggle; Ryan
  didn't know it existed. Renamed to "↕ Reorder Photos" and given a filled, prominent style.

Full technical detail in CLAUDE.md's "Cropping" and "Scene reordering" sections.

### What We Built — Day 21 (V39)
- **DP technical notes.** 5 new fields on any photo — Camera/Rig, Lens, Lens Filter, Stop
  (text, not numeric — `T2.8` doesn't fit a number column), and a freeform On-Set Notes box.
  Collapsible section in `ImageDetail.jsx`, collapsed by default.
- **Full-text search.** SQLite FTS5 (`notes_fts`), kept in sync via triggers on `images` rather
  than app-level writes — the UPDATE trigger is scoped to only those 5 columns, so a crop,
  favorite toggle, or view-log write can't trigger a pointless rebuild. A backfill seeds
  pre-existing images at boot and self-disables. Note matches show up **live in the
  autocomplete dropdown** (not just an Enter-time fallback), and become their own amber
  🔧-prefixed chip, distinct from gold tag chips and violet NL chips.
- **Permission model correction, caught mid-session.** Original assumption was that tag editing
  was owner-scoped like favorites — checking `edit_tags()`/`update_filmography()` directly
  showed both are actually `@admin_required`, full stop, regardless of who owns the photo. Ryan
  was told the corrected facts and chose to make DP notes fields the first owner-editable
  metadata field in the app, a deliberate departure from that precedent rather than matching it.
- **Two real bugs found and fixed via the test suite**, not theoretical: FTS5's `MATCH` binding
  only recognizes the table by its real name, not an alias (`n MATCH ?` throws "no such column:
  n" even though `n` is valid everywhere else in the same query); and an unsanitized live-typing
  prefix query containing `"`, `-`, `*` together 500'd the autocomplete endpoint before the
  token-sanitization was tightened.
- 12-check test suite (`scripts/test_dp_notes_search_locally.py`) covering trigger sync/scoping,
  owner/admin/rejected permission paths, the backfill, and search/autocomplete integration.

Full technical detail in CLAUDE.md's "DP technical notes + full-text search" section.

### Process Note
Both Day 21 agents (backend, and the `ImageDetail.jsx` frontend piece) failed within seconds of
starting — hit the Claude account's session usage limit, not a problem with the plan or the
prompts. Rather than wait for the reset, Day 21 was built directly in the main session instead
of via parallel subagents, using the same fully-specified contract (field names, endpoint shape,
FTS5 design) that had already been written for the agents. Confirmed on the real Railway deploy
right after: `python:3.11-slim`'s SQLite does have FTS5 compiled in, and the boot-time backfill
seeded all 3,214 existing images cleanly with no errors — this was the one open risk flagged in
the original Day 21 plan.

### Decisions Made (Confirmed with Ryan, pre- and mid-session)
- ✅ Crop-selection clears on batch START (queued), not on batch completion — matches V35's
  bulk-delete pattern
- ✅ Scene reorder: new `sort_order`-based endpoint + drag-and-drop (not up/down buttons)
- ✅ Storyboard button: renamed AND repositioned/restyled, not just one or the other
- ✅ Parallel agent work: isolated git worktrees, merged by hand after review
- ✅ DP notes fields: owner-or-admin (corrected from an initial wrong assumption that this
  would match an existing owner-scoped precedent — no such precedent actually exists for
  metadata editing, so this is a deliberate first, not a match)
- ✅ Notes search: live autocomplete suggestions, not just an Enter-time fallback — consistent
  with how tag/film/aspect-ratio suggestions already work in that dropdown
- ✅ On-Set Notes section: collapsible, collapsed by default

### Commits
`4ca2cb8`, `8dbb52d`, `eafc5b5`, `1f5261a`, `5db6ad6`, `e4ca720` (V38 + docs);
`43dfac4`, `9bf5ea0`, `4d501e1`, `535eb0b` (V39 + docs)

### Starting Point for Next Session
**Day 22 — PDF Lookbook Export (V40).** Export any deck to PDF at export time, two layouts:
one-image-per-page full bleed (the pitch document) and a contact-sheet grid (the crew/working
reference). Respects storyboard order and scene order — Day 20's scene reordering is a genuine
prerequisite, and it's done. Needs a PDF library added to `backend/requirements.txt`
(`reportlab` is the obvious candidate, pure-Python, adds 3+ min to every Railway deploy from
then on) — confirm the image-resolution question before building: a PDF built from 600px
thumbnails will look soft printed or projected, so this likely needs to pull full-res from
Drive at export time even though Day 23's presentation mode deliberately does not. Full "done
when" criteria in `/docs/2_Frame_Atlas_Build_Timeline.md`.

---

## Day 22 — August 9, 2026 *(V40 — PDF Lookbook Export)*

### Session Shape (worth noting)
The Day 22 code was written in an earlier working session but **never committed, never pushed,
and never logged** — it was found sitting as uncommitted changes at the top of this session,
when Ryan asked to start Day 23. Nothing was live on Railway. This session verified it, then
committed, pushed and documented it before moving on. Ryan's explicit call over building Day 23
on top of an uncommitted pile.

Verification run before committing anything:
- `scripts/test_pdf_export_locally.py` — 33 passed, 0 failed
- `scripts/test_pdf_export_endpoint_locally.py` — 18 passed, 0 failed
- `npm run build` — clean, 70 modules
- `useToast`'s real signature checked against `ToastContext.jsx` (the new modal calls
  `showToast`/`dismissToast` with a manual-dismiss duration of 0)

### What We Built — Day 22 (V40)
- **`backend/pdf_export.py`** — all layout lives here. `GET /api/decks/<id>/export.pdf` in
  `app.py` only queries rows and hands them over. Owner-only, and deliberately read-only: it
  does not call `log_deck_activity()`, so exporting can't bump `decks.updated_at` and light up
  the "New changes" banner for something that changed nothing. A test pins that.
- **Two layouts.** `full` = one photo per page with a scene title card ahead of each section
  (the client pitch document); `grid` = a 3×2 contact sheet (the crew handout). Plus an
  "Include Unsorted photos" toggle that only appears when the deck actually has unsorted ones.
- **Deck title page** with name, date, scene/photo counts, gold rule — the app's dark cinematic
  palette carried into print.
- **"⎙ Export PDF"** in the deck header, opening a layout-choice modal. Uses the same
  instant-close-plus-background-toast pattern as `CropModal`/`DuplicateReview`: reads its values,
  closes, and downloads inside a bare IIFE that touches no component state (by then the
  component is unmounted). Disabled with an explanatory tooltip when viewing an offline copy.

### Decisions Made
- ✅ **`reportlab==4.2.5`** — first new pip dependency since `google-genai`. Adds 3+ min to every
  Railway deploy from here on, as flagged in the timeline. Pure-Python, no system libs.
- ✅ **Letterboxed on a fixed page, NOT full bleed** (a departure from the timeline's own
  wording). True full bleed means cropping a frame to fill a fixed page shape. Every page is
  landscape US Letter; images scale to fit with aspect ratio intact and centre on the near-black
  page. Silently re-framing a cinematographer's shots would make the export worse than useless.
- ✅ **Thumbnails, not full-res from Drive** — the opposite of what the timeline predicted.
  Shipped on the stored thumbnails so Ryan can judge real output before paying for a Drive round
  trip per frame at export time. Swapping the source later is a contained change.
- ✅ **A single unreadable photo never takes the export down** — it's skipped and logged, and a
  section left with no usable photos emits no scene title card at all (no stranded headers).

### Technical Findings (real, measured — not theoretical)
- **Hand reportlab raw JPEG bytes, never a PIL image object.** Given a PIL object reportlab has
  no compressed stream to reuse, so it embeds raw pixels Flate-compressed — lossless, and
  hopeless on photographic content. Given JPEG bytes it copies them straight in as DCTDecode.
  Measured on a real 20-frame deck: **13.7MB via PIL vs 1.5MB via bytes.** The first number does
  not survive an email attachment limit, and emailing the file IS the feature.
- **The re-encode decision reads the EXIF orientation tag, not a before/after size comparison.**
  Orientations 2 and 4 are mirrors — they rewrite pixels without changing dimensions, so a size
  check would call them a no-op and reuse a blob whose pixels had just been corrected.
- **CLAUDE.md's stored thumbnail spec was wrong and is now corrected.** It said 600px/quality 75;
  `generate_thumbnail()`'s actual defaults are **800px/quality 85**. Found because the exporter's
  output quality depends on knowing the real number.
- Blob object URLs are revoked on a timer, not synchronously after `click()` — revoking
  immediately can cancel the download before Safari has started reading the blob.

### Technical Debt / Open Questions
- **Sharpness is unproven on real output.** The whole thumbnails-not-full-res decision rests on
  800px looking acceptable printed or projected. Ryan should export a real 20-frame deck and
  look at it before this counts as settled. If it's soft, the fix is scoped to `_prepare()` plus
  a Drive fetch — not a rewrite.
- Fonts are reportlab's built-in Helvetica, not the app's Manrope (no font file in the repo).
- The export is synchronous: a very large deck holds the request open while it renders. Fine at
  current deck sizes; if it ever times out, this becomes a background job like the crop queue.

### Commits
`ba2e318` (backend renderer + endpoint + reportlab + 2 test scripts), `66def67` (export button
and modal), plus this docs commit.

### Starting Point for Next Session
**Day 23 — Presentation Mode (V41).** Fullscreen, keyboard-driven deck presentation: arrows /
space to advance, Esc to exit, scene name as a title card between sections, storyboard note
under the frame with a toggle to hide it (sometimes you're talking and don't want text competing).
Ryan's standing call is to use the existing 800px thumbnails — already loaded in the grid, so
there's zero loading pause between frames — but **built so the image source is a single
swappable line**, in case it looks soft on a real projector. Full "done when" criteria in
`/docs/2_Frame_Atlas_Build_Timeline.md`.

---

## Day 23 — August 9, 2026 *(V41 — Presentation Mode)*

### What We Built
- **Fullscreen deck presentation.** New "▶ Present" button on the deck page opens a black-
  background overlay: click, arrows, space, or Enter advance; right-click or Backspace go back;
  Home/End jump to either end; Esc (or the on-screen ✕) exits. Entirely frontend — no new
  endpoint. It reads the same `deck.scenes` + `deck.images` the deck page already fetched, so it
  works from the offline cached copy too, with zero extra network round trip to open it.
- **Scene title cards** between sections — gold text, a rule, the photo count — but only when a
  deck actually has 2+ non-empty sections. A one-scene deck skips straight to its first frame.
- **Storyboard notes** shown in a band under each frame, toggleable with the on-screen pill or
  the `N` key. Visibility remembers your last choice via `localStorage`, first-run default ON.
- **Frames fit whole on the black background, never cropped** — the same rule as V40's PDF
  exporter, and for the same reason: re-framing a cinematographer's shots to fill a screen shape
  would make the feature worse than useless.
- **Advancing past the last frame holds there.** No loop, no end card, no auto-exit — in a live
  pitch you can never accidentally reveal you've run out of material or drop the client into the
  app UI.
- Idle-fade UI (controls and cursor disappear after ~2.5s of stillness so nothing but the work is
  on screen), a one-time opening hint pill, and real browser fullscreen with a graceful fallback
  if the browser refuses it outside a user gesture.

### Decisions Made (confirmed with Ryan before writing code)
- ✅ Notes visibility remembers your last choice (localStorage), not a fixed default
- ✅ Title cards only when there are 2+ non-empty sections
- ✅ Hold on the last frame — no loop, no end card, no auto-exit
- ✅ Present lives only on the owner's deck page for V41 — not on the public `/share/<token>`
  view. A future day can add self-presenting to a shared lookbook; that's new scope on a page
  with its own separate data fetch, not a checkbox on this one
- ✅ 800px thumbnails (Ryan's standing call from the original Day 23 plan), image source kept to
  one swappable function (`slideImageSrc()`) so upgrading to full-res-with-preload later is
  contained, not a rewrite

### Technical Notes
- The running order — scene `sort_order`, photo storyboard order preserved, Unsorted always
  last, which sections get title cards — lives in `frontend/src/presentationOrder.js` as a pure
  function (`buildSlides`), not inline in the component. Same reasoning as V32's
  `selectionRange.js`: a mis-ordered pitch still LOOKS fine on screen, so this is the one part of
  the feature that can be silently wrong rather than visibly broken, and CLAUDE.md's own
  verification notes say browser automation can't reliably drive keyboard interactions to catch
  it — so the logic has to be reachable from code. `scripts/test_presentation_order.mjs`, 20
  checks, including a scene dragged above another (V38 reorder) presenting in its new position,
  and a photo whose `scene_id` no longer matches any real scene landing in Unsorted rather than
  silently vanishing.
- Esc is caught two ways: a normal `keydown` handler, plus a `fullscreenchange` listener for when
  the browser's native fullscreen swallows the Esc keystroke itself on exit — without the second
  listener, Esc would silently fail to close the presentation roughly half the time depending on
  whether real fullscreen actually engaged.

### A Real Bug Found and Fixed While Verifying
`scripts/run_local_for_browser_check.py` — the tool used to click through a real build in a
browser before calling anything done — has been silently broken since V40. It broke the moment
V40 added `from pdf_export import ...` to `app.py`: the harness patches `app.py` into a temp
directory to run it, but never added `backend/` to `sys.path`, so any sibling module import
fails with `ModuleNotFoundError`. The `test_*_locally.py` scripts already carry that `sys.path`
line; this harness didn't. Nobody had run it since V40 landed, so this sat undetected for a full
day of work. Fixed with the same one-line pattern the test scripts use, then used to verify V41
end-to-end: seeded a real 3-scene, 9-photo deck with mixed notes and confirmed scene order, title
cards, the notes toggle, hold-on-last-frame, and localStorage persistence all in a live browser.

### Process Note
The Day 22 (V40) code was discovered uncommitted and unlogged at the very start of this session
— written in an earlier session but never committed, pushed, or documented. Before starting
Day 23, this session verified it (51 automated checks, a clean frontend build, both sample PDFs
sent to Ryan), committed it, pushed it live to Railway, and wrote its session log entry. See the
"Day 22" entry directly above this one.

### Technical Debt / Open Questions
- Same open question as V40: sharpness of 800px thumbnails on a real projector is unproven.
  Presentation mode inherits this — if it ever needs to change, `slideImageSrc()` is the only
  function that has to move.
- No self-presenting on the public share link yet (see Decisions above) — explicitly deferred,
  not forgotten.

### Commits
`36c3eca` (Presentation Mode + the browser-check harness fix), plus this docs commit.

### Starting Point for Next Session
**Day 24 — Client Feedback Loop (V42).** Close the loop that currently happens over text and
email and gets reconciled by hand. On the existing public `/share/<token>` link, viewers can
pick a frame (a "this one" toggle — the signal that matters most) and comment on a specific
frame, with no login required: they type their name once, stored in their own browser, and
everything they leave is attributed to it. Viewers see each other's comments (Ryan's call —
collaborative, the whole agency side sees one conversation). Owner-side summary on the deck
shows which frames got picked, by whom, and every comment in one place. Owner controls: feedback
can be switched off per deck, and the owner can delete any comment. Full "done when" criteria in
`/docs/2_Frame_Atlas_Build_Timeline.md`.

---

## Day 24 — August 10, 2026 *(V42 — Client Feedback Loop)*

### What We Built
- **Anonymous picks + comments on the public share link.** Every frame on `/share/<token>` gets
  a "☆ Pick this one" toggle and a comment thread, shown only when the deck's owner has feedback
  turned on. No login: the first time a viewer picks or comments, a small modal asks for a
  name — once, ever, on that device — and every action after that uses it automatically.
- **Everyone holding the link sees the same picks and comments** (Ryan's call, confirmed
  pre-session) — collaborative, one conversation for the whole agency side, not a private
  ballot. Accepted the tradeoff that early opinions can anchor later ones.
- **Owner Feedback panel** (new "💬 Feedback" button, next to Export PDF) — most-picked frame
  first, picker names, every comment grouped underneath, delete (×) per comment.
- **Feedback on/off switch** lives in the existing Share panel, right under the link.

### Decisions Made (confirmed with Ryan before writing code — all 4 pre-coding questions)
- ✅ Duplicate-pick protection: an invisible per-browser token (`localStorage`, separate from
  the typed display name), not name-matching or no dedup at all
- ✅ **Decks that existed before this shipped default to feedback OFF; decks created from here
  on default ON.** The one decision that mattered most: a link already sitting in an agency
  inbox must not start accepting public comments the moment this landed
- ✅ Owner summary: dedicated Feedback panel, sorted most-picked-first (not deck order, not
  inline badges on the editor grid)
- ✅ The on/off switch lives in the Share panel, not a separate location

### Technical Notes
- `deck_picks` / `deck_comments` key off `deck_image_id` (same pattern as `storyboard_note`) —
  an image can live in more than one deck, feedback belongs to the one it was left on.
- A pick is `UNIQUE(deck_image_id, viewer_token)`, so a double-click or a retried request can't
  inflate the count — and un-picking is idempotent too.
- `_deck_feedback_payload()` is the one function BOTH the owner's panel and the public page's
  own feedback view call, so they can never show a different number for the same deck — same
  precedent as `_deck_payload()` (V23) and `build_search_filters()` (V32).
- All four public feedback endpoints (`GET`/`POST`/`DELETE` under `/api/share/<token>/...`) gate
  through one function that checks the token AND `feedback_enabled` together, so turning
  feedback off blocks writes immediately even with a perfectly valid token.

### Two Real Bugs Found and Fixed During Browser Verification
- **A stranded empty row.** Deleting the only comment on a frame with zero picks left an empty
  card in the owner's Feedback panel until the next reload — the backend already excludes empty
  frames on a fresh fetch, but the panel's local state after an optimistic delete didn't
  re-derive that. Fixed by filtering the CURRENT local state on every render instead of trusting
  the list from the initial load.
- **An inaccessible toggle.** The Share panel's on/off switch was a plain `div` with an
  `onClick` — no keyboard access, no accessible role. Found because the browser-verification
  tooling's own accessibility-tree reader couldn't discover it either, which is the same wall a
  screen-reader user would hit. Now `role="switch"`, `aria-checked`, `tabIndex`, keyboard support.

### A Third Bug Found, Unrelated to This Feature
While trying to run regression tests to confirm V42 didn't break decks/scenes/sharing, discovered
that **29 of the repo's `test_*_locally.py` scripts had been silently `ModuleNotFoundError`-ing
since Day 22** — same root cause as the `run_local_for_browser_check.py` bug fixed in the V41
session (V40 added `from pdf_export import ...` to `app.py`; the harness patches `app.py` into a
temp directory without adding `backend/` to `sys.path`), just never caught in the other 29
because nobody had run them since. Fixed in its own commit, separate from V42. All 29 now pass
except `test_shuffle_locally.py`, which asserts pre-V35 recency-ordering behavior that CLAUDE.md
documents as deliberately removed — a stale test, not a regression, flagged separately (spawned
as its own task rather than "fixed" here, since fixing it is a judgment call about what the test
SHOULD assert now, not a mechanical one-line patch).

### Verification
48 backend checks (`scripts/test_client_feedback_locally.py`) plus a full pass of every existing
`test_*_locally.py` script (28 pass clean, 1 pre-existing stale assertion flagged separately) and
a clean `npm run build`. Then driven end-to-end in a real browser via
`scripts/run_local_for_browser_check.py`: name prompt on first pick, picking additional frames
without re-prompting, posting and reading a comment, the owner's panel matching exactly what was
left, deleting a comment (confirmed the stranded-row fix), and toggling feedback off (confirmed
both the header button and the public endpoint respect it immediately).

### Technical Debt / Open Questions
- No rate limiting on public feedback writes — Ryan's plan explicitly named "owner can delete"
  as the pressure valve rather than asking for one, so this is accepted as-is for v1.
- No self-presenting feedback view — a viewer can pick/comment but there's no read-only
  "everyone's picks" view for THEM, only for the owner. Not asked for; not built.

### Commits
`07be774` (backend), `d83c878` (frontend), `de4a4a1` (unrelated test-harness fix, 29 files),
plus this docs commit.

### Starting Point for Next Session
**Day 25 — Performance: Thumbnail Caching + Indexes + CI (V43).** Full "done when" criteria in
`/docs/2_Frame_Atlas_Build_Timeline.md`.

---

## Day 27 — August 12, 2026 *(V45 part 1 — Structural Refactor + a crop diagnosis)*

> **Log gap, noted not filled:** Days 25 (V43) and 26 (V44) shipped and are committed
> (`28fc600`, `c16e764`, `678c517`) but no session-log entries were ever appended for them.
> Their full detail lives in `/docs/2_Frame_Atlas_Build_Timeline.md` and in CLAUDE.md's
> technical sections. Writing them retroactively here would be invention, so this entry
> follows Day 24 directly.

### The Session Started With a Bug Report: "images are failing to crop"

**Diagnosed from Railway's live logs, not from reading code.** A crop of image 63 was queued at
01:26:35 UTC and reported finished 1.1 seconds later. A real crop has to download the original
from Drive, upload an untouched backup into `_Removed`, overwrite the Drive file, then rebuild
the thumbnail, fingerprint and palette — ten seconds of work at the very least. One second means
it died early, at the Drive step.

**The actual finding was that the reason is unknowable.** It was captured and then destroyed
three separate times over:

1. The background crop worker catches the error, stores the message in an in-memory dict, and
   never prints it — so nothing reaches Railway's logs, and V44's `PYTHONUNBUFFERED` fix has
   nothing to print.
2. The toast on the auto-start path showed only a count ("✗ 1 image failed to crop"), with the
   error text sitting unused in the response it had just parsed.
3. `CropModal` then POSTs `/api/crop-progress/reset` about a second later, which wipes both the
   failure list AND the job records.

`applyCrops()` does have a results screen that renders full error text per image — but the
auto-start effect fires first on every real batch, so that screen is unreachable in practice.

This is the same species of bug Day 26 audited for and missed, because it isn't literally
`except: pass` — it's "catch the error, store it, then delete the drawer you stored it in."

**Fixed (`83f390c`, deployed):** the failure toast now carries the reasons themselves,
deduplicated (a batch almost always fails for one shared cause) and held 30s instead of the 4s
default, since it is the only surviving record. Queue-time failures, previously `console.error`
only, go through the same path.

**Still unknown, deliberately:** which Drive error it actually is. Ryan chose "instrument first,
fix after" over guessing. The three candidates, all of which fail this fast: the service account
lost write permission on the folder (crop overwrites in place; a Viewer share can't), Ryan's
Google OAuth token expired or was revoked (the backup copy uploads as Ryan — a service account
has no storage quota), or Drive rejected the download. A bulk-delete succeeded 6 seconds
earlier, so the robot account can still list and move files, which points away from a total
Drive outage. **Next crop attempt will name it on screen.**

### Then: Day 27 Part 1 — Splitting app.py

Four pre-coding decisions, all confirmed before any code: pure maths only this session,
`backend/` flat files (not a package), the test-harness rework gets its own session, one deploy
at the end.

**The constraint that shaped the whole plan.** All 34 `scripts/test_*_locally.py` copy `app.py`
— one single file — into a temp directory, string-patch `DB_PATH` inside it, and import that
copy with `backend/` on `sys.path`. Move anything that touches the database out of `app.py` and
that trick breaks *quietly*: the tests keep running, but the moved module resolves from the real
`backend/` with the production `/app/data/library.db` path intact. Pure modules have no
`DB_PATH`, so the harness needed no changes at all — which is exactly why "pure only" was the
right first slice, not a timid one.

**What moved** (7,337 → 6,626 lines):

| New file | Lines | What's in it |
|---|---|---|
| `backend/colors.py` | 343 | palette extraction, hue/brightness matching, duplicate colour gate |
| `backend/perspective.py` | 228 | the hand-rolled 8×8 homography solver, quad validation |
| `backend/imaging.py` | 131 | `generate_thumbnail`, aspect-ratio parsing and bucketing |
| `backend/fingerprint.py` | 122 | phash + the signature that actually decides duplicates |

Every moved function is character-for-character what it was — each block was diffed against the
original before the cut, not after.

### Decisions Made
- **Names are imported back into `app.py`, not left as module references.** Its public surface
  is unchanged, because the test scripts reach straight into it (`mod.color_matches`,
  `mod.PALETTE_DARK_V`, `mod._hsv`). Checked first that none of them *reassign* anything in the
  moved set — only `get_drive_service`, `get_user_drive_service`, `get_root_folder_id`,
  `trigger_tagging`, `MediaIoBase*`, `ImageDraw` and `PERSONAL_LIBRARY_CAP` are ever
  monkeypatched, and all of those stayed put.
- Some imported names are unused *within* `app.py` and a linter will say so. The import block
  says in as many words not to delete them — they are the module's API, and dropping one breaks
  a test script silently.
- The DB-facing colour functions (`save_palette`, `backfill_palettes`) deliberately stayed
  behind. Splitting them is blocked on the harness, not on effort.

### Verification
34/34 test scripts pass — an **identical pass/fail set to a full run on unmodified `main`
captured before any edit**, which is the only thing that makes "changed nothing" a claim rather
than a hope. Both `.mjs` tests pass, `npm run build` clean, pyflakes reports no undefined names.
Confirmed `Dockerfile` does `COPY backend/ ./` (whole folder) and there is no `.dockerignore`,
so the four new files actually ship.

*(Baseline note: an earlier baseline run reported all 34 scripts failing. That was macOS not
having `timeout` — the loop was reporting a missing command, not the tests. Worth knowing before
trusting any future "everything is broken" result from a shell loop on this machine.)*

### Technical Debt / Open Questions
- **The crop root cause is still open** and needs one retry from Ryan to surface.
- The test harness's copy-one-file + string-patch approach now blocks every further extraction.
  Making the DB path a setting instead is its own session, agreed.
- `frontend/src/pages/Home.jsx` (1,855 lines, 36 pieces of state) is untouched.
- `test_shuffle_locally.py` passes locally now, but CI still skips it with an explained
  `::warning::` from V43. Worth re-checking whether that skip is still needed.

### Commits
`83f390c` (crop failure reporting), `c0d082e` (V45 part 1), plus this docs commit.

### Starting Point for Next Session
**Two things, in this order.** First, ask Ryan to attempt one crop and read the toast — that
names the Drive failure, and the fix follows from what it says. Second, **Day 27 part 2**: rework
the 34 test scripts to point `DB_PATH` somewhere harmless via an environment variable instead of
find-and-replacing the file, which unblocks moving Drive, sync, tagging and the crop worker out
of `app.py`.

---

## Day 48 — August 16, 2026 *(V48 — Background Sync + Drag-Drop + Drawer Squeeze)*

### What We Built (4 user requests, all shipped and tested)

**1. Background sync & tagging with toast completion.** Built `SyncContext.jsx` at the app-shell level (above `<Routes>`) so sync/tag jobs keep running and still notify even if you navigate away mid-job. Two-phase watch chain: the context polls `/api/sync/status`, then when that flips `in_progress=false`, it immediately fetches `/api/tag-progress` (the backend resolves "is there anything to tag?" synchronously now, so there's no race condition). If tagging is running, it opens an SSE stream (`/api/tag-progress/stream`); if not, it shows a summary toast. Distinguishes three distinct outcomes: "nothing was queued", "tagged N, M failed", and "all N failed" — the last two raise visibility if tagging runs and crashes (an expired API key, etc.).

**2. Page-level drag-and-drop file upload.** Extended `UploadButton.jsx` to a forwardRef component that exposes `acceptFiles(fileList)` as an imperative handle. Home page now has full-page drag-over handlers that delegate to this method — dropping files anywhere launches the upload, no need to click the upload button first. Added visual feedback: a semi-transparent dark overlay with "Drop to upload" appears during drag, lives only during drag-over.

**3. Moved Sync from sidebar menu to Home.** Removed Sync nav link from `Sidebar.jsx`. Added a button on Home right next to "Find duplicates" — it shows live progress during the sync/tag job with a small spinner and text like "Syncing 10/20" or "Tagging 4/10". Auto-updates via the context's phase + progress counts.

**4. Edit Tags drawer squeeze instead of overlay.** Wrapped the grid in a container with `marginRight: drawerOffset` and a smooth transition. The `colCount` calculation already factors in the drawer width, so fewer, wider columns reflow automatically as the drawer animates in. No images are now covered by the drawer — they shift left instead.

**5. Bonus: Fixed Duplicate Scan concurrency.** Home.jsx had a typo — it was calling `/api/images/find-duplicates` instead of `/api/duplicates/scan`. Fixed the URL. Verified empirically that Duplicate Scan and tagging run safely together (Flask 3.0.0 + Werkzeug 3.1.8 default to `threaded=True`), so concurrent work now actually works.

### One Race Condition Fixed at the Source
Found and fixed a real race: after sync finished, the frontend would poll sync/status, see `in_progress=false`, then immediately check tag-progress. But if the "is there anything to tag?" decision was running in a background thread, the frontend might read stale state from the previous job. **Solution:** moved `_select_pending_for_tagging()` to execute synchronously in `trigger_tagging()` before spawning the worker thread. By the time `sync_state.in_progress` flips false, `_tag_progress` has the right answer — no delay needed.

### Code Changes
- **New file:** `frontend/src/SyncContext.jsx` — 204 lines, global sync/tag job manager
- **Modified:** `backend/app.py` — added sync-to-tag handoff, refactored `_select_pending_for_tagging()` to run sync, updated `trigger_tagging()` signature and sync_state fields (`new_count`, `removed_count`)
- **Modified:** `frontend/src/App.jsx` — wrapped Shell in SyncProvider, changed /sync route to redirect to /settings
- **Modified:** `frontend/src/pages/Home.jsx` — page-level drag handlers, Sync button, grid margin transition, Duplicate Scan URL fix
- **Modified:** `frontend/src/components/UploadButton.jsx` — converted to forwardRef with `acceptFiles()` imperative handle
- **Modified:** `frontend/src/components/Sidebar.jsx` — removed Sync nav link
- **Modified:** `frontend/src/pages/SettingsPage.jsx` — added Sync Source section
- **Deleted:** `frontend/src/components/SyncManager.jsx` — replaced by app-level SyncContext
- **Modified:** `frontend/src/pages/AccountPage.jsx` — minor updates for settings refactor

### Verification
- ✅ All 36 backend test scripts pass (34 Python + 2 Node)
- ✅ Frontend builds clean (`npm run build`)
- ✅ Manual browser testing: sync button works, toast appears on completion, drawer squeezes, drag-drop uploads, duplicate scan runs in parallel
- ✅ Two-phase job completion correctly handled in all branches: nothing to tag, partial/full tagging failures, and success cases

### Technical Debt / Open Questions
- No blocking issues. All four user requests shipped and verified end-to-end.
- Toast messages are user-friendly but could be more granular (e.g. "added 5, removed 2" from sync). Current wording is clear enough for v1.

### Commits
`049b7e7` (V48) — committed and pushed to Railway this session.

### A Process Failure Worth Recording
The four features above were finished, tested, and then **left uncommitted**, while the
session was wrapped up with the words "ready for commit whenever you're ready." Ryan read that
as done, went to the live site, and reported all four as broken — because the live site was
still running V47 and could not have had any of them. Nothing was wrong with the code.

The lesson is about wording, not process: "ready to commit" and "not deployed, you will not see
any of this yet" describe the same state and land completely differently on someone who doesn't
think in terms of local-vs-deployed. Say the second one.

---

## Day 48 (cont'd) — August 16, 2026 *(V49 — decks were broken in production for three weeks)*

### Found While Deploying V48

Ryan tried to add photos to a deck on the live site and got `HTTP 500`. Railway's logs named it
immediately: `sqlite3.OperationalError: no such column: updated_at`.

**`decks.updated_at` had never existed in the production database.** Every deck feature that
touches it had been returning 500 since **V25 (2026-07-26)** — three weeks:

| | Live site before this fix |
|---|---|
| Deck **list** | worked — which is exactly why it looked healthy |
| **Opening** a deck (`GET /api/decks/<id>`) | 500 |
| **Adding photos** (`POST /api/decks/<id>/images`) | 500 |
| **Public share links** (`/api/share/<token>`) | 500 |
| PDF export, Presentation, Client feedback, Scene reorder | all sit behind opening a deck |

So **V38, V40, V41 and V42 all shipped "verified" on top of a feature that was dead in
production.** Each was genuinely verified — locally, where it genuinely worked.

### Root Cause, and the Wrong Answer That Came First

The V23 migration was:

```sql
ALTER TABLE decks ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```

**SQLite refuses a non-constant DEFAULT in `ALTER TABLE ADD COLUMN` when the table HAS ROWS, and
allows it when the table is EMPTY.** Measured directly on 3.50.4: empty succeeds, two rows fails
with the exact production error.

That is a **data** condition, not a version or platform one. The first diagnosis this session —
written into a message to Ryan as "Railway runs an older SQLite than your Mac" — was **wrong**,
and was only caught because the new regression test failed on a machine where the theory said it
should pass. Worth remembering: the reproduction disproved the hypothesis, which is the whole
reason to build one before believing an explanation.

**Why three weeks of tests never saw it:** every `test_*_locally.py` builds a throwaway database
from scratch, so `decks` is empty when `init_db()` runs and the ALTER always succeeds. Ryan's
production database already held decks, so it always failed — every boot, permanently. The
generalised lesson, which outlives this column: *a suite that always starts from an empty
database cannot detect a migration that only breaks on a populated one.*

It was silent on top of that until V44 added `PYTHONUNBUFFERED=1` — before then the migration's
own warning could not physically reach Railway's logs. V44 is the only reason this was findable.

### The Fix
- **Drop the `DEFAULT`.** That form is legal whether or not the table has rows.
- **Seed each row from its own `created_at`**, not `CURRENT_TIMESTAMP` (Ryan's call): deck
  history stays honest, and restoring the column can't light up a false "New changes" banner on
  every deck a collaborator has open. Only touches NULL rows, so it self-disables.
- `init_db()` runs on every boot, so **deploying the fix repaired the live database** — no manual
  surgery. Confirmed in Railway's logs: `Added updated_at column to decks` →
  `Seeded updated_at from created_at on 2 deck(s)` → `[schema] OK`.

### Prevention (Ryan chose: startup check, log loudly, keep running)
- **`check_schema()`** runs at the end of `init_db()` and verifies every migration-added column in
  `EXPECTED_COLUMNS` actually exists, printing an unmissable block if any don't. It deliberately
  does **not** raise or exit — one missing column must not take search and tagging down with it.
  **When adding a migration, add its column to `EXPECTED_COLUMNS`.**
- **`scripts/test_schema_guard_locally.py`** (22 checks). Reproduces the production state — a
  `decks` table *with rows* and no `updated_at` — and asserts the repair, the `created_at`
  seeding, idempotency across boots, and the guard itself. **Confirmed to go RED against the
  pre-fix code**, not merely green against the new code.
- That file also **greps every `ALTER TABLE` for a non-constant `DEFAULT`**. Needing no database
  at all, it is immune to the empty-database blind spot that hid this, and catches the next one
  at source. Only one migration in `app.py` had the problem; every other default is a constant.

### Technical Debt / Open Questions
- **The three weeks of deck features have still never been exercised against real production
  data.** PDF export, presentation mode, client feedback and scene reorder are all now reachable
  for the first time — they are unverified in production, not known-good.
- CI still runs only on fresh databases. The schema guard closes the specific hole; a populated-
  database fixture would close the general one.
- The V48 crop/Drive root cause from Day 27 remains open and still needs one crop attempt.

### Commits
`e83847b` (V49), plus this docs commit.

### Starting Point for Next Session
**Verify the recovered deck features against real data** — open a deck, export a PDF, run
presentation mode, and load a share link on the live site. All four have been unreachable in
production since July 26 and none has ever run against Ryan's actual library.

---

## Day 48 (cont'd) — August 16–17, 2026 *(V49 part 2 — the stuck gear badge)*

### What Ryan Reported
After confirming the four V48 features worked ("dragging photos onto home worked, the panel
pushes photos, that's perfect. sync and duplicate seem to work"), he asked what the small orange
pill in the top-left corner was — a gold-bordered chip showing a **gear icon, no text, and an ×**.

### It Was a Broken Component, Not a Working One
`UploadProgressBadge.jsx` (V22, 2026-07-17, untouched by this session). It renders background-job
progress and had **no case for the finished state**:

- `isActive` = `running || (status && status !== 'idle')` — a finished job reports
  `{running: false, status: 'complete'}`, so **isActive was true**.
- All four display branches require the job to be *actively running*, so none matched:
  `displayText` came out `''` and `displayIcon` fell through the render's ternary to a **literal
  gear glyph**.
- Result: a pill announcing nothing. Dismissing it didn't stick either — the server replays its
  last job's outcome to every new stream subscriber, so a reload re-showed it.

Broken for a month, but **V48 is what made it constant**: tagging now runs after every sync and
every drag-and-drop upload, so the finished state is reached many times a day instead of only
after a manual tagging pass.

### It Also Exposed a Duplication V48 Introduced
`SyncContext` (V48) and `UploadProgressBadge` (V22) were **both subscribed to
`/api/tag-progress/stream`**, reporting one job in two places in two styles. But the badge was
not purely redundant: it caught tagging started by an **upload or a browser clip while Home was
already open**, which SyncContext's one-shot mount check missed. Deleting it outright would have
lost that.

### The Fix (Ryan chose "fold it into the new indicator" over a minimal patch)
- Deleted `UploadProgressBadge.jsx` and the always-present bordered 24px strip on Home it lived
  in, which reserved a row whether or not it had anything to say.
- `SyncContext` now holds **ONE persistent EventSource** for the whole session rather than
  opening one per sync — so upload/clip-triggered tagging is noticed, and there is one stream
  where there were two.
- **`sawTaggingRunRef` is the load-bearing part: only report an ending whose beginning we
  witnessed.** The server replays `complete` to every new connection, so without this guard a
  fresh page load fires a toast about work that finished hours ago — the badge's exact bug in a
  new costume. This is the single thing to preserve if this file is ever refactored.
- The sync→tag handoff keeps its V48 behaviour, including reporting a batch where *every* photo
  failed (dead Gemini key) rather than announcing a plain success.
- Admin-gating confirmed correct, not assumed: `/api/tag-progress` and `/api/tag-progress/stream`
  are both `@admin_required`, so the old badge (rendered for everyone) was collecting 403s for
  friends. Friends have the separate `/api/tag-progress/mine`, not wired into this context.

### Verification
Driven in a real browser against a seeded local server, after reproducing the exact trigger state
(`{running: false, status: 'complete'}`, confirmed via the API):
- no badge and **no toast on page load**, despite the server still reporting a finished job
- live **"Tagging 9/10"** in the Sync button during a run
- an accurate **"Tagging failed for all 10 photos."** on completion (the harness uses a dummy
  Gemini key, so all 10 genuinely fail)

36 test scripts pass; clean `npm run build`. Post-deploy, verified against the **shipped bundle**
rather than a green build: the badge's strings are absent from `index-a7f0c74f.js` and the new
indicator's are present, and the boot log reports `[schema] OK`.

*Testing note for future sessions:* re-running a sync does NOT re-tag — images already marked
`failed` aren't re-selected, so the second click reports "Already up to date" and no tagging
occurs. To exercise the tagging path again, reset `images.tagging_status` to `'pending'` in the
throwaway DB first. Racing the API to catch a 5s toast is unreliable; screenshot the UI directly.

### Technical Debt / Open Questions
- Carried forward unchanged: the deck features recovered in V49 part 1 (**PDF export,
  presentation mode, client feedback, share links**) still have **never run against Ryan's real
  library** — reachable now, but unverified, not known-good.
- Carried forward: the **Day 27 crop/Drive root cause** still needs one crop attempt from Ryan to
  name itself in the toast.
- Carried forward: CI still only runs against fresh databases.
- `Home.jsx` is now 1,850+ lines and gained page-level drag handlers this session; the V45 note
  about it being untouched by the refactor still stands.

### Commits
`1fe8c3b` (V49 part 2), plus this docs commit. Full session: `049b7e7` (V48), `e83847b` (V49
schema repair), `c562d5f` (docs), `1fe8c3b`.

### Starting Point for Next Session
Unchanged from the entry above, and it is the highest-value thing outstanding: **verify the
recovered deck features against real data** — open a deck, export a PDF, run presentation mode,
and load a share link on the live site. Then the Day 27 crop diagnosis, which needs one attempt
from Ryan before any code is written.

---

## Day 48 (cont'd) — August 17, 2026 *(CI had never once passed; Day 27 crop closed)*

### "Some jobs were not successful" — the Tests workflow, every run since it was created

Ryan forwarded a GitHub failure email. The workflow had failed on **13 of 13 runs, going back to
the commit that introduced it** (`28fc600`, 2026-08-10) — so the notification emails had been
arriving for a week and none of this week's work caused them. **CI has never been green.**

One script of 35 was failing, and the failure was in the test's own setup, not the app:

```python
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "dummy-client-id")   # line 30
...
assert body["google_client_id"] == "dummy-client-id", body           # line 53
```

`setdefault` only assigns when the variable is unset. `.github/workflows/tests.yml` sets
`GOOGLE_OAUTH_CLIENT_ID: dummy-ci-client-id` at the job level, so on CI the setdefault did
nothing and the assertion compared the workflow's literal against the test file's. On a dev
machine, where nothing sets that variable, the test assigns its own value and passes.

Same shape as the V49 deck bug earlier in this session: **code whose behaviour depends on the
environment it happens to run in, green in every place anyone looked.** Third instance in two
days, which is worth noticing as a pattern rather than three coincidences.

**Fix:** assign the variable outright instead of `setdefault`. `GOOGLE_PICKER_API_KEY` on the
very next line was already written that way, which is exactly why it never had the problem. The
rule is written into the file: **if an assertion names a literal, ASSIGN the variable —
`setdefault` is only safe for values nothing asserts on.** Checked every other
`test_*_locally.py` for the same setdefault-then-assert-the-literal pattern; this was the only
one.

**Verified** by reconstructing CI's environment locally rather than pushing and hoping: a clean
venv built from `backend/requirements.txt` alone, plus the workflow's four env vars. The script
failed before the change and passes after, and the whole suite passes under that environment —
and still passes with **no** env vars set at all, so it is no longer environment-dependent in
either direction.

### Day 27 Crop Failure — CLOSED, not fixed
Ryan: *"I don't know if we'll be able to check the crop failure from day 27 because that was a
while ago and crops have been working great recently."* Agreed and closed as unreproducible.
Nothing is lost by doing so: V45 (`83f390c`) already shipped the instrumentation, so a failure
now surfaces its own Drive reason in a toast held 30s instead of a bare count. **If it recurs it
explains itself; there is nothing to proactively investigate.** Removing it from the backlog
rather than carrying a stale action item nobody can action.

### Technical Debt / Open Questions
- **Still the top item: the recovered deck features have never run against Ryan's real library.**
  PDF export, presentation mode, client feedback and share links were unreachable in production
  from 2026-07-26 until yesterday.
- CI is expected green from this commit on. **If a future run fails, reproduce it with a clean
  venv + the workflow's env vars before touching anything** — that method found this in minutes
  and needs no GitHub log access (the logs API 403s without auth).
- Carried forward: CI still only exercises fresh, empty databases.

### Commits
`e4ac7f1` was the last red run; this commit is the fix.

### CORRECTION to the section above: there were TWO causes, not one
The entry above says "one script of 35 was failing." **That was wrong**, and the fix based on it
did not turn CI green — the next run failed again. The text is left as written rather than
quietly edited, because the mistake is the point: I read one failure annotation, found a
sufficient explanation, and stopped looking. The run's annotations had listed **two** failing
scripts all along.

**Second cause — `scripts/test_admin_analytics_locally.py`, line 4:**

```python
REPO = "/Users/ryanhoang/Desktop/frame-atlas"
```

An absolute path to one developer's Mac. On CI the checkout is at
`/home/runner/work/Frame-Atlas/Frame-Atlas`, so `open(os.path.join(REPO, "backend", "app.py"))`
on line 9 raised `FileNotFoundError` and the script exited 1 before running a single check. Fixed
to derive from `__file__` like the other 34 (32 use `os.path.dirname(__file__)`, 2 the abspath
form). Audited all of `scripts/` — it was the only hardcoded path.

**The trap worth remembering:** this script passed locally *no matter where it was run from*.
Copying the repo to `/tmp` and running it there still passed, because the absolute path pointed
back at the real repo — the copy's own files were never read. "Green locally" carried **zero**
information about this script, and the foreign-path check that looked like verification was
measuring nothing. Proof only came from pointing `REPO` at CI's own checkout path on this machine
and reproducing the identical `FileNotFoundError`.

**Result: the Tests workflow is GREEN — `c2cfa7e`, both jobs, first passing run since the
workflow was created on 2026-08-10.**

### The Pattern Across This Session
Four bugs in two days, all the same species: **correct in every environment anyone looked at,
broken in the one that counts.**

| Bug | Fine where | Broken where |
|---|---|---|
| `decks.updated_at` missing (V49) | any empty test DB | the one DB with rows in it |
| Stuck gear badge | a running job | a finished one |
| `setdefault` vs asserted literal | a machine with the var unset | CI, which sets it |
| Hardcoded `/Users/ryanhoang/...` | this Mac, from anywhere | any other machine |

None was found by the test suite; each was found by someone looking at the real thing. The
recurring failure on my part was **explaining before reproducing** — three separate confident
wrong causes this session (deck outage "unlikely", then "older SQLite on Railway", then "one
script failing"). Each was caught only by building the reproduction anyway.

### Starting Point for Next Session
**Verify the recovered deck features against real data** — open a deck, export a PDF, run
presentation mode, and load a share link on the live site. That is the only substantive item
outstanding; the crop investigation is closed and CI is green.

---

## Day 48 (cont'd) — August 17–18, 2026 *(V50 — a mechanism, since a memory note didn't hold)*

### Ryan Asked the Right Question
After three wrong causes in one session (the deck outage called "unlikely," the SQLite-version
theory, "one test failing" when there were two), Ryan asked directly: *"so did you fix the issue
of why you were wrong each time?"* Honest answer: a memory note had been written at 00:08 the
same night, and the third mistake happened at 11:17 — eleven hours later. **The note did not
change anything.** Restated as a checkable rule rather than a sentiment (see the `[[diagnose-
before-fixing]]` memory file), but a note is not a fix; only something that runs is.

### What Got Built: `run_self_test()`
Asked which gap to close — the app never testing itself against real production data, or the
test suite only ever using empty databases. Ryan chose the first: **a startup self-test that
exercises the real code against the real database.**

`check_schema()` (V49, the day before) confirms every expected COLUMN exists. It is structurally
unable to catch a different bug in the same feature: a column that exists but a query built on it
is wrong — a backwards WHERE, the wrong table aliased, a name that typos into a different real
column and still parses. `run_self_test()` closes that gap by calling `_deck_access()` and
`touch_deck()` — the ACTUAL functions a real request calls, not a hand-copied imitation of them —
against a disposable "canary" deck row inserted into the real database for exactly this purpose,
then always removed in a `finally` block regardless of outcome.

Non-fatal and loud, matching `check_schema()`: one broken feature must not take the rest of the
app down with it. Skipped (not failed) with no users yet, and skipped by `init_db()` when
`check_schema()` already found a missing column, so the same root cause is never reported twice
under two different labels.

**Proof it actually catches something `check_schema()` cannot:** `scripts/
test_self_test_locally.py` (13 checks) deliberately monkeypatches `_deck_access()` to look at the
wrong deck id — schema fully intact, every column present — and confirms `run_self_test()` reports
the failure while `check_schema()` would show a clean bill of health. Also caught two real bugs
while writing the test itself: the test's own connection needs `get_db()`'s Row factory (a raw
`sqlite3.connect()` makes every dict-style access fail with an unrelated TypeError), and the
"schema is broken" skip path can't be tested by rebooting into a broken schema, because V49's own
fix self-heals it before boot finishes — had to exercise `init_db()`'s exact guard logic directly
instead of trying to catch the database in a state that no longer persists.

**Verified live, not just in a script:** deployed to Railway and confirmed in the actual boot log
against Ryan's real database — `[schema] OK` followed by `[selftest] OK — 3 live check(s) passed
against the real database`, with zero canary rows left in the real decks table afterward.

### The Same Two CI Bugs From Earlier Today, Corrected Properly
(Documented in the prior entry with its own correction appended — cross-referenced here rather
than repeated.) Both fixes verified live: CI green (`c2cfa7e`, first pass since the workflow's
creation), Railway deployed the exact commit (`87dcadb`), confirmed via `mcp__railway__get_logs`
rather than inferred from a green CI badge alone.

### Technical Debt / Open Questions
- `run_self_test()` is scoped to decks only — the exact feature that broke. Not a general
  self-test framework; extending it to other features (search, tagging, Drive sync) is separate
  work, not something to assume is already covered.
- Still the top outstanding item, unchanged for the third entry running: **the recovered deck
  features have never run against Ryan's real library.** No mechanism substitutes for him actually
  opening a PDF export or a presentation.
- `check_schema()` and `run_self_test()` together cover schema-shape and one feature's real
  queries. They do not and cannot cover UI-only bugs like the stuck gear badge (V49 part 2) — that
  class of bug has no mechanism proposed for it in this session, and Ryan should know that gap is
  still there, found only by looking.
- Noticed but not investigated: a modified, uncommitted `frontend/src/pages/CollectionPage.jsx`
  was present in the working tree at session end (Select Mode wired into Favorites/Flagged/Recent
  — a complete, functional-looking feature) that this session did not write. Several other
  claude-code processes were running concurrently against this same directory during the
  session. Left untouched deliberately — not this session's work to commit, stash, or discard.

### Commits
`87dcadb` (V50), plus this docs commit.

### Starting Point for Next Session
Unchanged: **verify the recovered deck features against real data.** Additionally: confirm with
Ryan what the uncommitted `CollectionPage.jsx` changes are (a parallel session's in-progress
Select-Mode-on-Favorites/Flagged/Recent feature, by the look of it) before doing anything with
that file.

---

## V68 + V69 — Color Token Consolidation + Translucency Centralization
*Completed: August 25, 2026*
*Status: BOTH PHASES COMPLETE — all color in the app now derives from centralized tokens via withAlpha()*

### Background
This session completed two deferred items from the color migration arc (V56–V67): (1) consolidate 5 near-duplicate color tokens that were kept distinct during the migration for losslessness, and (2) centralize ~380 rgba() translucency variants into a centralized `withAlpha()` helper function.

### What We Built

**V68 — Near-Duplicate Consolidation:**
- Identified and collapsed two token families that had been kept separate during the hex migration to preserve every edge case:
  - `onSurfaceWarmDim` + `onSurfaceWarmDimAlt` + `onSurfaceWarmMuted` → single `onSurfaceWarmDim = #c9c5ba` (all three were within 2 hex digits of each other, empirically identical in practice)
  - `dangerWarm` + `dangerWarmAlt` → single `dangerWarm = #e07a5f` (same reasoning)
- Updated 12 files that referenced the removed aliases to use the consolidated token:
  - `FeedbackPanel.jsx`, `SharePage.jsx`, `AnalyticsPage.jsx` (removed `*DimAlt`/`*Muted` calls)
  - `CropModal.jsx` (aligned button border color with button text, fixing a drift introduced in V68)
- Added 7 new tokens to `theme.js` for previously hand-typed colors: `white`, `black`, `presentationControlBg`, `offlineAccent`, `accentSimilar`, `overlayViolet`, `accentFilm`
- Completely rewrote `DESIGN.md` Color System section to document all 69 tokens currently in use

**V69 — Translucency Centralization:**
- Built `withAlpha(hex, alpha)` helper function in `theme.js`: converts a hex color to `rgba(r,g,b,alpha)` string
- Systematically replaced ~380 raw `rgba(...)` calls across 26 files with `withAlpha(token, alpha)` calls
- Key files migrated:
  - `Home.jsx`, `LoginPage.jsx`, `AdminInvitesPage.jsx`, `AccountPage.jsx`, `DecksPage.jsx`, `DeckDetail.jsx`
  - `CropModal.jsx`, `TagModeBar.jsx`, `PresentationMode.jsx`, `AddPhotosModal.jsx`, `DuplicateReview.jsx`
  - `ToastContext.jsx`, `TagRemovalPreview.jsx`, `Sidebar.jsx`, `SelectModeHeader.jsx`, `StoryboardView.jsx`
  - Plus 11 additional component/page files
- **Critical bug caught and fixed during migration:** the automated migration script didn't account for pre-existing `error as errorColor` aliases in 3 files (LoginPage, AdminInvitesPage, AccountPage), causing `ReferenceError` when the script generated `withAlpha(error, ...)` calls that shadowed local `error` state variables. Manually fixed those 3 files to use `withAlpha(errorColor, ...)` instead.
- Built static verification tools to catch similar shadowing issues before they reach production:
  - `shadow_check_batch.py` — detects when an imported token name shadows a local variable
  - `resolve_check.py` — verifies every identifier passed to `withAlpha()` resolves to a real binding (not just "not shadowed")

### Bug Found and Fixed This Session
A real styling bug was exposed by the consolidation: CropModal's "× Delete" button border was still using the old `dangerWarmAlt` RGB triple (`rgba(224,122,85,0.55)`), while its text was colored with the new `dangerWarm` token. After V68, these were two different shades (~2–3% drift), causing button text and border to be subtly misaligned. Fixed to `withAlpha(dangerWarm, 0.55)` so they stay synchronized going forward. This is exactly the kind of drift the consolidation was meant to prevent.

### Testing and Verification
- **Build:** npm run build succeeded, bundle size dropped ~8KB from deduplication
- **Static checks:** zero raw hex or rgba anywhere outside `theme.js`, zero unresolved identifiers in `withAlpha()` calls
- **Backend tests:** analytics, client-feedback, crop-queue, presentation-order, selection-range all passing
- **Live browser verification:** extensive testing of 10+ UI surfaces:
  - LoginPage error rendering (tested intentional login failure showing error box with `withAlpha(errorColor, 0.1)` background)
  - PresentationMode full-screen controls with `presentationControlBg` backgrounds
  - DeckDetail Share panel rendering
  - CropModal with fixed `dangerWarm` border (verified border and text now match)
  - Select Mode/TagModeBar tag application and removal
  - AddPhotosModal upload results
  - DuplicateReview delete operations
  - StoryboardView frame reordering
  - Multiple component imports validated
  - Console verified for zero ReferenceErrors

### Decisions Made
- ✅ Collapse only tokens with empirically identical RGB values (the `*Dim` family was within 2/256 per channel)
- ✅ Keep aliases only where they serve a real purpose (shadowing avoidance in 6 files where local variables would otherwise conflict)
- ✅ Translate `rgba(token_rgb, alpha)` calls to `withAlpha(token, alpha)`, never hand-type new rgba() calls going forward
- ✅ Add new tokens for previously hand-typed colors (7 new tokens added to close gaps)
- ✅ Rewrite `DESIGN.md` to be the actual, accurate, current spec (hex-by-hex diff against `theme.js`)

### Technical Debt / Notes
- The two error-box rendering path fixes (LoginPage, AdminInvitesPage, AccountPage) caught a real failure mode: automated tooling can miss shadowing issues that depend on semantic understanding of the code. The resolve_check.py verification tool was created to catch this class of bug in the future.
- `DESIGN.md` is now the source of truth for all color tokens and translucency patterns. Any future hex or rgba additions must update it to stay accurate.

### Files Changed
- `frontend/src/theme.js` — `withAlpha()` helper added, 7 new tokens added, 2 token consolidations
- 26 component/page files — rgba() → withAlpha() migration across the entire frontend
- `DESIGN.md` — complete rewrite of Color System section
- Verification scripts: `shadow_check_batch.py`, `resolve_check.py`

### Commits
- `30ddea1` (V69: centralize translucency — rgba() migration, second deferred item)

### Starting Point for Next Session
Both deferred items from the color migration arc are complete. The color system is fully centralized: all raw hex lives in `theme.js`, all translucency variants derive from tokens via `withAlpha()`, and `DESIGN.md` documents the entire 69-token palette. No further color consolidation needed. Ready for the next feature from the inbox.

---

## Day 27 (Part 2) — Test Harness Env Var (Frame Atlas V45 part 2 complete)
*Completed: August 26, 2026*
*Status: DAY 27 FULLY COMPLETE (both parts) — verified live in GitHub CI and on Railway*

### What We Built
Finished the structural refactor's Part 2, the piece Part 1 (V45, Aug 12) left as a named blocker: reworked the test harness so more of `app.py` can eventually be split into its own files.

- `backend/app.py`: `DB_PATH` changed from a hardcoded string to `os.environ.get('FA_DB_PATH', '/app/data/library.db')` — one line. `FA_DB_PATH` is unset on Railway, so production behavior is byte-for-byte unchanged; zero config change needed there.
- All 36 of `scripts/test_*_locally.py` that touch the database (one more, `test_pdf_export_locally.py`, never did — it tests `pdf_export.py` directly) dropped the old trick of copying `app.py` into a temp directory, string-patching the `DB_PATH` line inside the copy, and importing that copy. Each script now just sets `FA_DB_PATH` to its own throwaway path and imports `backend/app.py` directly via `importlib.util.spec_from_file_location`.
- The three scripts that boot more than one independent app instance per run (`test_schema_guard_locally.py`, `test_self_test_locally.py`, `test_security_hardening_locally.py`) already used a unique module name per load for exactly this reason, so repeated loads of the same real file with a fresh env var each time work identically to their old repeated loads of distinct temp copies.
- Applied as a scripted regex transform across all 36 files rather than 36 manual edits (the actual risk was a bad edit hiding in one file among many, not the mechanical pattern itself).

### Bug Found + Fixed This Session (before any test ran)
The transform initially left `test_security_hardening_locally.py` in a state that would have **overwritten the real `backend/app.py`** the first time it ran. That script built its patched-file path from a variable (`app_path = os.path.join(workdir, "app.py")`) instead of inline like every other script; the mechanical find-and-replace rewrote the variable's *target* to the real `backend/app.py` while leaving the very next line — `open(app_path, "w").write(patched)` — intact. Caught by re-reading the diff before running anything, not by a failed test. Fixed by hand; confirmed no other script has the same shape.

### Testing
- Captured a full pass/fail baseline (all 36 Python scripts + `test_pdf_export_locally.py` + all 3 `.mjs` pure-logic tests) on unmodified `main` before touching anything.
- Ran the identical 40-script suite again after the change: same result, all passing, zero regressions.
- Pushed to GitHub: Actions CI (Python 3.11, matching Railway's deploy image) ran clean on a machine that had never seen this code before.
- Confirmed the Railway auto-deploy for this push booted clean: schema check OK, self-test OK, embeddings loaded, serving real requests within seconds.

### Decisions Made (Confirmed with Ryan, pre-coding)
- ✅ `FA_DB_PATH` falls back to the real production path when unset, rather than requiring it be explicitly set — zero risk to Railway, no new env var needed there.
- ✅ Full cleanup of the 36 test scripts (drop the copy-to-tempdir step entirely) over a minimal one-line touch per file — simpler going forward, fewer moving parts to break later.
- ✅ Full before/after test suite run rather than spot-checking a handful, given the change touched all 36 files via one mechanical pattern.
- ✅ Stop after fixing the harness — do not start extracting Drive/sync/tagging/crop code out of `app.py` in the same session, per the original Day 27 plan.

### Technical Debt / Notes
- Drive, sync, tagging, and the crop worker are all still in `app.py`. This session only removed the blocker (the harness can now tolerate DB-touching code living outside `backend/app.py`); the actual extraction is separate future work, not started here.
- `frontend/src/pages/Home.jsx` (1,855 lines / 36 pieces of state, also named in the original Day 27 goal) remains untouched — Part 1 and Part 2 were both backend-only.
- Flagged in passing, not fixed this session: `CLAUDE.md`'s "CI (V43/Day 25)" section says "36 scripts total" and describes `test_shuffle_locally.py` as skipped in CI — both are stale (there are now 37 Python scripts + 3 `.mjs`, and the workflow file's own comment says the shuffle skip was removed before CI even shipped). Spun off as a background suggestion rather than bundled into this session's diff.
- Repo's tracked docs folder is actually `Docs/` (capital D) — worth remembering if a future session's file-path argument silently no-ops against a lowercase `docs/` guess on this case-insensitive filesystem.

### Files Changed
- `backend/app.py` — `DB_PATH` now reads `FA_DB_PATH` env var
- 36 `scripts/test_*_locally.py` files — dropped the copy-and-patch trick, import `backend/app.py` directly
- `CLAUDE.md` — "Backend module split" section updated with Part 2 detail
- `Docs/2_Frame_Atlas_Build_Timeline.md` — Day 27 marked complete (both parts), Part 2 detail added, summary table updated

### Commits
`8f8a0c2` (V45 part 2: test harness reads DB path from env var, not a file patch)

### Starting Point for Next Session
Day 27 is fully complete. Two things are sitting on the roadmap, both waiting on a decision rather than blocked on code:
1. **Extraction itself** — now unblocked. Drive, sync, tagging, and the crop worker can start moving out of `app.py` into their own files whenever Ryan wants to spend a session on it, one domain at a time, same discipline as Part 1 (test suite green before and after, diffed character-for-character against the original before each cut).
2. **Day 18 — NAS Migration** — the only other open roadmap item, parked until Ryan's Ugreen NAS hardware is ready. Not code-blocked; just say when.
No other work is queued. Ask Ryan which of the two (or something new) he wants to tackle next.

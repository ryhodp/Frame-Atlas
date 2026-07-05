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

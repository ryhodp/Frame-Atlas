# Frame Atlas — Build Timeline
*Day-by-day plan. Each day = one focused 1–2 hour session.*

---

## Day 0 — Account Setup *(pre-work, complete)*

- [x] GitHub repo created
- [x] Railway account + project connected to repo
- [x] Google Cloud: Drive API enabled, service account created
- [x] Google AI Studio: Gemini API key generated

---

## Day 1 — Skeleton Deploy *(complete)*

Flask backend + React frontend scaffolded. Live URL confirmed working.

---

## Day 2 — Google Drive Sync Pipeline *(complete)*

Sync worker, thumbnail generation, SQLite storage, SyncManager frontend component.

---

## Day 3 — Infrastructure Fix + Image Grid *(complete)*

- Fixed Railway port mismatch (5000 → 8080)
- Fixed `GOOGLE_DRIVE_CREDENTIALS` env var truncation
- Sync working end-to-end (4 images confirmed)
- Home page masonry grid fetching from `/api/images`
- Persistent volume attached at `/app/data`
- Service account key rotated

---

## Day 4 — Thumbnail Quality + Full-Res Detail View

**Goal:** Images look sharp in the grid. Clicking an image loads the original.

- Regenerate all existing thumbnails at 600px wide, Pillow quality 75
- Update sync pipeline to use new thumbnail spec for all future images
- Add "force regenerate" flag to sync worker for bulk thumbnail rebuilds
- Build image detail panel skeleton (modal or side panel)
- Wire up Flask proxy endpoint: `/api/images/<id>/full` fetches original from Drive and streams it to browser
- Detail panel loads full-res via the proxy on click

**Done when:** Grid images are noticeably sharper. Clicking any image opens a panel showing the full-resolution original with no visible Drive credentials in the browser network tab.

---

## Day 5 — AI Tagging Pass

**Goal:** Every image has structured tags, a caption, and filmography.

- Write Gemini Flash 2.0 tagging prompt using full tag taxonomy
- Prompt returns structured JSON: tags (all categories) + one-sentence caption + filmography (title/director/DP/year for film stills) + camera/format if detectable
- Aspect ratio auto-detected from image dimensions (free)
- Build batch tagging pipeline — process all untagged images in queue
- **Run bulk tagging pass on existing library (~$1 total)**
- Review sample of results; refine prompt if needed

**Done when:** 20 random images reviewed and tags look accurate and useful.

---

## Day 6 — Core Search (Part 1): Tag Chips

**Goal:** Search by tags with live-updating results.

- Tag autocomplete: as user types, matching tags from DB appear in real time
- Selecting a tag locks it as an AND filter chip
- Results grid narrows instantly on each chip added
- Chips are removable (click X)
- Unlimited chips — no cap

**Done when:** Can filter to `[night] [exterior] [motivated]` and get accurate results.

---

## Day 7 — Core Search (Part 2): NL Fallback + Color + Bookmarks

**Goal:** Full search experience working end to end.

- NL fallback: typed text with no matching tags → Gemini interprets → filter chip applied, styled differently
- Chips and NL phrases coexist in same search bar
- Color extraction at ingest (Pillow) added to sync pipeline
- Color swatch picker in UI → filter by dominant color
- **Bookmarked searches:** save any filter combination as a named preset → one-click recall

**Done when:** Can search `[night] [exterior] "something lonely and desperate"` + pick a warm color swatch, all active simultaneously. Can save and recall that search.

---

## Day 8 — Image Detail Panel (Full)

**Goal:** Click any image and see everything about it.

- Full-size image via Flask proxy (already built Day 4 — wire up here)
- AI caption displayed
- All tags shown by category — click any to edit or remove inline
- Filmography metadata (title, director, DP, year)
- Camera/format
- Favorite (star) and Flag buttons
- Add to deck / scene picker (deck system built Day 10)
- Source info: filename, folder, date added, aspect ratio

**Done when:** Clicking any image opens a complete metadata panel with editable tags and working favorite/flag buttons.

---

## Day 9 — CLIP + Similar Images

**Goal:** "Find Similar" returns visually and semantically related images.

- Write one-time local Python script to generate CLIP embeddings for all images
- Run script on Mac → vectors stored in SQLite
- Add CLIP embedding step to sync pipeline for future new images
- Similar images endpoint: cosine similarity on CLIP vectors + tag overlap score combined
- "Find Similar" button on image detail panel → ranked results grid

**Done when:** Clicking "Find Similar" on a moody backlit night exterior returns other moody backlit night exteriors.

---

## Day 10 — Tag Mode + Smart Co-occurrence Suggestions

**Goal:** Bulk tag editing in seconds, with smart suggestions.

- Toggle Tag Mode from main UI
- Multi-select: individual clicks, box-select, select all in current results
- Bulk apply: type or pick tag → applied to all selected instantly
- Bulk remove: shared tags across selection shown → click X to remove from all
- Custom tag creation on the fly
- **Smart co-occurrence suggestions panel (pure SQL math, free)**

**Done when:** Can select 100 BTS images, type `seamless-paper`, hit apply, and see smart suggestions appear.

---

## Day 11 — Decks + Scenes

**Goal:** Create project lookbooks and organize images into named scenes.

- Deck CRUD: create, rename, delete
- Add images to deck from any search or browse view
- Scene creation within a deck
- Drag images between scenes
- Deck view: collapsible scene sections, dense grid per scene

**Done when:** Can create a "30 FAD Lookbook" deck with scenes and populate each from search results.

---

## Day 12 — Storyboard Mode + Obsidian Export

**Goal:** Sequence images with notes; export to Obsidian vault.

- Storyboard mode within a scene: drag images into specific order
- Add text note to each image in the sequence
- **Obsidian markdown export:** deck → `.md` file with images as URL embeds pointing to app thumbnail server
- Read-only share link per deck (token-based, no login required for viewer)

**Done when:** Can sequence 10 images with notes, export `.md`, drop into Obsidian, see images render inline.

---

## Day 13 — Analytics + Utility Views

**Goal:** Know what's in your library and what your eye gravitates toward.

- Analytics dashboard: tag frequency heatmap, source type breakdown, mood distribution, location spread, time of day distribution, library growth over time
- Recently Added strip (images from last sync, on home view)
- Favorites view (all starred images)
- Flagged queue (all flagged images, clearable)

**Done when:** Dashboard loads with real data. Recently Added shows last sync's images.

---

## Day 14 — Multi-User Auth (Shared Library Model)

**Goal:** Friends can log in and make their own lookbooks from your image library.

- Username/password login system
- Admin account (you) + additional user accounts
- Each user has their own: decks, scenes, favorites, flags, bookmarked searches
- All users search the same shared image library
- Admin controls: add/remove users

**Done when:** A friend can log in, search the library, build a lookbook, and share it — without seeing your private decks.

---

## Day 15 — Polish + Mobile

**Goal:** App feels finished and works on every device.

- Loading states, error messages, empty states throughout
- Mobile browser usability pass (functional on iPhone)
- Performance: lazy-load thumbnails, paginate large result sets
- Edge cases: sync errors, Drive permission issues, empty library states

**Done when:** App runs smoothly on phone, handles errors gracefully, feels production-ready.

---

## Day 16 — Fly.io Migration *(CANCELLED — see note)*

~~**Goal:** Cut Railway, move to Fly.io free tier. Save ~$60/year.~~

**Cancelled July 16, 2026.** Fly.io killed its free tier for new accounts in
October 2024 — a small always-on app there now runs ~$2–5/month, roughly the
same as Railway. Migrating would be a day of infrastructure risk for near-zero
savings. Staying on Railway until the real $0/month move: Day 18 (NAS), once
hardware is ready. No Fly.io work planned before then.

---

## Day 17 — Personal Libraries per User *(COMPLETE — see Session Log)*

**Goal:** Each user connects their own image source and has an isolated library.

- Google OAuth login (users sign in with Google account)
- Each user sets their own image folder
- Per-user tagging pass with Gemini
- Fully isolated images, tags, decks per user

**Done when:** A friend can connect their own folder and have a completely separate library.

**Shipped July 16, 2026 (V17).** Built differently than originally scoped:
folder access goes through the shared service account (friends share their
Drive folder with the robot email + paste the link), NOT Google OAuth — OAuth's
`drive.file` scope was verified to only grant access to files the app itself
creates, so it could never have read a friend's existing folder. Per-user
Gemini keys shipped separately as V16 the day before. Full detail in the
Session Log.

---

## Day 18 — NAS Migration *(when hardware is ready — future)*

**Goal:** Move everything off cloud hosting entirely. $0/month forever.

- Set up Docker on Ugreen NAS
- Deploy Frame Atlas container on NAS
- Move Inspiration Images folder from Google Drive to NAS local folder
- Update sync worker: swap Google Drive API → local filesystem watch
- Run one-time script to remap `source_file_id` in SQLite from Drive IDs → local filenames
- Install Tailscale on NAS + all devices
- Confirm access from MacBook, iPhone, any device with internet

**Done when:** Frame Atlas loads on MacBook via Tailscale with all images, tags, and decks intact. Google Drive subscription cancelled.

---

## Day 19 — Browser Extension *(Post-MVP, future)*

**Goal:** Clip any image from the web directly into the library.

- Chrome extension (Manifest V3)
- Right-click any image on any page → "Add to Reference Library"
- Image saved to sync folder → picked up on next sync → auto-tagged

**Done when:** Right-clicking a film still on Letterboxd adds it to the library.

**Shipped as V25 (web clipping).** Built as a direct-capture extension (`POST /api/clip`
takes the pixels from the browser) rather than a save-to-Drive-then-sync flow — see
CLAUDE.md's "Web clipping (V25)" section.

---

# ═══ PHASE 2 — THE PITCH LAYER ═══
*Planned August 6, 2026, after a code review + a workflow review from the standpoint of a
creative director prepping an agency job. The finding that drove this phase: **Frame Atlas is
excellent at FINDING references and thin at PRESENTING them.** Search, tagging, colour and
decks are genuinely strong. But there's no export, no way to present from the app, no way to
reorder the sections of a lookbook, and no way for a client to react to one. For a pitch, that
last mile is the whole job.*

*Days below are sequenced so the agency-facing work lands first. Day 24 (performance) is the
biggest day-to-day quality-of-life win and can be pulled forward any time the app feels slow.*

---

## Day 20 — Deck Ordering + Crop Selection Fix *(V38 — small, start here)*

**Goal:** Stop the two things that interrupt the flow of actually building a lookbook.

- **Crop selection bug:** after "Crop all" runs, the selection and Select Mode stay active, so
  you have to manually hit Exit before selecting new photos. Clear both when a crop batch
  actually starts — but KEEP the selection if the modal was cancelled without cropping
  (you may have opened it by accident). Same pattern V35 already uses for bulk delete.
  Cause is at `Home.jsx` → `<CropModal onClose={() => setCropImages(null)} />`, which closes
  the modal without touching selection state.
- **Scene reordering:** `PATCH /api/scenes/<id>` currently only renames. Add ordering so
  scenes can be dragged up/down within a deck. For a pitch, section order IS the argument —
  realizing "tone should come before palette" currently means deleting and rebuilding.
- **Surface Storyboard mode better:** reordering photos *within* a scene already works
  (Day 12, V11) but it's hidden behind a "⊞ Storyboard" button that reads as an export or a
  view toggle. Ryan didn't know it existed. Make it obviously the way to sequence a scene.

**Done when:** Cropping two photos and returning home leaves nothing selected; scenes can be
dragged into a new order and it sticks; the way to sequence photos in a scene is findable
without being told.

---

## Day 21 — PDF Lookbook Export *(V39)*

**Goal:** A pitch needs a leave-behind — the file that sits in the client's inbox after the
meeting and gets forwarded to people who weren't in the room. Right now the only output
Frame Atlas has is a live web link.

- Export any deck to PDF, with the layout chosen at export time:
  - **One image per page, full bleed** — the pitch document. Scene name as a title card,
    each frame its own page, storyboard note underneath.
  - **Contact sheet grid** — multiple frames per page, scene by scene. The working
    reference / crew handout.
- Respects storyboard order and scene order (so Day 20 is a genuine prerequisite).
- Deck title page; frames without notes just omit the caption rather than leaving a gap.

**Watch out:** this needs a PDF library added to `backend/requirements.txt`, which adds 3+ min
to every Railway deploy from then on. Pick one deliberately — `reportlab` is the obvious
candidate and is pure-Python. Confirm the image resolution question before building: a PDF
built from 600px thumbnails will look soft when printed or projected, so this likely needs to
pull full-res from Drive at export time even though presentation mode does not.

**Done when:** A 20-frame deck exports to a PDF that Ryan would actually send to an agency,
in both layouts.

---

## Day 22 — Presentation Mode *(V40)*

**Goal:** Present a lookbook from the app, full screen, without a browser header bar in shot.

- Fullscreen deck presentation, keyboard-driven (arrows / space to advance, Esc to exit).
- Scene name as a title card between sections; storyboard note shown under the frame
  (toggleable — sometimes you're talking and don't want text competing).
- **Uses the existing 600px thumbnails** (Ryan's call). They're already loaded in the grid,
  so this is nearly free to build and there's zero loading pause between frames.
  **Build it so the image source is a single swappable line** — if it ever looks soft on a
  real projector, upgrading to full-res-with-preload should be a small change, not a rewrite.

**Done when:** Ryan can open a deck, hit present, and run a pitch off the laptop with nothing
on screen but the work.

---

## Day 23 — Client Feedback Loop *(V41)*

**Goal:** Close the loop that currently happens over text and email and gets reconciled by hand.

- On the existing public `/share/<token>` link, viewers can:
  - **Pick** a frame (a "this one" toggle) — the signal that matters most
  - **Comment** on a specific frame
- **No login required** — the whole value of the share link is that an agency producer opens it
  without making an account, and forcing signup kills response rate. Viewer types their name
  once, stored in their own browser, and everything they leave is attributed to it.
- **Viewers see each other's comments** (Ryan's call) — collaborative, the whole agency side
  sees one conversation. Accept the tradeoff that early opinions can anchor later ones.
- Owner-side summary on the deck: which frames got picked, by whom, and every comment in
  one place — replacing the manual reconciliation.
- Owner controls: feedback can be switched off per deck, and the owner can delete any comment.
  The share token is unguessable, but anyone it's forwarded to can post, so these are the
  pressure valve.

**Done when:** Ryan shares a lookbook, two people mark picks and leave notes without signing
in, and Ryan sees a consolidated summary.

---

## Day 24 — Performance: Thumbnail Caching + Indexes + CI *(V42)*

**Goal:** The biggest day-to-day quality-of-life win available. Pull this forward if the app
ever feels slow.

- **Thumbnails are the problem.** They're stored as base64 text inside the search response, so
  the browser cannot cache them — there's no image URL to remember. Measured at the real
  library size (3,499 images, ~81 KB each): **~6.5 MB per page of 60**, ~32 MB to scroll 300,
  and it all re-transfers on the next visit. That's 2.6 s on good 4G but **10.4 s on hotel/set
  wifi** and ~26 s on weak LTE. Fix: serve each thumbnail from its own cacheable URL
  (`/api/images/<id>/thumb`) with proper cache headers, and have the service worker cache
  those (it currently and correctly refuses to cache anything under `/api` — thumbnails would
  need to be a deliberate exception, since unlike `/api/auth/me` they're immutable content).
  This also makes offline mode genuinely useful instead of app-shell-only.
- **Database indexes — there are currently none.** Benchmarked at real scale
  (3,499 images / 108,469 tag rows): single tag search **11.0 ms → 0.18 ms (60×)**, two-tag
  search **9.7 → 0.50 ms (19×)**, autocomplete **31.0 → 7.9 ms (4×)**. Index build took
  291 ms and cost 3 MB. Honest framing: **invisible at today's size** — 11 ms is nothing —
  but it grows in a straight line, and autocomplete fires on every keystroke, so around
  ~14,000 images it starts to feel laggy. Cheap insurance, not an emergency.
- **CI:** 27 test scripts exist and nothing runs them automatically — they only execute when
  someone remembers, which is how the "7 of 23 failing on main" situation happened in July.
  A GitHub Action on every push is ~20 lines.

**Done when:** A revisit to the home grid loads from cache instead of re-downloading; tests
run themselves on every push.

---

## Day 25 — Security + Reliability Hardening *(V43)*

**Goal:** Close the gaps that are fine for an invite-only tool but shouldn't stay open forever.

- **No limit on login attempts** — passwords can be guessed as fast as requests can be sent.
  (Password *hashing* itself is correct — werkzeug pbkdf2.) Add throttling/lockout.
- **Friends' Gemini API keys are stored as plain readable text** in `users.gemini_api_key`.
  If the DB file leaked, those keys are usable and they bill to your friends. Encrypt at rest.
- **13 silent `except: pass` blocks** swallow errors without a trace — this is the pattern that
  made the V27 crop failure invisible for weeks. Audit them; log what's being discarded.
- **`backend/library.db` is committed to git** (currently empty/harmless, but a real one would
  put the whole library in git history). Untrack it and add to `.gitignore`.
- Checked and CLEAN, no action needed: SQL injection — every dynamic query uses hardcoded
  table names or generated `?` placeholders.

---

## Day 26 — Structural Refactor *(V44 — ongoing, no visible payoff)*

**Goal:** Lower the cost of every future feature. Nothing here is broken; this is about why
small changes keep having surprising side effects.

- `backend/app.py` is **6,376 lines** — every endpoint, AI tagging, Drive sync, crop, colour
  math, backups, in one file. Split by domain.
- `frontend/src/pages/Home.jsx` is **1,855 lines with 36 separate pieces of state**. The V35
  stale-selection bug lived exactly here, and so did the Day 20 crop-selection bug.
- Do this incrementally, one domain at a time, with the test suite green before and after —
  never as one big-bang rewrite.

---

## Summary

| Day | Focus | Key Output |
|---|---|---|
| 0 | Account setup | 4 credentials in hand ✅ |
| 1 | Skeleton deploy | Live URL on any device ✅ |
| 2 | Drive sync | Images appear from Drive ✅ |
| 3 | Infrastructure fix + image grid | Sync working, grid rendering ✅ |
| 4 | Thumbnail quality + full-res proxy | Sharp grid, original on click |
| 5 | AI tagging | Every image tagged + captioned |
| 6 | Tag chip search | Live filter by tags |
| 7 | NL + color + bookmarks | Full search experience |
| 8 | Image detail panel (full) | Click image → all metadata |
| 9 | CLIP + similar | Find visually similar images |
| 10 | Tag mode + suggestions | Bulk tag hundreds of images |
| 11 | Decks + scenes | Project lookbook organization |
| 12 | Storyboard + Obsidian | Sequenced export + vault sync |
| 13 | Analytics + utility | Library insights + utility views |
| 14 | Multi-user auth | Friends can log in |
| 15 | Polish + mobile | Production-ready |
| 16 | Fly.io migration | ~~Cut Railway~~ CANCELLED |
| 17 | Personal libraries | Per-user folders ✅ *(V17)* |
| 18 | NAS migration | $0/month forever *(parked — needs hardware)* |
| 19 | Browser extension | Web clipping ✅ *(V25)* |
| **— PHASE 2: THE PITCH LAYER —** | | |
| **20** | **Deck ordering + crop selection fix** | **Scenes reorder; selection clears after crop** *(V38 — next)* |
| 21 | PDF lookbook export | A file you can send an agency *(V39)* |
| 22 | Presentation mode | Present a pitch from the app *(V40)* |
| 23 | Client feedback loop | Picks + comments on share links *(V41)* |
| 24 | Performance: caching + indexes + CI | 6.5 MB/page → cached; tests self-run *(V42)* |
| 25 | Security + reliability hardening | Login throttling, key encryption *(V43)* |
| 26 | Structural refactor | Lower cost of every future change *(V44, ongoing)* |

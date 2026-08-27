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

*Days below are sequenced so the agency-facing work lands first. Day 25 (performance) is the
biggest day-to-day quality-of-life win and can be pulled forward any time the app feels slow.*

---

## Day 20 — Deck Ordering + Crop Selection Fix *(V38 — COMPLETE)*

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

**Shipped August 7, 2026 (V38).** All three pieces done: `CropModal` now tells its caller
whether a crop batch actually started vs. every other way it closes, so Home only clears
Select Mode on a real start. Scene reordering added as `POST /api/decks/<id>/scenes/reorder`
(mirrors the existing photo-storyboard-order endpoint) with drag-and-drop in `DeckDetail.jsx`,
using a distinct `dataTransfer` type so it can't collide with the existing photo-into-scene
drag. The Storyboard button was renamed "↕ Reorder Photos" and given a filled, prominent style.
Full detail in CLAUDE.md's "Cropping" and "Scene reordering" sections.

---

## Day 21 — DP Technical Notes + Full-Text Search *(V39 — COMPLETE)*

**Goal:** Frame Atlas's taxonomy is fluent in mood and light but has nowhere to record what it
actually took to get a shot — camera, lens, filtration, gear. Found in the ASC-DP review: every
technical tag is an AI *guess from pixels*; nothing lets Ryan record the real facts himself.

*Planned August 7, 2026, from Ryan's own example: "we shot at T12 on the Alexa Mini LF, used
the probe lens, a rain machine, and had the lights on a colored chase" — and wanting to search
"alexa mini lf" or "colored chase" and pull that photo straight up.*

**Fields (on the `images` table, any photo — Ryan's call, not restricted to `my_work`):**
- Structured, so these stay reliable filters and don't drown in prose: **Camera / Rig**,
  **Lens**, **Lens Filter**, **Stop**. `Stop` is text, not a number — T-stops don't fit a
  numeric column cleanly (`T1.2`, `T2.8`, `T4`).
- One free-text **"On-Set Notes"** box for everything else — rain machine, colored chase,
  technique, anything that doesn't fit a clean field. All fields optional; none block saving.
- Edited in `ImageDetail.jsx`, its own section alongside caption/tags/filmography.

**Search — SQLite FTS5, confirmed available (`sqlite3.sqlite_version` 3.43.2 locally; the
Railway image is `python:3.11-slim`, whose SQLite ships FTS5 compiled in — verify on first
real deploy, since a stripped base image is the one way this could differ):**
- Ryan asked for something like Obsidian's Omnisearch — tokenized, ranked, forgiving of
  imprecise phrasing, not a brittle `LIKE '%...%'`. FTS5 is SQLite's own built-in full-text
  index: tokenizes on word boundaries, ranks by relevance (BM25), and needs no new pip
  dependency (unlike the PDF library Day 22 needs) — it ships inside Python's `sqlite3` module.
- A virtual table (`notes_fts`) mirrors the free-text + structured fields, kept in sync via
  SQLite triggers on insert/update/delete of the `images` row — not from every callsite in
  `app.py` by hand, so it can't drift out of sync the way two hand-copied filter functions did
  before `build_search_filters()` (V32).
- A note match becomes **its own chip style** (Ryan's call) — visually distinct from a gold tag
  chip or a violet NL chip, e.g. amber "🔧 alexa mini lf" — so it's clear at a glance why a
  photo matched. Combines (AND) with tag chips and NL chips, same as every other filter type.
- Runs BEFORE the paid Gemini NL-interpret fallback, not instead of tag matching — free,
  instant, and this ordering is also a small standing cost win: fewer phrases fall through to a
  billed API call once notes search can answer them first.

**Watch out:**
- FTS5's tokenizer treats `-` and `.` as word breaks by default — "Alexa Mini LF" indexes fine,
  but confirm `T1.2` round-trips (`T`, `1`, `2` as separate tokens is likely fine for search,
  just don't assume the stored text renders back with the period intact without checking).
- This is genuinely new territory — no existing free-text search anywhere in the app to model
  the migration/backfill pattern on. Build the FTS trigger sync as its own small test harness
  before touching real data, same discipline as `test_v33_color_fix_locally.py`.

**Done when:** Ryan can enter camera/lens/stop/filter + a paragraph of notes on a photo, then
search "alexa mini lf" or "colored chase" from the same search bar and get that photo back
with a distinct chip showing why it matched.

**Shipped August 8, 2026 (V39).** Built as planned, with one confirmed refinement over the
original draft: rather than notes search running only as a silent Enter-time check before the
Gemini fallback, note matches show up **live in the autocomplete dropdown** as you type — Ryan's
call, for consistency with how tag/film/aspect-ratio suggestions already work in that same
dropdown. This still satisfies the original goal (free, instant, runs before any billed Gemini
call) since Enter always picks from the dropdown when it's showing; the live-suggestion path
just means seeing the match before committing to it, instead of finding out only after pressing
Enter. Permission model was also settled during planning: DP notes fields are **owner-or-admin**,
the first metadata field in this app editable by a friend on their own photo (every other edit
endpoint — tags, filmography — turned out to be admin-only, not owner-scoped as first assumed;
corrected and re-confirmed with Ryan before building). Full detail in CLAUDE.md's "DP technical
notes + full-text search" section.

---

## Day 22 — PDF Lookbook Export *(V40 — COMPLETE)*

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

**How it actually shipped:**
- `reportlab==4.2.5` it is — pure-Python, first new dependency since `google-genai`.
- **"Full bleed" became "letterboxed on a fixed page."** True full bleed means cropping a
  frame to fill a fixed page shape, and silently re-framing a cinematographer's shots would
  make the export worse than useless. Every page is landscape US Letter; the image scales to
  fit with its aspect ratio intact and centres on the near-black page, so leftover space
  reads as intentional margin.
- **Thumbnails, not full-res from Drive** — the opposite of what this entry predicted. The
  stored thumbnails are 800px/q85 (not the 600px this entry assumed), and pulling full-res
  would mean a Drive round trip per frame at export time. Shipped on thumbnails so Ryan can
  judge real output before paying that cost; swapping the source later is a contained change.
- **Hand reportlab JPEG bytes, never a PIL image object.** Measured on a real 20-frame deck:
  13.7MB via PIL vs 1.5MB via bytes, because bytes embed as DCTDecode while a PIL object gets
  Flate-compressed raw pixels. The first number doesn't survive an email attachment limit,
  and emailing the file IS the feature.
- 51 checks across two test scripts. Full technical detail in CLAUDE.md.

---

## Day 23 — Presentation Mode *(V41 — COMPLETE)*

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

**How it actually shipped:**
- **800px thumbnails, not 600px** — this entry's own number was stale (corrected at the source
  in V40; this file inherited the mistake). Same actual asset the grid already has in memory.
- **Four decisions confirmed with Ryan before writing code, none obvious from the plan above:**
  notes visibility remembers your last choice (localStorage) rather than a fixed default; title
  cards appear only when a deck has 2+ non-empty scenes, so a one-scene deck skips straight to
  its first frame; advancing past the last frame holds there — no loop, no end card, no
  auto-exit, so you can never accidentally reveal you've run out of material mid-pitch; and
  Present lives only on the owner's deck page for V41 (the public share link gets it in Day 24
  or later, not bundled here).
- The running order (scene order, photo order, which sections get title cards) is a pure
  function in `frontend/src/presentationOrder.js`, not inline in the component — same reasoning
  as V32's shift-click range selection: this kind of logic can silently be WRONG rather than
  visibly broken, and browser automation can't reliably drive keyboard interactions to catch it,
  so it needs to be reachable from code. 20 checks in `scripts/test_presentation_order.mjs`.
- Verified end-to-end in a real browser via `scripts/run_local_for_browser_check.py` against a
  seeded 3-scene, 9-photo deck — which surfaced and fixed an unrelated bug: that harness broke
  the moment V40 added `from pdf_export import ...` to `app.py`, because the harness patches
  `app.py` into a temp directory without ever adding `backend/` to `sys.path`. Nobody had run it
  since V40 landed.

---

## Day 24 — Client Feedback Loop *(V42 — COMPLETE)*

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

**How it actually shipped:**
- **Existing shared decks default OFF, new decks default ON** — the one real fork from "no
  login required" that needed an explicit decision. A link already sitting in an agency inbox
  must not start accepting public comments the moment this shipped; Ryan flips it on per deck
  from the Share panel when he wants it.
- The "browser, not a login" identity is two separate values: a random token the viewer's
  browser generates for itself (invisible, controls the actual pick toggle) and the name they
  type (can change any time without losing pick history). One name prompt, on the first pick or
  comment, whichever comes first — not a gate in front of the whole page.
- Owner summary is most-picked-frame-first, not deck order — the headline ("which one won") is
  the first thing Ryan sees, not something he has to scan for.
- Found and fixed two real bugs during browser verification: a stranded empty row when deleting
  the only comment on a picks-less frame, and a custom on/off switch that was a plain `div` with
  no keyboard access or accessible role — the same gap that made it briefly untestable through
  the accessibility tree is a gap a screen-reader user would hit too.
- Also found (unrelated to this feature, but blocking honest verification of it): 29 of the
  repo's `test_*_locally.py` scripts had been silently `ModuleNotFoundError`-ing since Day 22 —
  fixed in its own commit, separate from V42 itself.

---

## Day 25 — Performance: Thumbnail Caching + Indexes + CI *(V43 — COMPLETE)*

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

**Shipped August 10, 2026 (V43), in two passes.**

*Part 1 — indexes + CI:* 5 `CREATE INDEX IF NOT EXISTS` statements added to `init_db()`
(idempotent, run on every boot, no self-disabling flag needed). GitHub Actions workflow runs
all 36 `scripts/test_*_locally.py` + `.mjs` scripts on every push — turned out every one already
builds its own throwaway synthetic database, so no fixtures or secrets were needed after all,
simpler than originally planned.

*Part 2 — thumbnail caching:* `build_image_dict()` now returns a `/api/images/<id>/thumb?v=<md5_checksum>`
URL instead of embedded base64, for every authenticated context (search, decks, similar-images,
favorites/flagged/recent). One real design conflict surfaced during planning and got resolved
before writing code: the same function that serves search results also serves the public
`/share/<token>` page, which has no login — so a login-gated thumbnail URL would have broken
public share links. Fixed with a `public=` flag threaded through `build_image_dict()` →
`_fetch_image_dict()` → `_deck_payload()`: the public share view is the one deliberate exception
that still embeds base64, everything else gets the cacheable URL. The `?v=` is the image's own
checksum, so a crop (which rewrites it) forces a fresh fetch while an unrelated re-tag doesn't.
`frontend/public/sw.js` gained one narrow, documented exception to its "never cache /api" rule
for exactly this URL shape — which is also what keeps offline deck viewing working, since
`useOfflineCache.js` still stores the deck JSON verbatim and the service worker is what makes
the pictures those URLs point to actually available with no connection. No frontend `.jsx` file
needed to change — every consumer already just does `<img src={img.thumbnail}>`, and a URL
string works exactly like a data URI there. Verified live in a real browser: a repeat page load
fired zero network requests for thumbnails at all, served straight from the browser's own disk
cache. Full detail in CLAUDE.md's "Thumbnails" and "Offline support" sections.

---

## Day 26 — Security + Reliability Hardening *(V44 — COMPLETE)*

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

**Shipped August 11, 2026 (V44).** All four planned items done, plus one bug found that wasn't
on the list and turned out to matter more than anything that was:

- **Login throttling** ships as an escalating lockout keyed to the *account*, never the caller's
  IP — 5 wrong passwords free, then a lockout that doubles each further failure (30s → 1hr cap).
  Deliberately account-scoped rather than IP-scoped: this app is routinely used from shared
  networks (V43's own notes cite hotel/set wifi), where IP throttling would let one guesser lock
  out everyone else on that connection. The lock is checked **before** the password hash, so a
  locked account rejects even the *correct* password — otherwise the throttle would still leak
  "that one was right" one guess at a time. Verified live in the real login screen, including
  that the lock releases on its own with no admin action needed.
- **Gemini keys are Fernet-encrypted at rest**, cipher key in its own new Railway variable
  (`FA_ENCRYPTION_KEY`, deliberately separate from `FLASK_SECRET_KEY` so rotating one can't
  silently break the other). No migration pass needed: a legacy plaintext key just reads as-is
  and quietly upgrades to encrypted the next time it's saved. Verified against the actual
  database file on a live server, not just the API response, that a saved key is genuinely
  unreadable at rest.
- **The `except: pass` count was wrong — 16, not 13.** 13 are `init_db()`'s routine
  column-migration blocks, now silent only on the expected "column already exists" case and
  loud on anything else. 3 more outside `init_db()` now log too. One (aspect-ratio parsing) was
  deliberately left silent after confirming it's actually unreachable and fires on every
  autocomplete keystroke — logging it would've been pure noise.
- **`backend/library.db` untracked**, confirmed first that it was 0 bytes in its only commit
  (so no real data was ever in git history) before removing it.
- **The bug not on the list:** `Dockerfile` was missing `PYTHONUNBUFFERED=1`. Without it, every
  `print()` after boot sat in a buffer and was lost outright if the process restarted or crashed
  first — confirmed directly, a whole local session produced zero log output beyond startup.
  This makes the entire except:pass audit above pointless on arrival (a log line that never
  reaches Railway helps nobody), and is very likely part of why the V27 crop failure went
  unnoticed for weeks. One-line fix; verified logs now arrive in real time.
- 33 checks in `scripts/test_security_hardening_locally.py`, all 33 existing test scripts
  re-run clean, and full live-browser verification: normal login, a real lockout rendering in
  the actual login form (including that it rejects the *correct* password while locked and
  releases on its own), and a saved Gemini key confirmed encrypted in the live database file.

---

## Day 27 — Structural Refactor *(V45 — COMPLETE, both parts; no visible payoff)*

**Goal:** Lower the cost of every future feature. Nothing here is broken; this is about why
small changes keep having surprising side effects.

- `backend/app.py` is **6,376 lines** — every endpoint, AI tagging, Drive sync, crop, colour
  math, backups, in one file. Split by domain.
- `frontend/src/pages/Home.jsx` is **1,855 lines with 36 separate pieces of state**. The V35
  stale-selection bug lived exactly here, and so did the Day 20 crop-selection bug.
- Do this incrementally, one domain at a time, with the test suite green before and after —
  never as one big-bang rewrite.

**Part 1 shipped August 12, 2026 (V45).** `app.py` had grown to 7,337 lines by the time this
started, not the 6,376 above. The pure maths — bytes and numbers in, numbers out, nothing
touching the database, Drive or Flask — moved into four flat files beside it: `colors.py` (343),
`perspective.py` (228), `imaging.py` (131), `fingerprint.py` (122). **7,337 → 6,626 lines.**
Every moved function is character-for-character what it was, diffed against the original before
each cut.

**Why only the pure parts, and what that unblocked.** All 34 `scripts/test_*_locally.py` copy
`app.py` — one single file — into a temp directory, string-patch `DB_PATH` inside it, and import
that copy. Move database-touching code out and the trick breaks *quietly*: the tests keep
running while the moved module resolves from the real `backend/` with the production DB path
intact. Pure modules carry no `DB_PATH`, so this slice needed zero harness changes and could be
verified against an identical pass/fail baseline captured on unmodified `main`.

Names are imported back into `app.py` rather than left as module references, keeping its public
surface identical — the test scripts read `mod.color_matches`, `mod.PALETTE_DARK_V`, `mod._hsv`
directly. Confirmed first that none of them *reassign* anything in the moved set.

**Part 2 shipped August 26, 2026 (V45 part 2).** `DB_PATH` in `app.py` now reads
`DB_PATH = os.environ.get('FA_DB_PATH', '/app/data/library.db')` — one line, one word changed
from a plain assignment. `FA_DB_PATH` is unset in production, so Railway needed no config change
and the real path is untouched.

The actual count was **36 scripts, not 34** (a 37th, `test_pdf_export_locally.py`, tests
`pdf_export.py` directly and never touched `DB_PATH` at all). Every one of the 36 dropped the
copy-into-a-tempdir-and-string-patch dance entirely — not just swapped one line for
`os.environ.setdefault`, per the fuller cleanup Ryan chose over a minimal-touch edit. Each script
now sets `FA_DB_PATH` to its own throwaway path and loads `backend/app.py` directly via
`importlib.util.spec_from_file_location` with a unique module name per load — the three scripts
that boot more than one independent app instance in a single run (`test_schema_guard_locally.py`,
`test_self_test_locally.py`, `test_security_hardening_locally.py`) already gave each load a
unique spec name for exactly this reason, so loading the same real file repeatedly with a fresh
env var each time works identically to loading distinct temp copies.

Applied as a scripted regex transform across all 36 (hand-editing each would have been the real
risk), verified against an identical pass/fail baseline captured on unmodified `main` first, and
confirmed no regressions after. **The transform itself introduced one bug, caught before any test
ran:** `test_security_hardening_locally.py` builds its patched-file path from a variable
(`app_path = os.path.join(workdir, "app.py")`) rather than inline, so the mechanical
find-and-replace rewrote that variable's target to `backend/app.py` itself while leaving the very
next line — `open(app_path, "w").write(patched)` — intact, which would have **overwritten the
real `backend/app.py`** the first time that script ran. Fixed by hand in that one file before
running anything. Every other script builds the path inline and wasn't affected. All 36 Python
scripts plus the 3 `.mjs` pure-logic tests pass clean, before and after.

This unblocks extracting Drive, sync, tagging and the crop worker out of `app.py` — deliberately
not started this session, per plan. `Home.jsx` remains untouched.

---

# ═══ PHASE 3 — THE REFACTOR ═══
*Planned August 26, 2026. Day 27 got the **pure maths** out of `app.py` (colour, fingerprint,
imaging, perspective — bytes and numbers in, numbers out) and then reworked the test harness so
database-touching code is allowed to live outside `app.py` too. That second part was the whole
point: it unblocked everything below. Nothing here is a new feature. `backend/app.py` is still
**~6,960 lines** — every endpoint, plus AI tagging, Drive sync, the crop worker, backups, image
hydration, search — and every small change to it keeps having side effects in unrelated places
because it's all one file sharing one set of globals.*

*This phase splits it apart **one module per session**, in dependency order (leaf pieces first,
the things everything else leans on), with the full test suite green before and after each cut
and every moved function diffed character-for-character against the original — the exact
discipline V45 used. No session tries to do two modules. If a cut turns out bigger than a
session, it stops half-done only at a point where the suite is green, and finishes next time.*

### The two rules every session in this phase follows

1. **`app.py` is the only file allowed to `import` the others.** Each new module imports from
   `core.py` (the shared foundation, built Day 28) and from the already-pure V45 modules —
   never from `app.py`. This is what keeps Python from tying itself in a knot (a "circular
   import" — two files each waiting on the other to finish loading).
2. **Call sites get qualified, tests get updated.** Once `get_drive_service` lives in `drive.py`,
   code in `app.py` that used to call it by its bare name now calls `drive.get_drive_service()`,
   and the ~10 test scripts that swap in a fake Drive change from `mod.get_drive_service = fake`
   to `drive.get_drive_service = fake`. Ryan chose this (updating the tests) over the alternative
   of having `app.py` quietly re-export every moved name to keep the tests untouched — fewer
   hidden trapdoors, even though it's more files touched per session. The mechanical test-script
   edits are applied as one scripted transform and **eyeballed in the diff before anything runs**
   — V45 part 2's near-miss (a transform that would have overwritten the real `app.py`) is the
   standing reminder of why.

### Endpoints stay put — for now

Flask routes (`@app.route(...)` functions) are **not** moved in this phase. Moving them means
Flask "blueprints," which is a real conceptual step and a separate risk. Instead, each session
moves the *worker and helper functions* a route calls, and leaves the route itself in `app.py`
as a thin wrapper. Whether to blueprint the routes afterwards is a decision for the end of the
phase, once `app.py` is down to mostly-just-routes and we can see how big that actually is.

---

## Day 28 — Foundation: `core.py` + `schema.py` + Security Hardening *(V70 — COMPLETE)*

**Goal:** Create the shared base every later module imports from, secure session handling, and
add rate limiting on sensitive endpoints.

### Code Changes

- **`core.py`** — the genuinely shared, dependency-free pieces:
  - `get_db()` and `DB_PATH` (the database connection and where the file lives)
  - `_shuffle_key()`, `chunked()` / `SQL_PARAM_CHUNK`
  - `normalize_tag_value()`, `clear_ai_tags()`, `TAG_PLURAL_STRIP_EXCEPTIONS`,
    `MANUAL_TAG_CATEGORIES`
  - `CAT_COLORS`, `CAT_LABELS` (the 15-category tag taxonomy display maps)
  - `GEMINI_MODEL`, `GEMINI_PRICING`, `get_model_pricing()`
- **`schema.py`** — the ~920-line block that builds and migrates the database on boot:
  `init_db()`, `check_schema()`, `run_self_test()`, `missing_columns()`,
  `_is_duplicate_column_error()`, `load_embeddings_seed()`. This is close to a straight
  lift-and-shift of one contiguous region, and it's the single biggest line-count win in the
  whole phase.
- `app.py` imports both at the top and calls `schema.init_db()` etc. on startup exactly as now.

### Security Fixes (from spot-check, risk-level noted)

**Finding 1 (⚠️ Medium risk):** Session cookies missing secure flags.
- Add `app.config['SESSION_COOKIE_SECURE'] = True` and `app.config['SESSION_COOKIE_HTTPONLY'] = True` after the `SESSION_COOKIE_SAMESITE` line.
- Ensures cookies only ride on HTTPS and are inaccessible to JavaScript.

**Finding 2 (🔴 Medium-High risk, defer implementation):** Password reset tokens sent in JSON response instead of via email.
- **Current state:** `/api/auth/forgot-password` returns the token in JSON; no email is sent.
- **Fix deferred:** Integrate email sending (Flask-Mail or Mailgun) so tokens travel via email, not HTTP responses.
- **Why defer:** This is a feature enhancement (actually sending emails), not an immediate blocker. Tokens expire in 1 hour, are 256 bits of entropy, and one-time use only. Wire it up before shipping to friends; for now, document in CLAUDE.md that this path is for admin use only.

**Finding 3 (⚠️ Medium risk):** No rate limiting on sensitive non-login endpoints.
- Add Flask-Limiter to rate-limit `/api/auth/register` and `/api/auth/forgot-password` (e.g., 5 per minute per IP).
- Login already has account-based throttling (V44); this caps the public endpoints.
- ~10 lines of config in app.py.

**Watch out:**
- `init_db()` and the backfill functions (`backfill_palettes`, `backfill_phashes`,
  `backfill_notes_fts`) are intertwined — decide whether the backfills come with `schema.py` on
  Day 28 or wait for Day 31 (`images_common.py`) with the other palette code. Leaning: backfills
  wait, so Day 28 stays a clean schema-only cut.
- `test_schema_guard_locally.py` and `test_self_test_locally.py` both load the app fresh multiple
  times per run and will need their imports repointed.

**Done when:** `core.py` and `schema.py` exist, security fixes applied, `app.py` imports from them, the full suite is
green, and `app.py` has dropped ~1,000 lines with zero behaviour change.

**How it actually shipped (V70, August 26 2026):**
- `backend/core.py` (141 lines) — `get_db()` + a new `db_path()` (reads `FA_DB_PATH` live rather
  than snapshotting at import, so a multi-boot test harness gets the right file), `_shuffle_key()`,
  `chunked()`/`SQL_PARAM_CHUNK`, `normalize_tag_value()`/`clear_ai_tags()`/`TAG_PLURAL_STRIP_EXCEPTIONS`/
  `MANUAL_TAG_CATEGORIES`, `CAT_COLORS`/`CAT_LABELS`, `GEMINI_MODEL`/`GEMINI_PRICING`/`get_model_pricing()`.
  Imports nothing from the project — stdlib only.
- `backend/schema.py` (849 lines) — `_is_duplicate_column_error()`, `EXPECTED_COLUMNS`,
  `missing_columns()`, `check_schema()`, `init_db()`, `load_embeddings_seed()`. `init_db()` gained one
  parameter — `init_db(run_self_test=None)` — because `run_self_test()` **stayed in `app.py`** (it
  calls `_deck_access()`/`touch_deck()`, which live there, and schema.py may not import app.py). app.py
  passes it in at both boot sites. Every migration is byte-for-byte the original, verified by diffing
  the whole `init_db` body against pre-change `main`; the only additions are the two `run_self_test`
  lines and the new `rate_limit_hits` table.
- app.py imports every moved name straight back (`from core import …` / `from schema import …`), so its
  public surface is unchanged and **no `test_*_locally.py` needed repointing for the move** — verified
  that none of them monkey-patch a moved name. `test_schema_guard_locally.py` was the one edit: it greps
  source for `ALTER TABLE`, so its `open(...)` path moved from `app.py` to `schema.py`.
- **6,960 → 6,136 lines in `app.py`** (−824 net; ~904 moved out, ~80 of security code added back).
- Security: `SESSION_COOKIE_HTTPONLY=True` always; `SESSION_COOKIE_SECURE=True` except when
  `RUNNING_LOCALLY` (`FA_DB_PATH` set) — otherwise the Flask test client and localhost dev drop the
  cookie and every login-gated test breaks. Rate limiting is **hand-rolled** (Ryan's call over
  Flask-Limiter — no new dependency, no +3 min deploys): a `rate_limit_hits` table, `_rate_limited()`
  in app.py, 5 hits/60 s per IP on `/api/auth/register` + `/api/auth/forgot-password`, keyed on the
  last `X-Forwarded-For` entry, fails **open** on any DB error, and no-ops entirely when local. Reset
  token stays in the JSON response — documented admin-only in CLAUDE.md, email delivery still deferred.
- New test: `scripts/test_day28_hardening_locally.py` (22 checks — the split wiring, cookie flags, and
  the limiter in both local and simulated-production modes). Full suite 41/41 green before and after;
  verified live in a browser (login, grid, search, image detail, all endpoints 200).
- Also fixed in passing: `run_local_for_browser_check.py` and `diagnose_color_filter.py` had been
  broken since V45 part 2 (they string-patched a `DB_PATH = '...'` line that no longer exists) — both
  now load `backend/app.py` directly via `FA_DB_PATH`.

---

## Day 29 — Google Drive layer → `drive.py` *(V71 — COMPLETE)*

**Goal:** All Google Drive connection, auth, and folder-listing code in one file. This is the
layer sync, backup, crop, and upload all sit on top of, so it comes out early — and it's the one
Ryan named first.

- Moves: `get_drive_service()`, `get_user_drive_service()`, `get_user_credentials()`,
  `get_oauth_flow()`, `get_service_account_email()`, `parse_drive_folder_id()`,
  `list_images_in_folder()`, `get_root_folder_id()`, `get_or_create_removed_folder()`,
  `download_drive_file()`, `drive_error_reason()`
- Constants: `REMOVED_FOLDER_NAME`, `PERSONAL_LIBRARY_CAP`, `UPLOAD_SCOPES`
- Depends only on `core.py` + the Google client libraries.

**Watch out — this is the highest-blast-radius cut in the phase:**
- Roughly **10 test scripts** swap these functions for fakes:
  `run_local_for_browser_check.py`, `test_bulk_delete_locally.py`,
  `test_duplicate_color_check_locally.py`, `test_crop_queue_locally.py`,
  `test_personal_drive_connect_locally.py`, `test_oauth_token_refresh_locally.py`,
  `test_perspective_crop_locally.py`, `test_personal_library_locally.py`,
  `test_sync_delete_parity_locally.py`, `test_v25_clip_locally.py`. Every one needs repointing.
- `MediaIoBaseDownload` / `MediaIoBaseUpload` are patched on the app module by some of those
  tests, but they're Google library names *used by* sync / backup / crop code that hasn't moved
  yet. They should stay imported in whichever module actually uses them and be patched there —
  do **not** fold them into `drive.py` just because Drive tests touch them.
- `sync_folder_worker()` (still in `app.py` until Day 34) will now call
  `drive.get_drive_service()`, `drive.PERSONAL_LIBRARY_CAP`, etc. — qualified.

**Done when:** `drive.py` exists, every Drive call site in `app.py` is qualified, all ~10 test
scripts are repointed, full suite green.

**How it actually shipped (V71, August 27 2026):**
- `backend/drive.py` (239 lines) — the 11 functions + 3 constants above, every body
  character-for-character the original. Imports `get_db` from `core` and the Google client libs
  that left `app.py` (`Credentials`, `UserCredentials`, `Flow`, `Request`, `RefreshError`,
  `build`, `HttpError`, `MediaIoBaseDownload`).
- **11 test scripts repointed, not 10** — the estimate missed `test_admin_analytics_locally.py`,
  which reads `mod.PERSONAL_LIBRARY_CAP`. Transform was `mod.<name>` → `mod.drive.<name>` across
  the moved set; `mod.drive` is reachable because `app.py` does `import drive`, so no script
  needed a new import.
- **`MediaIoBaseDownload` ended up imported in BOTH files.** `download_drive_file()` moved (so
  `drive.py` imports it) but four other `app.py` functions still use it directly. The 3 scripts
  that fake the crop/reconcile download path (`test_crop_queue`, `test_perspective_crop`,
  `run_local_for_browser_check`) now also patch `mod.drive.MediaIoBaseDownload`. `MediaIoBaseUpload`
  did not move.
- `test_oauth_token_refresh_locally.py` was the one script patching a moved *Google class*
  (`mod.UserCredentials.refresh`) rather than a project function — repointed to
  `mod.drive.UserCredentials.refresh`.
- `run_db_backup()` + its folder helper + constants stayed in `app.py` for Day 33; their Drive
  calls are qualified now. `app.py`: **6,136 → 5,944 lines**.
- New `scripts/test_drive_locally.py` (32 checks — split wiring, `parse_drive_folder_id` string
  cases, `get_root_folder_id` per-user + fallback, `drive_error_reason` HttpError parsing,
  `get_service_account_email`). Full suite 39 Python + 3 `.mjs` green before and after; the
  browser-check harness boots clean and syncs its 10 fake images end-to-end through the qualified
  call paths.

---

## Day 30 — Gemini keys & usage → `gemini.py` *(V72 — COMPLETE)*

**Goal:** The friend-API-key encryption and spend-tracking code, self-contained.

- Moves: `_fernet()`, `encrypt_secret()`, `decrypt_secret()`, `set_user_gemini_key()`,
  `get_user_gemini_key()`, `record_gemini_usage()` (~110 lines)
- Depends on `core.py` + the `cryptography` package.

**Watch out:** `test_gemini_keys_locally.py` and `test_security_hardening_locally.py` exercise
these directly. `test_security_hardening_locally.py` is also the one V45 part 2 nearly broke —
handle its imports by hand, not just via the scripted transform.

**Done when:** `gemini.py` exists, both test scripts repointed, suite green.

**How it actually shipped (V72, August 27 2026):**
- `backend/gemini.py` (148 lines) — the 6 functions + `ENCRYPTED_PREFIX`, every body
  character-for-character the original (diffed against `HEAD` — 109 lines identical). Imports
  `get_db`/`get_model_pricing`/`GEMINI_MODEL` from `core` + `from datetime import datetime`;
  `Fernet` stays a lazy import *inside* `_fernet()`, so `app.py` has no top-level `cryptography`
  import to drop.
- **Qualify + repoint (rule 2), not re-export.** `app.py` does `import gemini`; 9 call sites
  qualified. `record_gemini_usage`'s 2 call sites (tagging worker, `/api/interpret`) stay in
  `app.py` until Day 32 but are qualified now anyway.
- **Both test scripts hand-edited, no scripted transform** — only `test_gemini_keys_locally.py`
  and `test_security_hardening_locally.py` reference the moved names (~6 + ~11 lines), and the
  latter is the V45-part-2 near-miss file. `mod.gemini.<name>` reaches them via `app.py`'s
  `import gemini`.
- Module name `gemini.py` doesn't collide with the Google SDK (`from google import genai as
  genai_client` → submodule `google.genai`, never top-level `gemini`).
- New `scripts/test_gemini_locally.py` (25 checks). Full suite **40 Python + 3 `.mjs` green
  before and after** (39→40 with the new file); `run_local_for_browser_check.py` boots clean and
  syncs 10 fake images. `app.py`: **5,944 → 5,828 lines** (−116).

---

## Day 31 — Image hydration & palette → `images_common.py` *(V73 — COMPLETE)*

**Goal:** The helpers that turn a raw `images` row into the rich object the frontend gets, plus
the boot-time backfills — shared today by search, decks, similar-images, utility views, sync, and
crop.

- Moves: `build_image_dict()`, `hydrate_image_rows()`, `_fetch_image_dict()`, `save_palette()`,
  `backfill_palettes()`, `backfill_phashes()`, `backfill_notes_fts()` (~300 lines)
- Depends on `core.py` + `colors.py` + `fingerprint.py` + `imaging.py` (all already modules).

**Watch out:**
- `build_image_dict()` has the `public=` flag that keeps public share links working (V43) —
  moving it must not touch that logic.
- Lots of callers. This is a "qualify many call sites" session more than a "move much code" one.
- If the Day 28 note held, the three `backfill_*` functions arrive here from `schema.py`'s
  territory — make sure `schema.py`/`init_db()` calls them via `images_common.` now.

**Done when:** every hydration/backfill call site qualified, suite green.

**How it actually shipped (V73, August 27 2026):**
- `backend/images_common.py` (370 lines) — the 7 planned functions **plus `merge_plural_tag_duplicates()`**
  (Ryan's call: it's a boot self-heal sitting right next to the other three backfills, only needs
  `get_db` + `normalize_tag_value`, so all four moved together). Every body diffed
  character-for-character against `HEAD` — all 8 byte-identical.
- **`favorite_col()` moved to `core.py`, not `images_common.py`.** `_fetch_image_dict()` builds its
  own favourite-aware SELECT with it, and `images_common` can't import `app.py`. It's a pure
  int-in/SQL-string-out helper used by ~6 SELECTs across the app — a clean fit for `core`'s
  "shared foundation" role. `app.py` re-imports it, so its other 5 call sites are byte-unchanged.
- **The Day 28 "watch out" was moot:** the three `backfill_*` were never called from inside
  `schema.py`/`init_db()` — they run at `app.py` module scope right after `init_db()`. They still
  do, now qualified `images_common.backfill_*()`.
- Rule 2 (qualify + repoint), same as Days 29–30. `import images_common`; ~15 call sites qualified.
  3 test scripts repointed (`test_v24_color`, `test_v33_color_fix`, `test_dp_notes_search`) — all
  plain `mod.<name>(` → `mod.images_common.<name>(` calls, no monkeypatches of these names exist
  anywhere. `scripts/diagnose_color_filter.py` (not in CI) got the same repoint; it has a
  pre-existing, unrelated DRIFT failure.
- New `scripts/test_images_common_locally.py` (41 checks). Full suite **41 Python + 3 `.mjs` green
  before and after** (40→41 with the new file); `run_local_for_browser_check.py` boots clean and
  syncs 10 fake images through the qualified `save_palette` path. `app.py`: **5,828 → 5,484 lines** (−344).

---

## Day 32 — Tagging worker → `tagging.py` *(planned)*

**Goal:** The Gemini auto-tag loop and its live-progress plumbing in one file.

- Moves: `_select_pending_for_tagging()`, `_run_tagging_job()` / `_run_tagging_job_inner()`,
  `trigger_tagging()`, `_broadcast_progress()`, the `_tag_progress` / `_sse_queues` state and
  their locks, and `GEMINI_TAGGING_PROMPT` (~460 lines including the prompt)
- Depends on `core.py` + `gemini.py` + the `google.genai` client.

**Watch out:**
- The `_tag_progress` dict is shared mutable state. The tagging routes staying in `app.py`
  (`tag_progress_stream`, `tag_progress_snapshot`, `tag_start`, `tag_mine`, `tag_progress_mine`,
  `retry_failed`) will read it as `tagging._tag_progress`. Confirm nothing keeps a *copy* of the
  dict reference at import time.
- ~8 test scripts set `trigger_tagging` to a no-op so tests don't call Gemini. All repointed to
  `tagging.trigger_tagging = ...`.
- The sync→tag handoff timing (V48) is delicate and documented in CLAUDE.md — the move must not
  change *when* `trigger_tagging()` resolves relative to the sync-complete flag.

**Done when:** `tagging.py` exists, routes read `tagging._tag_progress`, ~8 scripts repointed,
suite green — including `test_personal_library_locally.py` which checks the sync-then-tag chain.

---

## Day 33 — Monthly backup → `backup.py` *(planned)*

**Goal:** The once-a-month database-snapshot-to-Drive job, isolated.

- Moves: `run_db_backup()`, `_backup_due()`, `_backup_scheduler_loop()`,
  `start_backup_scheduler()`, `get_or_create_backups_folder()`, `BACKUP_FOLDER_NAME`,
  `KEEP_BACKUP_COUNT` (~115 lines)
- Depends on `core.py` + `drive.py`.

**Watch out:** no dedicated test exists for this today — verification is the full suite staying
green plus a manual read-through and, ideally, one real backup run confirmed in the Railway logs
after deploy. Consider writing a small `test_backup_locally.py` as part of this session so the
next person isn't flying blind.

**Done when:** `backup.py` exists, `app.py` calls `backup.start_backup_scheduler()` on boot,
suite green, one live backup confirmed.

---

## Day 34 — Crop worker → `crop.py` *(planned)*

**Goal:** The background crop-job queue and its worker thread — including the 190-line
`_process_crop_jobs()` that currently sits near the *top* of `app.py` for no reason.

- Moves: `_process_crop_jobs()`, the `_crop_queue` / `_crop_progress` state and lock,
  `_crop_job_counter`, `get_crop_progress()`, `reset_crop_progress()`, and the crop-apply logic
  lifted out of the `crop_image()` route (route stays as a wrapper) (~300 lines)
- Depends on `core.py` + `drive.py` + `perspective.py` + `imaging.py` + `images_common.py`.

**Watch out:**
- `test_crop_queue_locally.py` and `test_perspective_crop_locally.py` are the coverage here —
  both patch Drive fakes and `trigger_tagging`, and `test_perspective_crop_locally.py` patches
  `mod.ImageDraw` (a fixture helper, not app code). Repoint carefully.
- The destructive-write tail (back up original to `_Removed` *first*, then overwrite) must move
  as one piece — CLAUDE.md flags that this exact path is what broke in V27.

**Done when:** `crop.py` exists, both crop test scripts repointed, suite green, and one real crop
run confirmed on the live site (the standing "Day 27 crop" item — a real crop that explains
itself in the toast if it fails).

---

## Day 35 — Drive sync → `sync.py` *(planned)*

**Goal:** The folder-sync worker and everything it calls — the last big worker domain in
`app.py`.

- Moves: `sync_folder_worker()`, `_ingest_image()`, `_load_existing_phashes()`,
  `reconcile_drive_changes()`, `_users_with_synced_folders()`, the `sync_state` dict,
  `merge_plural_tag_duplicates()` (~360 lines)
- Depends on `core.py` + `drive.py` + `tagging.py` + `images_common.py` + `fingerprint.py` +
  `colors.py` + `imaging.py`. (It comes last precisely because it depends on nearly everything
  else that moved.)

**Watch out:**
- `test_personal_library_locally.py`, `test_sync_delete_parity_locally.py`,
  `test_v25_clip_locally.py`, `test_duplicate_color_check_locally.py` all call
  `mod.sync_folder_worker(...)` and/or `_ingest_image` directly — repoint to `sync.`.
- The half-the-library-vanished guard (V30) and the sync-delete cascade table list must move
  verbatim.
- `reconcile_drive_changes()` runs at boot on a background thread — confirm `app.py` still
  starts that thread, now pointing at `sync.reconcile_drive_changes`.

**Done when:** `sync.py` exists, 4 test scripts repointed, suite green, and one real sync run
confirmed on the live site.

---

## Days 36–42 — Route Blueprints (backend) *(planned, granular)*

With the workers out (Day 35), `app.py` should be roughly **routes + startup wiring**, likely in
the 3,500–4,000-line range (down from ~6,960). Now the routes themselves come out, grouped by
domain into Flask "blueprints" (a blueprint = a bundle of related routes registered onto the app
as a unit). This is a distinct risk class from the worker extractions — blueprint registration,
`url_for` endpoint names, and decorator availability all change — so each blueprint gets its own
session.

**The pattern every blueprint session follows:**
1. Create `routes_<domain>.py` with a `Blueprint` object.
2. Move the domain's `@app.route(...)` functions into it, changing `@app.route` →
   `@bp.route` and keeping the URL paths byte-identical.
3. Register the blueprint in `app.py` with `app.register_blueprint(bp)`.
4. `admin_required` / `require_login` decorators move to `core.py` (or an `auth_helpers.py`) so
   blueprints can import them without touching `app.py`.
5. Full suite green before and after; every route's URL unchanged so the frontend needs zero
   changes.

### Day 36 — `routes_auth.py`
Login, logout, register, setup, forgot/reset password, invite codes, `/api/auth/me`. ~250 lines.
The `require_login` / `admin_required` / `_adopt_session_from_header` helpers move to `core.py`
here. Highest care: the `@app.before_request` login gate must keep working across all blueprints.

### Day 37 — `routes_search.py`
`/api/search`, `/api/search/ids`, `/api/autocomplete`, `/api/interpret`, `/api/bookmarks`,
`build_search_filters()`, `_fts5_match_query()`, `get_similar_images()`, `_cosine_similarity()`.
~500 lines. Self-contained — search reads the DB, writes nothing except bookmarks.

### Day 38 — `routes_tags.py`
`/api/tags/*` (bulk apply/remove/preview/summary/suggestions), `/api/tag-categories`,
`edit_tags`, `count_tags_for_images`, `_parse_bulk_tag_request`. ~350 lines. Pairs naturally with
the `tagging.py` worker from Day 32 but stays a separate cut.

### Day 39 — `routes_images.py`
Favorite toggle, filmography edit, on-set notes, download, delete, bulk delete, thumbnail serve,
full-res proxy, `regenerate_thumbnails`, `extract_colors`. ~600 lines. Touches Drive (delete →
`_Removed`) so depends on `drive.py` being done.

### Day 40 — `routes_decks.py`
The whole decks/scenes/storyboard/share/feedback block — `list_decks` through
`get_shared_deck` and the V42 client-feedback endpoints. ~900 lines, the single biggest route
group. Self-contained domain (its own tables, its own `_deck_payload` / `_deck_access` helpers).
The PDF export endpoint (`export_deck_pdf`) comes here too — the `pdf_export.py` module it calls
is already split.

### Day 41 — `routes_sync.py`
`/api/sync/*`, `/api/sync-settings`, `/api/account/*` (folder connect, setup status, Gemini key),
`/api/backups/*`, `/api/folders`, `/api/models`, `/api/config`. ~400 lines. Thin wrappers over
the `sync.py` / `drive.py` / `backup.py` workers already extracted.

### Day 42 — `routes_analytics.py` + final cleanup
`/api/analytics`, `/api/analytics/users`, `/api/views/*`, `/api/views/log`,
`get_utility_view()`, `log_image_views()`. ~200 lines. Plus: whatever's left in `app.py` should
now be just the Flask app object, config, blueprint registration, `before_request` gate, the
`serve()` catch-all for the React shell, and the `__main__` startup block — target **under 400
lines**. Update CLAUDE.md's file-structure section to reflect the final module layout.

---

## Days 43+ — `Home.jsx` breakup (frontend) *(planned, separate track)*

`frontend/src/pages/Home.jsx` is **1,855 lines with 36 separate pieces of state**. The V35
stale-selection bug and the Day 20 crop-selection bug both lived here. Different language,
different risks — there is no `.jsx` test suite the way there's a `test_*_locally.py` suite for
the backend, so verification leans harder on live browser checks.

**Rough cut plan (to be scoped properly in its own planning session before starting):**
- **Day 43** — Extract search/filter state into a `useSearch()` custom hook (chips, NL chips,
  note chips, colour, aspect ratio, film filter, the `buildFilterParams()` assembler). ~400
  lines of state logic out.
- **Day 44** — Extract Select Mode / Tag Mode into a `useSelection()` hook (the selection Set,
  drag-select, shift-click range, the bulk-action handlers). ~350 lines. This is where the two
  historical bugs lived.
- **Day 45** — Extract the masonry grid + infinite scroll + view-logging into a `<ImageGrid>`
  component. ~300 lines.
- **Day 46** — Whatever's left: the page becomes composition — `<Home>` wires the hooks and
  components together and owns very little state directly. Target **under 500 lines**.

---

## After Day 46 — stop, or reassess

A ~400-line `app.py` split into ~15 focused modules, and a ~500-line `Home.jsx` composed from
hooks and components, is a genuinely different codebase to work in. At that point the refactor
phase is done. Anything further (splitting `ImageDetail.jsx`, `DeckDetail.jsx`, further backend
helper consolidation) is case-by-case, driven by actual friction, not a plan.

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
| 20 | Deck ordering + crop selection fix | Scenes reorder; selection clears after crop ✅ *(V38)* |
| 21 | DP technical notes + full-text search | Camera/lens/stop/filter fields + Omnisearch-style find ✅ *(V39)* |
| 22 | PDF lookbook export | A file you can send an agency *(V40)* |
| 23 | Presentation mode | Present a pitch from the app *(V41)* |
| 24 | Client feedback loop | Picks + comments on share links *(V42)* |
| 25 | Performance: caching + indexes + CI | 6.5 MB/page → cached; tests self-run ✅ *(V43)* |
| 26 | Security + reliability hardening | Login throttling, key encryption ✅ *(V44)* |
| 27 | Structural refactor | Pure maths out of app.py: 7,337 → 6,626 lines; test harness unblocked ✅ *(V45)* |
| **— PHASE 3: THE REFACTOR —** | *split app.py one module/session, tests green each side* | |
| 28 | Foundation: `core.py` + `schema.py` + security fixes | Shared DB/constants + boot code out (app.py −824 lines); cookie flags, hand-rolled rate limiting ✅ *(V70)* |
| 29 | Google Drive layer → `drive.py` | Drive connection/auth/listing in one file; 11 test scripts repointed (app.py −192 lines) ✅ *(V71)* |
| 30 | Gemini keys & usage → `gemini.py` | Key encryption + spend tracking isolated; 2 test scripts hand-repointed (app.py −116 lines) ✅ *(V72)* |
| 31 | Image hydration & palette → `images_common.py` | `build_image_dict`/hydrate/`_fetch` + 4 backfills out; `favorite_col`→`core.py`; 3 test scripts repointed (app.py −344 lines) ✅ *(V73)* |
| 32 | Tagging worker → `tagging.py` | Gemini auto-tag loop + progress plumbing *(planned)* |
| 33 | Monthly backup → `backup.py` | Snapshot-to-Drive job isolated *(planned)* |
| 34 | Crop worker → `crop.py` | Background crop queue + worker thread *(planned)* |
| 35 | Drive sync → `sync.py` | Folder-sync worker + ingest, last big worker domain *(planned)* |
| 36 | Routes → `routes_auth.py` | Login/register/invite routes as a blueprint *(planned)* |
| 37 | Routes → `routes_search.py` | Search/autocomplete/bookmarks/similar as a blueprint *(planned)* |
| 38 | Routes → `routes_tags.py` | Bulk tag ops + tag editing as a blueprint *(planned)* |
| 39 | Routes → `routes_images.py` | Favorite/filmography/notes/download/delete/thumb *(planned)* |
| 40 | Routes → `routes_decks.py` | Decks/scenes/storyboard/share/feedback/PDF, biggest group *(planned)* |
| 41 | Routes → `routes_sync.py` | Sync/account/backups/config route wrappers *(planned)* |
| 42 | Routes → `routes_analytics.py` + cleanup | Analytics/views + app.py down to <400 lines *(planned)* |
| 43 | `Home.jsx` → `useSearch()` hook | Search/filter state extracted from the 1,855-line page *(planned)* |
| 44 | `Home.jsx` → `useSelection()` hook | Select/Tag Mode logic out (where 2 historical bugs lived) *(planned)* |
| 45 | `Home.jsx` → `<ImageGrid>` component | Masonry + infinite scroll + view-logging out *(planned)* |
| 46 | `Home.jsx` final composition | Page becomes wiring; target <500 lines *(planned)* |

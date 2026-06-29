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

## Day 16 — Fly.io Migration *(infrastructure, no new features)*

**Goal:** Cut Railway, move to Fly.io free tier. Save ~$60/year.

- Install Fly.io CLI, create new app
- Migrate persistent volume (SQLite + thumbnails)
- Transfer environment variables (Drive credentials, Gemini key)
- Update any Railway-specific config
- Confirm sync worker, cron job, and all endpoints working on Fly.io
- Decommission Railway project

**Done when:** Frame Atlas runs identically on Fly.io. Railway cancelled.

---

## Day 17 — Personal Libraries per User *(Optional upgrade)*

**Goal:** Each user connects their own image source and has an isolated library.

- Google OAuth login (users sign in with Google account)
- Each user sets their own image folder
- Per-user tagging pass with Gemini
- Fully isolated images, tags, decks per user

**Done when:** A friend can connect their own folder and have a completely separate library.

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
| 16 | Fly.io migration | Cut Railway, save $60/year |
| 17 | Personal libraries | Per-user folders *(optional)* |
| 18 | NAS migration | $0/month forever *(when ready)* |
| 19 | Browser extension | Web clipping *(future)* |

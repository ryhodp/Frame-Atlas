# Frame Atlas — Product Requirements Document
*v6 — Updated after infrastructure planning session*

---

## Overview

Frame Atlas is a self-hosted personal web app that turns an image folder (Google Drive now, NAS later) into a fully searchable, AI-tagged visual reference library. Features an Obsidian Bases-style incremental tag filter with NL fallback, dense thumbnail grid, dual-mode similar image search (CLIP visual + tag overlap), color picker, unlimited tag chips, bookmarked searches, bulk tag editing with smart co-occurrence suggestions, per-project lookbook decks with named scenes and storyboard ordering, Obsidian markdown export, favorites, flagging, analytics dashboard, and multi-user support.

**Primary user:** Ryan Ho, DP/Gaffer. Personal use with optional friend accounts and read-only lookbook sharing.

---

## Infrastructure & Hosting Strategy

### Current Stack (Building Phase)
- **Compute:** Railway ($5/month) — finish building the app here
- **Image source:** Google Drive ($3/month for 100GB) — Inspiration Images folder
- **Database + thumbnails:** Railway persistent volume (~$0.50/month)

### Migration Path (When Ready)
**Phase A — Cut Railway, keep Drive**
Migrate compute to **Fly.io** (free tier). Same Docker deploy, no cold starts, 3GB persistent volume — enough for ~30,000+ images worth of thumbnails + SQLite. Saves ~$60/year. One-time migration effort, no code changes.

**Phase B — Cut Drive, move to NAS**
When a Ugreen NAS (or equivalent) is purchased:
- Move Inspiration Images folder from Google Drive to a local NAS folder
- iPhone syncs to NAS via the NAS's own mobile app instead of the Drive app
- Swap one config variable pointing the sync worker from Google Drive API → local filesystem watch
- Run the Frame Atlas Docker container on the NAS itself
- Access via Tailscale from any device in the world
- Recurring cost: **$0/month**

**The only migration work at Phase B:**
- Replace Google Drive API polling with local folder watching (~1 day of code)
- One-time script to remap `drive_file_id` identifiers → local filenames in SQLite (~20 lines Python)
- All tags, decks, bookmarks, and metadata survive intact — they live in SQLite, not Drive

### Cost Summary

| Phase | Monthly Cost |
|---|---|
| Now (Railway + Drive) | ~$8.50/month |
| After Fly.io migration | ~$3/month |
| After NAS migration | $0/month |

---

## Thumbnail Quality

- **Resolution:** 600px wide JPEG
- **Quality:** Pillow default (75)
- **Detail view:** Loads original full-res image proxied through Flask from Google Drive (credentials never leave server). Thumbnail used for grid only.
- **Existing images:** Thumbnails regenerated on next sync after resolution change

---

## Multi-User Architecture

### Design Principle: user_id from Day 1

Every database table includes a `user_id` column from the very start — even when only one user exists. Makes the upgrade from shared to personal libraries additive rather than a restructure.

### Phase 1 — Shared Library (MVP, Day 12)

One deployment, one image folder, one shared image pool. Each user gets their own login, their own decks, lookbooks, favorites, flags, and bookmarked searches. Everyone searches the same library. Owner holds the Gemini API key.

### Phase 2 — Personal Libraries (Optional Upgrade, Day 14)

Each user connects their own image source. Fully isolated library, tags, and decks per user. All queries filter by `user_id`.

---

## Tag Taxonomy

All tags AI-generated at ingest via Gemini Flash 2.0. All tags are editable, removable, and extensible. Custom tags created on the fly in Tag Mode.

### Mood / Feeling
`lonely` `intimate` `tense` `ominous` `serene` `chaotic` `melancholic` `warm` `euphoric` `epic` `mundane` `dreamlike` `claustrophobic` `vast`

### Lighting Quality
`hard` `soft` `motivated` `unmotivated` `single-source` `practical-heavy` `high-key` `low-key` `no-fill` `bounce-heavy` `silhouette` `chiaroscuro`

### Lighting Color Temperature
`warm-tungsten` `cool-daylight` `mixed-sources` `green-practical` `neon` `firelight` `moonlight`

### Color Palette
`desaturated` `high-contrast` `monochromatic` `warm-palette` `cool-palette` `earthy` `high-saturation` `bleach-bypass` `golden` `teal-orange`

### Shot Type
`extreme-wide` `wide` `medium-wide` `medium` `close-up` `extreme-close-up` `aerial` `POV` `over-shoulder` `two-shot`

### Framing / Composition
`centered` `rule-of-thirds` `dutch-angle` `low-angle` `high-angle` `eye-level` `negative-space` `symmetrical` `foreground-frame`

### Location Type
`interior` `exterior` `diner` `hospital` `warehouse` `rooftop` `forest` `urban-street` `office` `home` `car` `bar` `stage` `industrial` `desert` `water`

### Time of Day / Weather
`golden-hour` `magic-hour` `midday` `blue-hour` `night` `overcast` `dawn` `rain` `fog` `snow` `harsh-sun`

### Source Type
`film-still` `BTS` `production-still` `mood-texture` `abstract`

### Subject Count
`no-subject` `solo` `pair` `group` `crowd`

### Subject — Camera Relationship
`looking-at-camera` `looking-away` `profile` `back-to-camera`

### Performance / Actor Emotion
`joy` `grief` `fear` `rage` `longing` `neutral` `shock` `tenderness` `defiance`

### Genre / Aesthetic
`horror` `western` `sci-fi` `romance` `documentary` `thriller` `noir` `drama` `comedy` `action`

### Era / Decade
`period-piece` `70s` `80s` `90s` `contemporary` `futuristic`

### Camera / Format
`35mm-film` `16mm-film` `anamorphic` `spherical` `digital` `arri` `red` `sony` `blackmagic`
*(AI-detected for film stills; manually added for production stills)*

### Aspect Ratio
`2.39:1` `1.85:1` `16:9` `4:3` `square`
*(Auto-detected from image dimensions at ingest — free, no AI)*

### Custom Tags
User-defined, created on the fly in Tag Mode. No restrictions on naming.

---

## Feature Requirements

### Search — Incremental Tag Filter with NL Fallback

- User types → autocomplete shows matching tags in real time
- Selecting a tag locks it as an AND filter chip → results narrow instantly
- Next typed characters → autocomplete reflects only tags present in remaining results
- If typed text has no matching tags → input auto-switches to NL mode → Gemini interprets phrase → filter applied
- Tag chips and NL phrases coexist freely in same search bar
- Unlimited chips — no cap
- **Exact tag chip searches never invoke AI.** Pure SQL. Free, instant.
- **Bookmarked Searches:** Save any chip + NL combination as a named preset. One-click recall from sidebar.

---

### Image Detail Panel

Clicking any image opens a panel with:
- Full-size image (proxied from Drive through Flask — credentials stay server-side)
- AI-generated one-sentence caption
- All tags organized by category — click any to edit or remove inline
- Filmography metadata: film title, director, DP, year
- Camera / lens / format
- Aspect ratio
- "Find Similar" button
- Add to deck / scene picker
- Favorite (star) + Flag buttons
- Source info: filename, folder location, date added

---

### Similar Images — Dual Mode

**Tag Overlap:** Scores every image by percentage of shared tags. Images with ≥90% tag overlap surfaced as top results.

**CLIP Visual Similarity:** `clip-vit-base-patch32` via HuggingFace. 512-dimension embeddings generated once via local Mac script, stored in SQLite as binary vectors. Railway/Fly.io only runs cosine similarity arithmetic at query time — no model inference on the server.

**Final ranking** = weighted combination of both scores.

---

### Color Picker Search

- Pillow extracts 5 dominant hex colors per image at ingest (free)
- Color swatch picker in UI → filter results by dominant palette
- Fully combinable with tag chips

---

### Tag Mode — Bulk Editing with Smart Suggestions

- Toggle Tag Mode from anywhere
- Multi-select: individual clicks, box-select drag, select all in current results
- Bulk apply: type or choose tag → applied to all selected instantly
- Bulk remove: shared tags across selection displayed → click X to remove from all
- Custom tag creation on the fly
- **Smart Co-occurrence Suggestions (pure SQL, free):** surfaces patterns like "You tagged 34 images `seamless-paper`. 31 similar images also have `BTS`. Add to all 34?"

---

### Image Source — Sync Pipeline

**Current: Google Drive**
- Sync worker watches one designated Drive folder (subfolders included)
- Detects new images by Drive file ID
- Runs nightly + available on-demand via "Sync Now" button
- iPhone uploads via Drive app, picked up automatically

**Future: NAS Local Folder**
- Sync worker watches local filesystem folder instead of Drive API
- iPhone uploads via NAS mobile app
- Detection switches from Drive file ID → local filename + modified date
- One-time migration script remaps existing image identifiers in SQLite

**Ingest pipeline per new image (same regardless of source):**
1. Generate thumbnail at 600px wide, Pillow quality 75
2. Detect aspect ratio from image dimensions (free)
3. Send to Gemini Flash 2.0 → structured JSON: tags + caption + filmography + camera/format
4. Extract 5 dominant hex colors via Pillow
5. Queue for CLIP embedding (generated in local batch script on Mac)
6. Write everything to SQLite with `user_id`

---

### Decks + Scenes + Storyboard

**Deck** = named project-level collection (e.g. "30 FAD Lookbook")
**Scene** = named group within a deck (e.g. "Night Exteriors", "Golden Hour Drive")
**Storyboard mode** = drag images into sequence within a scene, add per-image text notes
**Read-only share link** per deck (token-based, no login required for viewer)

---

### Obsidian Markdown Export

Any deck exports as a `.md` file. Images embedded as URL links pointing to the app's thumbnail server — zero image files added to vault. Images render inline in Obsidian reading view.

---

### Favorites + Flagging

**Favorites:** Star any image. Accessible via persistent "Favorites" view.
**Flagging:** Flag images needing attention. "Flagged" queue shows all flagged images; clear flags once resolved.

---

### Recently Added View

Strip on home view showing images ingested since last sync.

---

### Analytics Dashboard

- Tag frequency heatmap
- Source type breakdown (film-still vs BTS vs mood-texture etc.)
- Mood distribution
- Location type spread
- Time of day distribution
- Most-used bookmarked searches
- Library growth over time

---

### Browser Extension *(Post-MVP — Day 15)*

Chrome extension (Manifest V3). Right-click any image on any webpage → "Add to Reference Library" → image saved to sync folder → auto-tagged by Gemini on next sync.

---

## Cost Breakdown

### One-Time Ingest (~3,000 images)

| Item | Cost |
|---|---|
| Gemini Flash 2.0: tags + caption + filmography | ~$0.30–1.00 |
| CLIP embeddings (local Python script, Mac) | Free |
| Color extraction via Pillow | Free |
| Aspect ratio detection | Free |
| **Total one-time** | **Under $1** |

### Ongoing Monthly (Fly.io phase)

| Item | Cost |
|---|---|
| Fly.io hosting (free tier) | $0 |
| Google Drive 100GB | $3.00 |
| Exact tag searches (SQL only) | $0 |
| NL phrase search (Gemini) | ~$0.001/search |
| New image tagging (Gemini) | ~$0.0001/image |
| **Total ongoing** | **~$3/month** |

---

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python + Flask | Simple, great image/AI library support |
| Database | SQLite | Zero-config, scales to 50k+ images |
| AI — tagging, captions, NL | Gemini Flash 2.0 | ~10x cheaper than Claude Haiku; excellent vision |
| Visual similarity | CLIP `clip-vit-base-patch32` | Embeddings pre-computed locally, vectors in SQLite |
| Color extraction | Python Pillow | Free, runs at ingest |
| Thumbnail quality | 600px wide, quality 75 | Grid display; detail view loads original from Drive |
| Full-res detail view | Flask proxy from Drive | Credentials stay server-side |
| Aspect ratio | Math from image dimensions | Free, instant |
| Co-occurrence suggestions | SQL aggregation | Free, no API |
| Frontend | React + Tailwind | Real-time chips, dense grid, drag-drop scenes |
| Hosting (now) | Railway | Finish building here |
| Hosting (soon) | Fly.io free tier | ~$60/year savings, same Docker deploy |
| Hosting (future) | NAS via Docker + Tailscale | $0/month forever, accessible anywhere |
| Image source (now) | Google Drive | Inspiration Images folder |
| Image source (future) | NAS local folder | iPhone syncs via NAS app |
| Drive sync | Google Drive API v3 (service account) | Swapped for filesystem watch at NAS migration |
| Auth (Phase 1) | Username/password | Simple, adequate for shared library |
| Auth (Phase 2) | Google OAuth | Required for personal library model |
| Browser extension | Chrome Manifest V3 | Post-MVP |

---

## Database Schema (Key Tables)

```sql
-- user_id included from Day 1 even in single-user mode

images (
  id, user_id, source_file_id, filename,
  thumbnail_path, caption, aspect_ratio,
  date_added, is_favorite, is_flagged
)
-- source_file_id = Drive file ID now, local filename later

tags (id, image_id, user_id, category, value)
colors (id, image_id, user_id, hex, rank)
embeddings (id, image_id, user_id, clip_vector BLOB)
filmography (id, image_id, title, director, dp, year)
saved_searches (id, user_id, name, chips_json, nl_phrase)
decks (id, user_id, name, share_token)
scenes (id, deck_id, name, sort_order)
deck_images (id, deck_id, scene_id, image_id, storyboard_order, storyboard_note)
users (id, username, password_hash, role, image_source_folder, gemini_api_key)
```

---

## Architecture Summary

```
INGEST (NOW)                          INGEST (FUTURE)
────────────                          ───────────────
Google Drive Folder                   NAS Local Folder
  + iPhone → Drive app                  + iPhone → NAS app
        │                                     │
        ▼                                     ▼
Sync Worker (Railway → Fly.io → NAS Docker)
  ├── Detect new images
  ├── Generate 600px thumbnail (Pillow quality 75)
  ├── Detect aspect ratio
  ├── Gemini Flash 2.0 → tags + caption + filmography
  └── Pillow → 5 dominant hex colors
        │
        ▼
One-Time Local Script (Mac)
  └── CLIP embeddings → stored in SQLite

QUERY
─────
React Frontend
  → Flask API
      ├── Tag chip search          → SQL (free)
      ├── NL phrase search         → Gemini → SQL (~$0.001)
      ├── Color search             → SQL (free)
      ├── Similar images           → cosine similarity + tag overlap (free)
      ├── Co-occurrence hints      → SQL aggregation (free)
      ├── Bookmarked searches      → SQL (free)
      ├── Decks / scenes           → SQL (free)
      ├── Analytics                → SQL aggregation (free)
      ├── Obsidian export          → markdown generation (free)
      ├── Share link               → token lookup (free)
      └── Full-res detail image    → Flask proxy from Drive (free)
```

---

## What's Not In Scope

- Video or motion reference
- Streaming / watch-the-film integration
- AI features that run at query time without explicit user action (all AI is ingest-time or NL-triggered)

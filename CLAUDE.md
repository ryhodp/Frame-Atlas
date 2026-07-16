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

**API Endpoints (complete)**
- `/api/images` — all images
- `/api/search` — AND-filter tag search; optional `seed` param (V14) switches the unfiltered grid to a deterministic shuffled order — images the user viewed in the last 7 days sort below unseen ones; any active filter ignores the seed and stays newest-first. Optional `ar` param (V15) filters by aspect-ratio bucket (e.g. `ar=2.39:1`) — every image snaps to its nearest standard format via `normalize_ar_label()`, same math as the tile labels
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

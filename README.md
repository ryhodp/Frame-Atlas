# Frame Atlas — Reference Library for Cinematographers

A self-hosted personal web app that turns Google Drive folders of reference images into a fully searchable, AI-tagged visual library.

**Status:** Day 1 — Skeleton Deploy ✅

---

## Quick Start

### Prerequisites

- Python 3.9+
- Node.js 16+
- GitHub account
- Railway account (free tier works)
- Google Cloud account (for Drive API)
- Google AI account (for Gemini API key)

### Local Development

```bash
# Clone repo
git clone <repo-url>
cd frame-atlas

# Backend
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py

# Frontend (new terminal)
cd frontend
npm install
npm run dev
```

App runs on `http://localhost:5173` (frontend) → proxies `/api` to `http://localhost:5000` (backend).

### Railway Deployment

1. **Connect GitHub repo to Railway** (via dashboard)
2. **Add environment variables** in Railway project settings:
   - `GEMINI_API_KEY` — from Google AI Studio
   - `GOOGLE_DRIVE_CREDENTIALS` — service account JSON (as string)
   - `DATABASE_PATH` — defaults to `/app/frame_atlas.db`
3. **Deploy** — Railway runs `start.sh` automatically

App runs at your Railway project URL (e.g., `https://frame-atlas-xxxx.railway.app`).

---

## Project Structure

```
frame-atlas/
├── backend/
│   ├── app.py              # Flask backend
│   ├── requirements.txt     # Python dependencies
│   └── static/            # Built React frontend (generated on deploy)
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── App.jsx
│   │   ├── main.jsx
│   │   └── index.css
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   └── postcss.config.js
├── railway.toml            # Railway deployment config
├── start.sh               # Startup script for Railway
├── .env.example           # Environment template
└── .gitignore
```

---

## Database Schema

All tables include `user_id` from Day 1 (ready for multi-user upgrade in Day 12).

**Core Tables:**
- `users` — user accounts
- `images` — image metadata (Drive file ID, aspect ratio, caption, etc.)
- `tags` — image tags (category + value, e.g., "mood: ominous")
- `colors` — dominant hex colors per image
- `embeddings` — CLIP vectors for visual similarity (added Day 7)
- `filmography` — film metadata (title, director, DP, year)
- `decks`, `scenes`, `deck_images` — project lookbooks (added Day 9)
- `saved_searches` — bookmarked filter combinations
- `sync_state` — Drive sync tracking

---

## API Endpoints (Day 1)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/health` | GET | Backend health check |
| `/api/config` | GET | Config status (DB, Gemini, Drive ready?) |

More endpoints added each day.

---

## Build Timeline

| Day | Focus | Status |
|---|---|---|
| 0 | Account setup | 🔄 You did this |
| 1 | Skeleton deploy | ✅ This sprint |
| 2 | Drive sync | Coming next |
| 3 | AI tagging | Coming next |
| 4–6 | Core search features | Coming next |
| 7 | CLIP + similar images | Coming next |
| 8 | Tag mode + suggestions | Coming next |
| 9 | Decks + scenes | Coming next |
| 10 | Storyboard + Obsidian export | Coming next |
| 11 | Analytics | Coming next |
| 12 | Multi-user auth | Coming next |
| 13 | Polish + mobile | Coming next |
| 14 | Personal libraries (optional) | Coming next |
| 15 | Browser extension (optional) | Coming next |

---

## Troubleshooting

### Backend won't start locally
```bash
# Check Python version
python --version  # Should be 3.9+

# Reinstall dependencies
pip install -r requirements.txt
```

### Frontend won't build
```bash
# Clear cache
rm -rf node_modules package-lock.json
npm install
npm run build
```

### Railway deploy fails
- Check build logs in Railway dashboard
- Verify environment variables are set
- Ensure `start.sh` has execute permissions (`chmod +x start.sh`)

---

## Next Steps (Day 2)

1. Verify URL loads on phone, laptop, and another device
2. Confirm backend `/api/health` returns `{"status": "ok"}`
3. Set up Google Drive folder + service account credentials
4. Begin Google Drive sync worker (Day 2 focus)

---

## License

Personal project. Frame Atlas © 2024.

# Frame Atlas — Version History

## Frame Atlas V1 — Skeleton Deploy
*Day 1 — Initial deployment structure*

### What's Included
- Flask backend with SQLite database schema (all tables from PRD)
- React + Vite + Tailwind frontend
- Health check endpoints
- Static file serving for SPA routing
- Railway deployment configuration
- Environment variable template

### Key Features
- Live URL on any device
- Database initialized with `user_id` from Day 1 (ready for multi-user upgrade)
- Simple header + empty image grid placeholder
- Backend health check integration

### Files Created
- Backend: `app.py`, `requirements.txt`
- Frontend: `package.json`, `vite.config.js`, `tailwind.config.js`, React components
- Deploy: `railway.toml`, `start.sh`, `Procfile`
- Docs: `README.md`, `.env.example`

### Next Version (V2)
Day 2 will add Google Drive sync worker — images will populate from your Drive folder.

---

*Track all major versions and feature additions here for quick reference.*

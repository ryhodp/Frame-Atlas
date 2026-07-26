# Frame Atlas Clipper (V25)

Right-click any image on the web — or a video, to grab the frame showing right
now — and save it straight into your Frame Atlas library.

The image goes into your Google Drive folder, gets a thumbnail and a colour
palette, and joins the AI tagging queue. Exactly what the upload button in the
app does, just without leaving the page you're on.

---

## Installing it (one time, about a minute)

Chrome doesn't allow installing an extension from a folder by double-clicking —
it has to be loaded manually. This is normal for a private extension that isn't
published to the Chrome Web Store.

1. Open Chrome and go to `chrome://extensions`
2. Turn on **Developer mode** (toggle, top right)
3. Click **Load unpacked** (button, top left)
4. Select this `extension` folder inside `frame-atlas`
5. The gold Frame Atlas "F" appears in your toolbar

It stays installed. You only do this once.

> Loading it this way means Chrome shows a "Disable developer mode extensions"
> warning bubble on startup. Harmless — just close it.

---

## Using it

**An image:** right-click it → **Add image to Frame Atlas**

**A video frame:** pause where you want it, right-click the video →
**Add current video frame to Frame Atlas**

A small gold confirmation appears in the top-right corner. If the image is
already in your library it says so instead of adding a second copy.

---

## Signing in

There's nothing to set up. The extension uses the Frame Atlas login already
open in your browser.

If clipping fails with a sign-in message, open Frame Atlas in a tab, log in,
and try the clip again.

---

## Pointing it somewhere else

By default it saves to the live site
(`https://frame-atlas-production.up.railway.app`). To change that — a local
copy, say — click the Frame Atlas icon in the toolbar, type the address, and
hit Save.

---

## When it can't clip something

| What you see | What's happening |
|---|---|
| "Sign in to Frame Atlas first" | Your login expired. Open Frame Atlas, log in, retry. |
| "Already in your library" | Not an error — the same photo is already saved. |
| "This site blocks copying its video frames" | A browser security rule on that site, not something the extension can get around. Take a screenshot and upload it instead. |
| "That video hasn't loaded a frame yet" | Press play (or scrub to a frame) before right-clicking. |
| "Couldn't download that image" | The site refused the download — usually hotlink protection. |

Clipping is admin-only, same as the upload button, because both write into
Ryan's Drive folder.

---

## Files

| File | What it does |
|---|---|
| `manifest.json` | Permissions and wiring Chrome reads on install |
| `background.js` | Context menus, image/frame capture, the upload call, the toast |
| `options.html` / `options.js` | The little popup for setting the address |
| `icon*.png` | Toolbar icons |

Backend endpoint: `POST /api/clip` in `backend/app.py`.
Tests: `scripts/test_v25_clip_locally.py`.

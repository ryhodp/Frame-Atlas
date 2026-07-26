/* Frame Atlas Clipper — service worker.
 *
 * Right-click an image (or a video, to grab the frame showing right now) and
 * it goes to POST /api/clip, which drops it in the Drive folder, thumbnails
 * it, pulls its palette and queues it for AI tagging — the same path the
 * in-app uploader takes.
 *
 * Auth piggybacks the Frame Atlas login already in this browser. See
 * readSessionCookie() for why the cookie is copied into a header by hand.
 */

const DEFAULT_BASE_URL = 'https://frame-atlas-production.up.railway.app';

const getBaseUrl = async () => {
  const { baseUrl } = await chrome.storage.sync.get('baseUrl');
  return (baseUrl || DEFAULT_BASE_URL).replace(/\/+$/, '');
};

// ── context menus ──────────────────────────────────────────────────────────
const MENUS = [
  { id: 'fa-clip-image', title: 'Add image to Frame Atlas', contexts: ['image'] },
  { id: 'fa-clip-frame', title: 'Add current video frame to Frame Atlas', contexts: ['video'] },
];

const installMenus = () => {
  chrome.contextMenus.removeAll(() => {
    for (const m of MENUS) chrome.contextMenus.create(m);
  });
};

chrome.runtime.onInstalled.addListener(installMenus);
chrome.runtime.onStartup.addListener(installMenus);

// ── auth ───────────────────────────────────────────────────────────────────
/* The Flask session cookie is SameSite-restricted, so Chrome will not attach
 * it to a request originating from this extension. Reading it here and
 * sending it as X-FA-Session gets the same session across explicitly — the
 * backend verifies the signature before trusting it. */
const readSessionCookie = async (baseUrl) => {
  const candidates = ['session', 'frame_atlas_session'];
  for (const name of candidates) {
    const cookie = await chrome.cookies.get({ url: baseUrl, name });
    if (cookie?.value) return cookie.value;
  }
  return null;
};

// ── capture ────────────────────────────────────────────────────────────────
/* Fetched here in the service worker rather than in the page: <all_urls> host
 * permission means these requests skip CORS, so images from sites that block
 * cross-origin reads still come through. */
const fetchImageAsDataUrl = async (srcUrl) => {
  if (srcUrl.startsWith('data:')) return srcUrl;
  const res = await fetch(srcUrl);
  if (!res.ok) throw new Error(`Couldn't download that image (HTTP ${res.status}).`);
  const blob = await res.blob();
  if (!blob.type.startsWith('image/')) throw new Error("That link isn't an image.");
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Couldn't read that image."));
    reader.readAsDataURL(blob);
  });
};

/* Runs inside the page. Paints the video's current frame to a canvas — the
 * only way to get at it, since the frame isn't a file anywhere. Cross-origin
 * video without CORS headers taints the canvas and makes toDataURL throw;
 * that's a browser security rule, not something the extension can work
 * around, so it's reported plainly. */
const grabVideoFrame = (targetSrc) => {
  const videos = [...document.querySelectorAll('video')];
  const video =
    videos.find(v => v.currentSrc === targetSrc || v.src === targetSrc) ||
    videos.find(v => !v.paused) ||
    videos[0];

  if (!video) return { error: 'No video found on this page.' };
  if (!video.videoWidth || !video.videoHeight) {
    return { error: 'That video hasn\'t loaded a frame yet — press play first.' };
  }

  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);

  try {
    return { dataUrl: canvas.toDataURL('image/jpeg', 0.92) };
  } catch {
    return { error: "This site blocks copying its video frames — try a screenshot instead." };
  }
};

// ── toast ──────────────────────────────────────────────────────────────────
/* Injected per-notification rather than kept as a persistent content script,
 * so the extension adds nothing to pages until you actually clip. */
const showToast = (text, tone) => {
  const id = 'frame-atlas-toast';
  document.getElementById(id)?.remove();

  const el = document.createElement('div');
  el.id = id;
  el.textContent = text;
  // Appears at full opacity with no entrance animation, deliberately. A
  // backgrounded or throttled tab freezes the animation timeline and never
  // fires requestAnimationFrame, so anything that fades IN from 0 can get
  // stuck invisible — leaving a clip with no confirmation at all. Nothing
  // here gates visibility on an animation running.
  Object.assign(el.style, {
    position: 'fixed', top: '18px', right: '18px', zIndex: '2147483647',
    maxWidth: '320px', padding: '12px 16px', borderRadius: '10px',
    font: '500 13px/1.45 ui-sans-serif, system-ui, -apple-system, sans-serif',
    color: tone === 'error' ? '#ffb4ab' : '#0f0f10',
    background: tone === 'error' ? '#2a2c31' : '#d9a441',
    border: tone === 'error' ? '1px solid rgba(255,180,171,0.5)' : 'none',
    boxShadow: '0 10px 30px rgba(0,0,0,0.35)',
    opacity: '1', pointerEvents: 'none',
  });
  document.documentElement.appendChild(el);

  setTimeout(() => {
    const fade = el.animate?.([{ opacity: 1 }, { opacity: 0 }], { duration: 240, fill: 'forwards' });
    if (fade) fade.onfinish = () => el.remove();
    // Removed on a timer too, not just onfinish: if the tab is in the
    // background the fade never progresses and onfinish never arrives, which
    // would leave the toast stuck on the page for good.
    setTimeout(() => el.remove(), 400);
  }, tone === 'error' ? 5200 : 2600);
};

const toast = async (tabId, text, tone = 'ok') => {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: showToast,
      args: [text, tone],
    });
  } catch {
    // Injection is refused on chrome:// pages, the Web Store, PDFs, etc.
    // Fall back so the result is never silently lost.
    chrome.notifications?.create({
      type: 'basic',
      iconUrl: 'icon128.png',
      title: 'Frame Atlas',
      message: text,
    });
  }
};

// ── main flow ──────────────────────────────────────────────────────────────
const clip = async (dataUrl, sourceUrl, baseUrl) => {
  const cookie = await readSessionCookie(baseUrl);
  const headers = { 'Content-Type': 'application/json' };
  if (cookie) headers['X-FA-Session'] = cookie;

  const res = await fetch(`${baseUrl}/api/clip`, {
    method: 'POST',
    headers,
    credentials: 'include',
    body: JSON.stringify({ image: dataUrl, source_url: sourceUrl }),
  });

  let body = {};
  try { body = await res.json(); } catch { /* non-JSON error page */ }

  if (res.status === 401) {
    throw new Error(body.message || `Sign in to Frame Atlas first — open ${baseUrl} and log in.`);
  }
  if (res.status === 403) {
    throw new Error('That Frame Atlas account cannot clip images.');
  }
  if (!res.ok) {
    throw new Error(body.message || `Frame Atlas returned an error (${res.status}).`);
  }
  return body;
};

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!MENUS.some(m => m.id === info.menuItemId) || !tab?.id) return;

  const baseUrl = await getBaseUrl();
  try {
    let dataUrl;
    let sourceUrl = info.srcUrl || tab.url;

    if (info.menuItemId === 'fa-clip-frame') {
      const [{ result } = {}] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: grabVideoFrame,
        args: [info.srcUrl || ''],
      });
      if (!result || result.error) throw new Error(result?.error || 'Could not read that video.');
      dataUrl = result.dataUrl;
      sourceUrl = tab.url;            // the page, not the streaming blob: URL
    } else {
      dataUrl = await fetchImageAsDataUrl(info.srcUrl);
    }

    const out = await clip(dataUrl, sourceUrl, baseUrl);
    if (out.status === 'duplicate') {
      await toast(tab.id, '✓ Already in Frame Atlas');
    } else {
      await toast(tab.id, '✓ Saved to Frame Atlas — tagging now');
    }
  } catch (err) {
    await toast(tab.id, `Frame Atlas: ${err.message}`, 'error');
  }
});

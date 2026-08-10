/* Frame Atlas service worker — V23 offline support.
 *
 * Without this, "offline mode" could never work: the browser can't even fetch
 * index.html or the JS bundle with no connection, so the app never boots and
 * the cached decks in IndexedDB are unreachable. This caches the app shell so
 * Frame Atlas opens offline; the deck DATA still comes from IndexedDB
 * (see hooks/useOfflineCache.js).
 *
 * Deliberately never caches /api — those responses are per-user and
 * auth-sensitive, and a stale /api/auth/me would show the wrong account.
 *
 * ONE exception (V43/Day 25): /api/images/<id>/thumb URLs. They're
 * content-addressed (?v=<checksum> changes whenever the pixels do — see
 * build_image_dict() in app.py), so a cache hit is always the right image,
 * same reasoning as the fingerprinted /assets/ files below. This is also
 * what keeps offline deck viewing working now that deck payloads carry
 * thumb URLs instead of embedded base64 — useOfflineCache.js still saves
 * the deck JSON verbatim, and this is what makes the pictures those URLs
 * point to actually available with no connection.
 */

const CACHE = 'frame-atlas-shell-v1';
const SHELL = '/index.html';
const SW_VERSION = 'v1';   // fetch /__sw_version to confirm which build is live

// flask-cors puts `Vary: Origin` on every response, and Cache API matching
// honours Vary by default. Entries are stored by fetches the worker itself
// makes (no Origin header), but an ES-module request is always sent in cors
// mode WITH one — so the JS bundle missed the cache and the app never booted
// offline, while the stylesheet (no-cors, no Origin) matched fine.
const MATCH_OPTS = { ignoreVary: true };

// Vite fingerprints the bundle filenames, so they can't be hard-coded here.
// Read index.html and pull them out instead — that way one online visit is
// enough to be offline-ready, rather than needing a second visit for the
// fetch handler to warm them up.
const precacheShell = async () => {
  const cache = await caches.open(CACHE);
  const res = await fetch(SHELL, { cache: 'reload' });
  if (!res.ok) return;
  const html = await res.clone().text();
  await cache.put(SHELL, res);

  const assets = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map(m => m[1]);
  await Promise.all(
    assets.map(url =>
      fetch(url)
        .then(r => (r.ok ? cache.put(url, r) : null))
        .catch(() => {})
    )
  );
};

self.addEventListener('install', (event) => {
  event.waitUntil(precacheShell().catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// The clone has to happen synchronously, before the response is handed back to
// the page — awaiting caches.open() first lets the page start reading the body,
// and cloning a used body throws, so nothing ever got cached.
const cacheCopy = (event, request, response) => {
  if (!response || !response.ok || response.type === 'opaque') return response;
  const copy = response.clone();
  event.waitUntil(caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => {}));
  return response;
};

// Replay a cached response with only the headers that matter. The stored
// headers come from whatever served them originally and can carry junk that a
// plain fetch() tolerates but the ES-module loader rejects outright — the Flask
// dev server, for one, emits a doubled `Date`, which made every cached module
// load fail with net::ERR_FAILED while fetch() of the same URL was fine.
const replay = async (hit) => {
  const body = await hit.arrayBuffer();
  return new Response(body, {
    status: 200,
    statusText: 'OK',
    headers: {
      'Content-Type': hit.headers.get('Content-Type') || 'application/octet-stream',
      'Cache-Control': 'no-cache',
    },
  });
};

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // The one deliberate exception to "never cache /api" — see the file
  // header comment. Cache-first, same as /assets/ below: the URL itself
  // changes whenever the image does, so a hit is never stale.
  if (/^\/api\/images\/\d+\/thumb$/.test(url.pathname)) {
    event.respondWith(
      caches
        .match(request, MATCH_OPTS)
        .then((hit) =>
          hit ? replay(hit) : fetch(request).then((response) => cacheCopy(event, request, response))
        )
    );
    return;
  }

  if (url.pathname.startsWith('/api/')) return;

  // Diagnostic hook — confirms which service worker build is actually serving.
  if (url.pathname === '/__sw_version') {
    event.respondWith(
      new Response(JSON.stringify({ version: SW_VERSION, cache: CACHE }), {
        headers: { 'Content-Type': 'application/json' },
      })
    );
    return;
  }

  // SPA routes (/decks/3, /favorites, …) have no file of their own — every
  // navigation resolves to index.html. Network first so a new deploy is
  // picked up immediately, cached shell when there's no connection.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => cacheCopy(event, SHELL, response))
        .catch(async () => {
          const hit = await caches.match(SHELL, MATCH_OPTS);
          return hit ? replay(hit) : Response.error();
        })
    );
    return;
  }

  // Fingerprinted filenames mean a cache hit is always the right version —
  // safe to serve straight from cache and skip the network entirely.
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches
        .match(request, MATCH_OPTS)
        .then((hit) =>
          hit ? replay(hit) : fetch(request).then((response) => cacheCopy(event, request, response))
        )
    );
    return;
  }

  // Everything else same-origin (icons, guide screenshots): fresh when we can,
  // cached when we can't.
  event.respondWith(
    fetch(request)
      .then((response) => cacheCopy(event, request, response))
      .catch(async () => {
        const hit = await caches.match(request, MATCH_OPTS);
        return hit ? replay(hit) : Response.error();
      })
  );
});

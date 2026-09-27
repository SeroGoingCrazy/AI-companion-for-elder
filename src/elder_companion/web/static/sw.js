/* Sunny service worker.
 *
 * Why this exists: the demo runs on venue wifi. Precaching the shell means the app opens
 * instantly and still renders when the network stalls, and it is what makes both surfaces
 * installable to a phone home screen.
 *
 * What it must never touch: anything live. Chat turns, the alert SSE stream, TTS audio and
 * fall snapshots all bypass the worker entirely — a cached alert or a replayed reply would
 * be worse than no worker at all.
 */

const VERSION = "sunny-v1";
const SHELL = `${VERSION}-shell`;
const RUNTIME = `${VERSION}-runtime`;

// Same-origin assets worth having before the first offline load. The two pages are not
// precached: they are per-elder server-rendered HTML, handled network-first below.
const SHELL_ASSETS = [
  "/static/tokens.css",
  "/static/elder.css",
  "/static/family.css",
  "/static/lang.js",
  "/static/elder.js",
  "/static/family.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/maskable-192.png",
  "/static/icons/maskable-512.png",
  "/static/icons/apple-touch-icon.png",
];

// Live paths, never cached and never served from cache.
const BYPASS = [/^\/api\//, /^\/media\//, /^\/healthz$/, /^\/sw\.js$/];

const FONT_HOSTS = ["fonts.googleapis.com", "fonts.gstatic.com"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      // addAll is all-or-nothing; one 404 would leave the app with no worker at all.
      .then((cache) => Promise.allSettled(SHELL_ASSETS.map((url) => cache.add(url))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== SHELL && k !== RUNTIME).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

/** Network first, falling back to the last good copy. Used for the two pages. */
async function networkFirst(request) {
  try {
    const res = await fetch(request);
    if (res && res.ok) {
      const cache = await caches.open(RUNTIME);
      cache.put(request, res.clone());
    }
    return res;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw err;
  }
}

/** Serve from cache at once, refresh in the background. Used for static assets and fonts. */
async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((res) => {
      if (res && (res.ok || res.type === "opaque")) cache.put(request, res.clone());
      return res;
    })
    .catch(() => null);
  return cached || (await network) || Response.error();
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  if (url.origin === self.location.origin) {
    if (BYPASS.some((re) => re.test(url.pathname))) return;

    if (request.mode === "navigate") {
      event.respondWith(networkFirst(request));
      return;
    }
    if (url.pathname.startsWith("/static/")) {
      event.respondWith(staleWhileRevalidate(request, SHELL));
    }
    return;
  }

  // Atkinson Hyperlegible is the whole point of the elder page's legibility; keep it offline.
  if (FONT_HOSTS.includes(url.hostname)) {
    event.respondWith(staleWhileRevalidate(request, RUNTIME));
  }
});

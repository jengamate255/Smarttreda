/* MT5 Dashboard service worker.
   - Network-first for static assets, cache fallback for offline.
     Network-first guarantees the dashboard never runs stale JS: the old
     cache-first strategy served an outdated app.chart.js and crashed the
     app until the worker was forcibly replaced.
   - /api/* is NEVER cached: stale prices/positions are dangerous for
     trading decisions. Offline => requests fail and panels render
     explicit error states instead of silently showing old data. */
const VERSION = "mt5dash-v9";
const STATIC_CACHE = VERSION + "-static";

const STATIC_URLS = [
  "/",
  "/static/index.html",
  "/static/app.core.js",
  "/static/app.chart.js",
  "/static/app.panels.js",
  "/static/app.dojo.js",
  "/static/app.risk.js",
  "/static/app.agents.js",
  "/static/app.sound.js",
  "/static/app.navigation.js",
  "/static/app.operations.js",
  "/static/app.diagnostic.js",
  "/static/app.portfolio.js",
  "/static/operations.css",
  "/static/dojo.css",
  "/static/style.css",
  "/static/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/vendor/lightweight-charts.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(STATIC_CACHE);
    // Add each URL individually: a single missing/404 asset must never
    // abort the install. (addAll() rejecting on one 404 left the previous
    // worker + its stale cache in control of the page.)
    await Promise.allSettled(STATIC_URLS.map((u) => cache.add(u)));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys.filter((k) => k.startsWith("mt5dash-") && k !== STATIC_CACHE)
        .map((k) => caches.delete(k))
    );
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return;
  if (url.origin !== location.origin) return;

  // Live trading data: network only, no cache fallback.
  if (url.pathname.startsWith("/api/") || url.pathname === "/ws") {
    return; // default browser handling -> surfaces real network errors
  }

  // The worker script itself: always network (browsers revalidate it).
  if (url.pathname === "/sw.js") return;

  // Static assets + app shell: network-first, cache fallback (offline).
  event.respondWith((async () => {
    const cache = await caches.open(STATIC_CACHE);
    try {
      const resp = await fetch(event.request);
      if (resp && resp.ok) cache.put(event.request, resp.clone());
      return resp;
    } catch (err) {
      const cached = await cache.match(event.request);
      if (cached) return cached;
      if (url.pathname === "/") {
        const shell = await cache.match("/static/index.html");
        if (shell) return shell;
      }
      throw err;
    }
  })());
});

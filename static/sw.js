/* MT5 Dashboard service worker.
   - Cache-first for static assets only.
   - /api/* is NEVER cached: stale prices/positions are dangerous for
     trading decisions. Offline => requests fail and panels render
     explicit error states instead of silently showing old data. */
const VERSION = "mt5dash-v2";
const STATIC_CACHE = VERSION + "-static";

const STATIC_URLS = [
  "/",
  "/static/index.html",
  "/static/app.js",
  "/static/style.css",
  "/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/vendor/lightweight-charts.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(STATIC_URLS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k.startsWith("mt5dash-") && k !== STATIC_CACHE)
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return;
  if (url.origin !== location.origin) return;

  // Live trading data: network only, no cache fallback.
  if (url.pathname.startsWith("/api/") || url.pathname === "/ws") {
    return; // default browser handling -> surfaces real network errors
  }

  // Static assets: cache-first with background refresh.
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const networkFetch = fetch(event.request)
        .then((resp) => {
          if (resp.ok) {
            const copy = resp.clone();
            caches.open(STATIC_CACHE).then((cache) => cache.put(event.request, copy));
          }
          return resp;
        })
        .catch(() => cached);
      return cached || networkFetch;
    })
  );
});

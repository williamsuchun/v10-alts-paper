// Service worker — offline cache for shell, network-first for data
const VERSION = "v1";
const SHELL_CACHE = `v10-shell-${VERSION}`;
const SHELL_FILES = [
  "./",
  "./index.html",
  "./style.css",
  "./app.js",
  "./manifest.json",
  "./icon.svg",
  "./icon-192.png",
  "./icon-512.png",
  "./apple-touch-icon.png",
  "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL_FILES)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // For raw.githubusercontent (state data): network-first, no cache
  if (url.hostname === "raw.githubusercontent.com") {
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
    return;
  }
  // For shell: cache-first, fall back to network
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request).then((res) => {
      // Update cache for shell files
      if (SHELL_FILES.some((f) => e.request.url.endsWith(f.replace("./", "")))) {
        const clone = res.clone();
        caches.open(SHELL_CACHE).then((c) => c.put(e.request, clone));
      }
      return res;
    }))
  );
});

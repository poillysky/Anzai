/* Anzai PWA shell — precache app chrome; API stays network-first. */
const CACHE = "anzai-shell-v1";
const PRECACHE = [
  "/",
  "/market",
  "/news",
  "/analysis",
  "/agent",
  "/login",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/apple-touch-icon-180.png",
  "/avatars/anzai.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE).catch(() => undefined)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
    ),
  );
  self.clients.claim();
});

function isApi(url) {
  return (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/backend/api/")
  );
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  let url;
  try {
    url = new URL(req.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;

  // API: network-only (app shows offline UI from client)
  if (isApi(url)) {
    event.respondWith(
      fetch(req).catch(
        () =>
          new Response(JSON.stringify({ detail: "offline" }), {
            status: 503,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );
    return;
  }

  // Navigations / static: stale-while-revalidate shell
  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(req);
      const network = fetch(req)
        .then((res) => {
          if (res && res.ok && (req.mode === "navigate" || req.destination === "script" || req.destination === "style" || req.destination === "image" || url.pathname.endsWith(".webmanifest"))) {
            cache.put(req, res.clone()).catch(() => undefined);
          }
          return res;
        })
        .catch(() => cached);
      return cached || network;
    }),
  );
});

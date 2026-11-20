const CACHE = "relay-shell-v1";
const SHELL = ["/", "/manifest.webmanifest", "/static/icons/icon-192.svg", "/static/icons/icon-512.svg"];
self.addEventListener("install", event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))));
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request).then(r => r || new Response("RELAY is offline. Reconnect to continue shopping.", {status: 503, headers: {"Content-Type":"text/plain"}}))));
});

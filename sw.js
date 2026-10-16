// Pronóstico real o no tanto · permite instalarla y abrirla aunque falle la señal.
// La página y los datos se piden siempre primero a internet; la copia guardada es solo de respaldo.
const CACHE = "rnt-v1";
const BASE = ["./", "./index.html", "./manifest.webmanifest", "./icono-192.png", "./icono-512.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(BASE)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin) return;   // las consultas del clima van directo
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok) { const copia = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copia)); }
    return r;
  }).catch(() => caches.match(e.request).then(r => r || caches.match("./index.html"))));
});

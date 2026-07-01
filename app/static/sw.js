/* Service Worker – PWA Offline Cache */
// Cache-Namen bei jedem Deploy mit spürbaren JS/CSS-Änderungen erhöhen (v1 -> v2 -> ...):
// der activate-Handler löscht dann automatisch alle Caches mit altem Namen, statt dass
// veraltete Board-Skripte unbegrenzt im Cache liegen bleiben ("F5 nötig nach Update").
const CACHE = 'ec-v2';
const BOARD_CACHE = 'ec-board-v2';
// STAB-3: Kartenkacheln-Cache. Eigener Bucket (getrennt von CACHE/BOARD_CACHE,
// damit ein App-Update den Tile-Cache nicht mitloescht) mit weicher Groessen-
// Grenze (LRU-artig: aeltester Eintrag zuerst raus) — Kacheln fuer ein Gebiet
// koennen sonst unbegrenzt wachsen. Nuetzlich nicht nur bei Totalausfall,
// sondern v.a. bei LANGSAMEM Netz (das haeufigere Problem im Feld).
const TILE_CACHE = 'ec-tiles-v1';
const TILE_CACHE_MAX_ENTRIES = 800;
const PRECACHE = [
  '/',
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/js/alpine.min.js',
  '/static/js/htmx.min.js',
  '/static/js/sortable.min.js',
  '/static/manifest.webmanifest',
  '/login',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE && k !== BOARD_CACHE && k !== TILE_CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Kartenkacheln-Anbieter (aktuell: {a,b,c}.tile.openstreetmap.org, siehe
// karte.html L.tileLayer). Nur DIESE Cross-Origin-Requests werden gecacht —
// alle anderen (Fonts, Drittanbieter-APIs etc.) bleiben unangetastet.
function isMapTileRequest(url) {
  return /(^|\.)tile\.openstreetmap\.org$/.test(url.hostname);
}

async function trimTileCache(cache) {
  const keys = await cache.keys();
  const excess = keys.length - TILE_CACHE_MAX_ENTRIES;
  if (excess > 0) {
    // Cache.keys() liefert Eintraege in Einfuegereihenfolge -> die ersten N
    // sind die aeltesten (einfache FIFO-Naeherung an LRU).
    await Promise.all(keys.slice(0, excess).map(req => cache.delete(req)));
  }
}

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // Never intercept WebSocket or API calls
  if (url.pathname.startsWith('/ws/') || url.pathname.startsWith('/api/')) return;

  // STAB-3: Kartenkacheln cache-first bedienen (schnell bei langsamem Netz),
  // im Hintergrund auffrischen (stale-while-revalidate), Cache weich begrenzen.
  if (isMapTileRequest(url)) {
    e.respondWith(
      caches.open(TILE_CACHE).then(cache =>
        cache.match(e.request).then(cached => {
          const fetchPromise = fetch(e.request).then(res => {
            if (res.ok) {
              cache.put(e.request, res.clone()).then(() => trimTileCache(cache));
            }
            return res;
          }).catch(() => cached);
          return cached || fetchPromise;
        })
      )
    );
    return;
  }

  // Cross-origin requests (außer Kartenkacheln oben) — Browser direkt zugreifen lassen
  if (url.origin !== location.origin) return;

  // Block mutating requests offline — return 503 with X-Offline header
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(e.request.method)) {
    e.respondWith(
      fetch(e.request).catch(() =>
        new Response('Offline', {
          status: 503,
          headers: { 'X-Offline': '1', 'Content-Type': 'text/plain' },
        })
      )
    );
    return;
  }

  // Board pages (/einsatz/<id>) — network-first, cache last successful response
  if (/^\/einsatz\/\d+$/.test(url.pathname)) {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          if (res.ok) {
            const clone = res.clone();
            caches.open(BOARD_CACHE).then(c => c.put(e.request, clone));
          }
          return res;
        })
        .catch(async () => {
          const cached = await caches.match(e.request, { cacheName: BOARD_CACHE });
          if (cached) {
            // Inject offline banner into the cached HTML response
            const html = await cached.text();
            const banner = `<div id="offline-banner" style="position:fixed;top:0;left:0;right:0;z-index:9999;background:#d42225;color:#fff;text-align:center;padding:6px 12px;font-size:.85rem;">
              Offline-Modus — zuletzt synchronisiert: ${new Date(cached.headers.get('date') || Date.now()).toLocaleString('de-AT')}
            </div>`;
            const patched = html.replace('<body', `${banner}<body`);
            return new Response(patched, {
              status: 200,
              headers: { 'Content-Type': 'text/html; charset=utf-8', 'X-Offline': '1' },
            });
          }
          return caches.match('/') || new Response('Offline', { status: 503 });
        })
    );
    return;
  }

  // Static assets — stale-while-revalidate (always fetch fresh, serve cache if offline)
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.open(CACHE).then(cache =>
        cache.match(e.request).then(cached => {
          const fetchPromise = fetch(e.request).then(res => {
            if (res.ok) cache.put(e.request, res.clone());
            return res;
          });
          return cached || fetchPromise;
        })
      )
    );
    return;
  }

  // Everything else — network-first, fall back to cache
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});

// Push notification handler
self.addEventListener('push', e => {
  if (!e.data) return;
  let data;
  try { data = JSON.parse(e.data.text()); } catch { data = { title: 'FF Wolfurt', body: e.data.text() }; }
  e.waitUntil(
    self.registration.showNotification(data.title || 'FF Wolfurt', {
      body: data.body || '',
      icon: '/static/img/Logo-rot.png',
      badge: '/static/img/badge.png',
      data: { url: data.url || '/' },
      requireInteraction: true,
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = e.notification.data?.url || '/';
  e.waitUntil(clients.openWindow(url));
});

/* Service Worker – PWA Offline Cache */
// Cache-Namen bei jedem Deploy mit spürbaren JS/CSS-Änderungen erhöhen (v1 -> v2 -> ...):
// der activate-Handler löscht dann automatisch alle Caches mit altem Namen, statt dass
// veraltete Board-Skripte unbegrenzt im Cache liegen bleiben ("F5 nötig nach Update").
const CACHE = 'ec-v13';
const BOARD_CACHE = 'ec-board-v2';
// Objektverwaltung: Offline-Precache der Android-App (objekt_offline_sync.js
// befuellt ihn; hier nur lesen/ergaenzen — App-Updates loeschen ihn nicht)
const OBJEKT_CACHE = 'ec-objekt-v1';
// Nachschlagewerke: Gefahrgut-Offline-Index (network-first) + unveraenderliche
// Rettungskarten-PDFs (cache-first, /nachschlagewerk-cache/). App-Updates
// loeschen ihn nicht (Offline-Bestand bleibt erhalten).
const NW_CACHE = 'ec-nachschlagewerk-v1';
const CORE_PRECACHE = [
  '/',
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/js/alpine.min.js',
  '/static/js/htmx.min.js',
  '/static/js/sortable.min.js',
  '/static/manifest.webmanifest',
  '/login',
];
const OPTIONAL_PRECACHE = [
  '/static/css/leaflet.min.css',
  '/static/js/chart.umd.min.js',
  '/static/js/leaflet.min.js',
  '/static/js/stats_karte.js',
];
const CORE_READY_KEY = '/__precache-core-ready__';

async function precacheGroup(cache, assets, group) {
  const results = await Promise.allSettled(assets.map(asset => cache.add(asset)));
  results.forEach((result, index) => {
    if (result.status === 'rejected') {
      console.warn(`[SW] ${group}-Precache fehlgeschlagen: ${assets[index]}`, result.reason);
    }
  });
  return results.every(result => result.status === 'fulfilled');
}

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(async cache => {
      const [coreReady] = await Promise.all([
        precacheGroup(cache, CORE_PRECACHE, 'Core'),
        precacheGroup(cache, OPTIONAL_PRECACHE, 'Optional'),
      ]);
      if (coreReady) {
        try {
          await cache.put(CORE_READY_KEY, new Response('ok'));
        } catch (error) {
          console.warn('[SW] Core-Precache-Marker konnte nicht gespeichert werden', error);
        }
      }
      await self.skipWaiting();
    })
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.open(CACHE).then(async cache => {
      const coreReady = Boolean(await cache.match(CORE_READY_KEY));
      const keys = await caches.keys();
      const protectedCaches = [CACHE, BOARD_CACHE, OBJEKT_CACHE, NW_CACHE];
      await Promise.all(keys.filter(key => {
        if (protectedCaches.includes(key)) return false;
        // Bei unvollstaendig precachtem Core den letzten App-Shell-Cache behalten.
        if (!coreReady && /^ec-v\d+$/.test(key)) return false;
        return true;
      }).map(key => caches.delete(key)));
      await self.clients.claim();
    })
  );
});

// STAB-3-Regression (2026-07-06): Ein per fetch() aus dem Service Worker
// erneut abgesetzter Tile-Request setzt Sec-Fetch-Dest auf "empty" statt
// "image" (wie beim nativen <img>-Laden) — OSMs Fastly-Edge behandelt das
// als Scraping/Bot-Traffic und blockt mit HTTP 503 (live verifiziert: mit
// abgemeldetem SW laden dieselben Kacheln sofort). Tile-Requests deshalb
// NICHT mehr proxien, sondern wie alle anderen Cross-Origin-Requests direkt
// vom Browser laden lassen (kein e.respondWith → nativer Sec-Fetch-Dest
// bleibt erhalten). Ein eigener Cache-Bucket fuer echtes Offline-Tile-Caching
// muesste anders (z.B. expliziter Vorab-Download) geloest werden.
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // Never intercept WebSocket or API calls
  if (url.pathname.startsWith('/ws/') || url.pathname.startsWith('/api/')) return;

  // Cross-origin requests (inkl. Kartenkacheln) — Browser direkt zugreifen lassen
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

  // Hydranten-/Löschwasser-JSON — network-first, letzte Antwort cachen (Funkloch)
  if (/^\/einsatz\/\d+\/hydranten\.json$/.test(url.pathname)) {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          if (res.ok) {
            const clone = res.clone();
            caches.open(BOARD_CACHE).then(c => c.put(e.request, clone));
          }
          return res;
        })
        .catch(() => caches.match(e.request, { cacheName: BOARD_CACHE })
          || new Response('{"hydranten":[],"stand":null}', { headers: { 'Content-Type': 'application/json' } }))
    );
    return;
  }

  // Gefahrgut-Offline-Index — network-first, letzte Antwort in NW_CACHE (offline nutzbar)
  if (url.pathname === '/nachschlagewerke/gefahrgut/index.json') {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          if (res.ok) {
            const clone = res.clone();
            caches.open(NW_CACHE).then(c => c.put(e.request, clone));
          }
          return res;
        })
        .catch(() => caches.match(e.request, { cacheName: NW_CACHE })
          || new Response('{"anzahl":0,"eintraege":[]}', { headers: { 'Content-Type': 'application/json' } }))
    );
    return;
  }

  // Rettungskarten-Katalog-Index — network-first, letzte Antwort in NW_CACHE (offline suchbar)
  if (url.pathname === '/nachschlagewerke/rettungskarten/katalog.json') {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          if (res.ok) {
            const clone = res.clone();
            caches.open(NW_CACHE).then(c => c.put(e.request, clone));
          }
          return res;
        })
        .catch(() => caches.match(e.request, { cacheName: NW_CACHE })
          || new Response('{"anzahl":0,"eintraege":[]}', { headers: { 'Content-Type': 'application/json' } }))
    );
    return;
  }

  // Rettungskarten-PDFs (unveraenderliche UUID-Pfade) — cache-first aus NW_CACHE,
  // Netz als Fallback. Nach erstem Aufruf offline verfuegbar (PR 5).
  if (url.pathname.startsWith('/nachschlagewerk-cache/')) {
    e.respondWith(
      caches.open(NW_CACHE).then(cache =>
        cache.match(e.request).then(cached => {
          if (cached) return cached;
          return fetch(e.request).then(res => {
            if (res.ok) cache.put(e.request, res.clone());
            return res;
          });
        })
      )
    );
    return;
  }

  // Board pages (/einsatz/<id>[/info]) und Objekt-Einsatzansichten (/objekte/<id>/einsatz)
  // — network-first, cache last successful response (Objektinfo im Fahrzeug bei Funkloch)
  if (/^\/einsatz\/\d+(\/info)?$/.test(url.pathname) || /^\/objekte\/\d+\/einsatz$/.test(url.pathname)) {
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
          // Erst besuchte Seiten (BOARD_CACHE), dann Objekt-Precache (Android-Sync)
          const cached = await caches.match(e.request, { cacheName: BOARD_CACHE })
            || await caches.match(e.request, { cacheName: OBJEKT_CACHE });
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

  // Objekt-Medien (Thumbs/Seitenbilder/Einzel-PDFs) — cache-first aus dem
  // Offline-Precache (objekt_offline_sync.js, Android-App), Netz als Fallback.
  // Dateien sind unveraenderlich (UUID-Pfade) → cache-first ist sicher.
  if (url.pathname.startsWith('/objekt-medien/')) {
    e.respondWith(
      caches.open(OBJEKT_CACHE).then(cache =>
        cache.match(e.request).then(cached => {
          if (cached) return cached;
          return fetch(e.request).then(res => {
            if (res.ok) cache.put(e.request, res.clone());
            return res;
          });
        })
      )
    );
    return;
  }

  // Static assets — stale-while-revalidate (always fetch fresh, serve cache if offline)
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.open(CACHE).then(async cache => {
        const cached = await cache.match(e.request);
        const fetchPromise = fetch(e.request).then(async res => {
          if (res.ok) {
            try {
              await cache.put(e.request, res.clone());
              const keys = await cache.keys();
              await Promise.all(keys
                .filter(key => key.url !== e.request.url && new URL(key.url).pathname === url.pathname)
                .map(key => cache.delete(key)));
            } catch (error) {
              console.warn(`[SW] Static-Asset konnte nicht gecacht werden: ${url.pathname}`, error);
            }
          }
          return res;
        });
        if (cached) {
          // Der Fetch laeuft best-effort weiter; catch verhindert einen
          // unbehandelten Reject, waehrend sofort die Cache-Response zurueckgeht.
          void fetchPromise.catch(error => {
            console.warn(`[SW] Static-Revalidierung fehlgeschlagen: ${url.pathname}`, error);
          });
          return cached;
        }
        return fetchPromise.catch(async error => {
          console.warn(`[SW] Static-Asset nicht verfuegbar: ${url.pathname}`, error);
          const offlineCached = await cache.match(e.request, { ignoreSearch: true })
            || await caches.match(e.request, { ignoreSearch: true });
          if (offlineCached) return offlineCached;
          return new Response('Static asset unavailable', {
            status: 503,
            headers: { 'Content-Type': 'text/plain', 'X-Offline': '1' },
          });
        });
      })
    );
    return;
  }

  // Everything else — network-first, fall back to cache
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});

function decodeBase64Url(value) {
  const padding = '='.repeat((4 - value.length % 4) % 4);
  const raw = atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, char => char.charCodeAt(0));
}

self.addEventListener('pushsubscriptionchange', e => {
  e.waitUntil((async () => {
    let opts = e.oldSubscription?.options;
    if (!opts?.userVisibleOnly || !opts?.applicationServerKey) {
      const response = await fetch('/push/vapid-public-key', {
        credentials: 'same-origin',
      });
      if (!response.ok) throw new Error('VAPID-Schluessel nicht verfuegbar');
      const data = await response.json();
      if (!data.publicKey) throw new Error('VAPID-Schluessel fehlt');
      opts = {
        userVisibleOnly: true,
        applicationServerKey: decodeBase64Url(data.publicKey),
      };
    }

    const subscription = await self.registration.pushManager.subscribe(opts);
    const response = await fetch('/push/subscribe', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(subscription),
    });
    if (!response.ok) throw new Error('Push-Abo konnte nicht gespeichert werden');
  })().catch(() => {}));
});

// Push notification handler
self.addEventListener('push', e => {
  if (!e.data) return;
  let data;
  try { data = JSON.parse(e.data.text()); } catch { data = { title: 'FF Wolfurt', body: e.data.text() }; }
  const kind = data.kind || 'classic';
  const opts = {
    body: data.body || '',
    icon: '/static/img/Logo-rot.png',
    badge: '/static/img/badge.png',
    data: { url: data.url || '/', kind, live: data.live },
    requireInteraction: true,
  };

  if (kind === 'einsatz_live' || kind === 'gsl_live') {
    opts.tag = data.tag;
    opts.renotify = data.live && data.live.alert === true;
    opts.silent = !opts.renotify;
  } else if (kind === 'einsatz_live_end' || kind === 'gsl_live_end') {
    opts.tag = data.tag;
    opts.renotify = false;
    opts.silent = true;
    opts.requireInteraction = false;
  }

  let shown = self.registration.showNotification(data.title || 'Einsatzcockpit', opts);
  if (kind === 'einsatz_live_end' || kind === 'gsl_live_end') {
    shown = shown.then(() => new Promise(resolve => setTimeout(resolve, 8000)))
      .then(() => self.registration.getNotifications({ tag: opts.tag })
        .then(notifications => notifications.forEach(notification => notification.close()))
        .catch(() => {}));
  }
  e.waitUntil(shown);
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const targetUrl = e.notification.data?.url || '/';
  const absoluteTargetUrl = new URL(targetUrl, self.location.origin).href;
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      const matchingClient = windowClients.find(c => {
        try { return new URL(c.url).origin === self.location.origin; } catch { return false; }
      });
      if (!matchingClient) return clients.openWindow(targetUrl);
      return matchingClient.focus().then(focusedClient => {
        if (focusedClient.navigate && focusedClient.url !== absoluteTargetUrl) {
          return focusedClient.navigate(targetUrl).catch(() => focusedClient);
        }
        return focusedClient;
      });
    })
  );
});

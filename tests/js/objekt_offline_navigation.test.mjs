import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

function worker() {
  const handlers = {};
  const buckets = new Map();
  const origin = 'https://test.local';
  const key = request => new URL(typeof request === 'string' ? request : request.url, origin).href;
  const cacheFor = async name => {
    if (!buckets.has(name)) buckets.set(name, new Map());
    const entries = buckets.get(name);
    return {
      match: async request => entries.get(key(request))?.clone(),
      put: async (request, response) => entries.set(key(request), response.clone()),
      add: async path => entries.set(key(path), new Response(path)),
      keys: async () => [...entries.keys()].map(url => new Request(url)),
      delete: async request => entries.delete(key(request)),
    };
  };
  const caches = {
    open: cacheFor,
    match: async (request, options) => (await cacheFor(options?.cacheName || 'core')).match(request),
    keys: async () => [...buckets.keys()],
    delete: async name => buckets.delete(name),
  };
  vm.runInNewContext(readFileSync('app/static/sw.js', 'utf8'), {
    self: { addEventListener: (name, fn) => { handlers[name] = fn; },
      skipWaiting: async () => {}, clients: { claim: async () => {} } },
    caches, location: { origin }, URL, Response, Headers, console,
    fetch: async () => { throw new Error('offline'); },
  });
  return {
    buckets,
    put: async (path, value, name = 'ec-objekt-v2') => (await cacheFor(name)).put(path, new Response(value)),
    lifecycle: async name => {
      let pending;
      handlers[name]({ waitUntil: promise => { pending = promise; } });
      await pending;
    },
    fetch: async (path, mode = 'navigate') => {
      let pending;
      handlers.fetch({ request: { url: origin + path, mode, method: 'GET' },
        respondWith: promise => { pending = promise; } });
      return pending;
    },
  };
}

test('Listen verwenden bei Query-/Slash-Varianten die synchronisierte Gesamtliste', async () => {
  const sw = worker();
  await sw.put('/objekte/', 'ALLE OBJEKTE');
  await sw.put('/kontakte', 'ALLE KONTAKTE');
  for (const path of ['/objekte/?q=haus&sort=name', '/objekte?q=a', '/kontakte/?q=b']) {
    assert.match(await (await sw.fetch(path)).text(), /ALLE /);
  }
});

test('Detailseite, Slash-Variante und alter Einsatzcache funktionieren ohne Einsatzvermischung', async () => {
  const sw = worker();
  await sw.put('/objekte/1', '<body>DETAIL');
  await sw.put('/objekte/2/einsatz', '<body>EINSATZ');
  assert.match(await (await sw.fetch('/objekte/1/')).text(), /DETAIL/);
  assert.match(await (await sw.fetch('/objekte/2')).text(), /EINSATZ/);
  assert.equal((await sw.fetch('/objekte/2/einsatz?incident_id=5')).status, 503);
  assert.equal((await sw.fetch('/objekte/2', 'cors')).status, 503);
  await sw.put('/objekte/1', '<body>BESUCHT', 'ec-board-v4');
  assert.match(await (await sw.fetch('/objekte/1')).text(), /BESUCHT/);
});

test('Cache-Miss zeigt Offline-HTML nur bei Navigation; Fragmente behalten X-Offline/503', async () => {
  const sw = worker();
  await sw.put('/static/offline.html', 'KEINE VERBINDUNG', 'ec-v27');
  await sw.put('/', 'FALSCHE STARTSEITE', 'core');
  for (const path of ['/objekte/9', '/kontakte', '/kontakte/9', '/andere-seite']) {
    const response = await sw.fetch(path);
    assert.equal(response.status, 503);
    assert.equal(response.headers.get('X-Offline'), '1');
    assert.match(response.headers.get('Content-Type'), /text\/html/);
    assert.equal(await response.text(), 'KEINE VERBINDUNG');
  }
  for (const path of ['/objekte/9', '/objekte/9/stammdaten', '/kontakte']) {
    const response = await sw.fetch(path, 'cors');
    assert.equal(response.status, 503);
    assert.equal(response.headers.get('X-Offline'), '1');
    assert.equal(await response.text(), 'Offline');
  }
});

test('Installation precacht Offline-Seite; Aktivierung erhaelt Objekt- und Boardcache', async () => {
  const sw = worker();
  await sw.put('/objekte/1', 'OBJEKT');
  await sw.put('/einsatz/1', 'BOARD', 'ec-board-v4');
  await sw.put('/', 'ALT', 'ec-v25');
  await sw.lifecycle('install');
  await sw.lifecycle('activate');
  assert.equal(sw.buckets.has('ec-v25'), false);
  assert.equal(sw.buckets.has('ec-objekt-v2'), true);
  assert.equal(sw.buckets.has('ec-board-v4'), true);
  assert.match(await (await sw.fetch('/objekte/9')).text(), /offline.html/);
});

test('Android startet Sync mit leerem Cache sofort; Plattform und native Einstellung bleiben massgeblich', async () => {
  const source = readFileSync('app/static/js/objekt_offline_sync.js', 'utf8');
  for (const [platform, enabled, expected] of [['android', true, 1], ['android', false, 0], ['web', true, 0]]) {
    const requests = [];
    const timers = [];
    const caches = { open: async () => ({ keys: async () => [] }) };
    const window = { caches, Capacitor: { getPlatform: () => platform,
      Plugins: { DeviceKeepalive: { getObjectSyncSettings: async () => ({ enabled }) } } } };
    vm.runInNewContext(source, {
      window, caches,
      localStorage: { getItem: () => null },
      setTimeout: (fn, delay) => { timers.push(delay); },
      fetch: async url => { requests.push(url); return { ok: false, status: 403 }; },
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests.length, expected);
    if (expected) assert.equal(requests[0], '/api/objekte/sync');
    assert.ok(timers.every(delay => delay === 6 * 60 * 60 * 1000));
  }
});

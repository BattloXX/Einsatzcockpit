import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const MEDIA = '/objekt-medien/seite/1/bild';
const STAND = 'ec_objekt_medien_stand';
const LAST = 'ec_objekt_sync_zuletzt';
function manifest(version = 'v1') {
  return { objekte: [{ einsatz_url: '/objekte/1/einsatz', seiten: [{ urls: [MEDIA],
    ...(version ? { version, versioned_urls: [MEDIA + '?v=' + version] } : {}),
  }] }] };
}
function webview(initial = manifest()) {
  let now = 10000000;
  let current = initial;
  let putError = null;
  let putGate;
  let httpStatus = 200;
  let gate;
  let storageError = false;
  const entries = new Map();
  const storage = new Map();
  const requests = [];
  const reports = [];
  const origin = 'https://test.local';
  const key = request => new URL(typeof request === 'string' ? request : request.url, origin).href;
  const cache = {
    match: async request => entries.get(key(request))?.clone(),
    keys: async () => [...entries.keys()].map(url => new Request(url)),
    delete: async request => entries.delete(key(request)),
    put: async (request, response) => {
      if (key(request).includes('/objekt-medien/')) {
        if (putGate) await putGate();
        if (putError) throw putError;
      }
      entries.set(key(request), response.clone());
    },
  };
  const localStorage = {
    getItem: name => { if (storageError) throw new Error('storage'); return storage.get(name) ?? null; },
    setItem: (name, value) => { if (storageError) throw new Error('storage'); storage.set(name, value); },
    removeItem: name => storage.delete(name),
  };
  const window = { location: { origin }, caches: {}, ObjektSyncNative: {
    reportStatus: (cached, total, activity) => reports.push(activity),
  } };
  vm.runInNewContext(readFileSync('app/static/js/objekt_offline_sync.js', 'utf8'), {
    window, localStorage, URL, Date: { now: () => now },
    caches: { open: async () => cache, delete: async () => { entries.clear(); return true; } },
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (url === '/api/objekte/sync') {
        if (gate) await gate;
        return Response.json(current);
      }
      if (url === '/kontakte/offline-sync') return Response.json({ urls: [] });
      return new Response('PNG', { status: url.startsWith('/objekt-medien/') ? httpStatus : 200 });
    },
  });
  return { entries, storage, requests, reports, cache,
    sync: window.objektOfflineSync, clear: window.objektOfflineCacheLeeren,
    setManifest: value => { current = value; },
    setPutError: value => { putError = value; },
    setPutGate: value => { putGate = value; },
    setHttpStatus: value => { httpStatus = value; },
    setStorageError: value => { storageError = value; },
    setGate: value => { gate = value; },
    advance: ms => { now += ms; },
    mediaRequests: () => requests.filter(r => r.url.startsWith('/objekt-medien/')),
    versions: () => JSON.parse(storage.get(STAND) || '{}'),
  };
}

test('gleiche Medienversion wird uebersprungen, neue Version unter plain Pfad gespeichert', async () => {
  const w = webview();
  assert.equal(await w.sync(), true);
  assert.equal(w.mediaRequests()[0].url, MEDIA + '?v=v1');
  assert.equal(w.mediaRequests()[0].options.cache, undefined);
  assert.equal(await (await w.cache.match(MEDIA)).text(), 'PNG');
  assert.equal(await w.cache.match(MEDIA + '?v=v1'), undefined);
  assert.equal(w.versions()[MEDIA], 'v1');
  await w.sync({ force: true });
  assert.equal(w.mediaRequests().length, 1);
  assert.match(w.reports.at(-1), /Übersprungen: 2/);
  w.setManifest(manifest('v2'));
  await w.sync({ force: true });
  assert.equal(w.mediaRequests().at(-1).url, MEDIA + '?v=v2');
  assert.equal(w.versions()[MEDIA], 'v2');
  assert.match(w.reports.at(-1), /Geladen: 2 .*Bytes geladen: 6 .*Fehler: 0/);
});

test('verlorener Cache Storage laedt versioniert trotz gespeichertem Stand', async () => {
  const w = webview();
  await w.sync();
  w.entries.clear();
  await w.sync({ force: true });
  assert.equal(w.mediaRequests().length, 2);
  assert.equal(w.mediaRequests()[1].url, MEDIA + '?v=v1');
});

test('put-Fehler speichert keine neue Version und meldet QuotaExceeded in Diagnose', async () => {
  const w = webview();
  await w.sync();
  w.setManifest(manifest('v2'));
  w.storage.delete(LAST);
  w.setPutError(new DOMException('Cache voll', 'QuotaExceededError'));
  assert.equal(await w.sync(), true); // Fehler stoppen den Worker nicht, bestaetigen den Lauf aber nicht (kein LAST)
  assert.equal(w.versions()[MEDIA], 'v1');
  assert.equal(w.storage.has(LAST), false);
  assert.match(w.reports.at(-1), /Fehler: 1 .*QuotaExceededError: Cache voll/);
  w.setPutError(null);
  assert.equal(await w.sync(), true);
  assert.equal(w.versions()[MEDIA], 'v2');
});

test('HTTP-Fehler bestaetigt keine Version und kein erfolgreicher Lauf', async () => {
  const w = webview();
  w.setHttpStatus(503);
  assert.equal(await w.sync(), true); // Fehler stoppen den Worker nicht, bestaetigen den Lauf aber nicht (kein LAST)
  assert.equal(w.versions()[MEDIA], undefined);
  assert.equal(w.storage.has(LAST), false);
  assert.match(w.reports.at(-1), /HTTP 503/);
});

test('entfernte Pfade werden aus Cache und gespeichertem Stand geloescht', async () => {
  const w = webview();
  await w.sync();
  w.setManifest({ objekte: [] });
  await w.sync({ force: true });
  assert.deepEqual(w.versions(), {});
  assert.equal(await w.cache.match(MEDIA), undefined);
});

test('parallele Aufrufe teilen Promise, 30 Minuten gelten nur fuer entprellte Aufrufe, force umgeht Intervall', async () => {
  const w = webview();
  let release;
  w.setGate(new Promise(resolve => { release = resolve; }));
  const first = w.sync();
  assert.equal(w.sync(), first);
  assert.equal(w.sync({ force: true }), first);
  release();
  await first;
  const count = w.requests.length;
  assert.equal(await w.sync({ entprellen: true }), true);
  assert.equal(w.requests.length, count);
  w.advance(30 * 60 * 1000 - 1);
  await w.sync({ entprellen: true });
  assert.equal(w.requests.length, count);
  w.advance(1);
  await w.sync();
  assert.ok(w.requests.length > count);
  const next = w.requests.length;
  await w.sync({ force: true });
  assert.ok(w.requests.length > next);
  await w.clear();
  assert.equal(w.storage.has(LAST), false);
  assert.equal(w.storage.has(STAND), false);
});

test('alte Manifeste behalten URL-Mengen-Delta auch mit Query-Strings', async () => {
  const old = manifest(null);
  old.objekte[0].seiten[0].urls = [MEDIA + '?legacy=1'];
  const w = webview(old);
  await w.sync();
  assert.equal(w.mediaRequests()[0].url, MEDIA + '?legacy=1');
  assert.equal(await (await w.cache.match(MEDIA)).text(), 'PNG');
  await w.sync({ force: true });
  assert.equal(w.mediaRequests().length, 1);
  assert.deepEqual(w.versions(), {});
});

test('ungueltiges oder gesperrtes localStorage verhindert Sync nicht', async () => {
  const w = webview();
  w.storage.set(STAND, 'kaputt');
  await w.sync();
  assert.equal(w.versions()[MEDIA], 'v1');
  w.setStorageError(true);
  assert.equal(await w.sync(), true);
});


test('Version wird erst nach aufgeloestem cache.put bestaetigt', async () => {
  const w = webview();
  let release;
  let reached;
  const pending = new Promise(resolve => { release = resolve; });
  const putting = new Promise(resolve => { reached = resolve; });
  w.setPutGate(async () => { reached(); await pending; });
  const run = w.sync();
  await putting;
  assert.equal(w.versions()[MEDIA], undefined);
  assert.equal(w.storage.has(LAST), false);
  release();
  assert.equal(await run, true);
  assert.equal(w.versions()[MEDIA], 'v1');
});

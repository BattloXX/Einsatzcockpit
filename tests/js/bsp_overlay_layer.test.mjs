import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/static/js/bsp_overlay_layer.js', import.meta.url), 'utf8');
const overlay = (id = 1, extra = {}) => ({id, name: 'Halle', objekt_name: 'Betrieb', geschoss: 'EG', bereich: 'Nord',
  freigabe_nr: 2, freigegeben_am: '2026-10-30T10:00:00Z', bounds: [[47, 9], [48, 10]],
  raster_url: '/raster/' + id, standard_sichtbar: true, standard_deckkraft: 0.6,
  sichtbarkeit: 'INHERIT', deckkraft: null, kann_einstellen: true, ...extra});
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture({liste = [overlay()], storageFail = false, kann = true, nurLokal = false, listUrl = "/einsatz/1/lagefuehrung/bsp-overlays.json", storage = new Map()} = {}) {
  const elements = new Map();
  function el(id) {
    if (!elements.has(id)) elements.set(id, {events: {}, value: '', checked: false, disabled: false,
      textContent: '', children: [], addEventListener(name, fn) {this.events[name] = fn;},
      appendChild(child) {this.children.push(child);}});
    return elements.get(id);
  }
  const panes = {}, active = new Set(), created = [], requests = [];
  let daten = liste, antwort;
  const map = {getPane: name => panes[name], createPane(name) {return panes[name] = {style: {}};},
    removeLayer: layer => active.delete(layer)};
  const context = {window: {localStorage: {
    getItem(key) {if (storageFail) throw Error('gesperrt'); return storage.get(key) || null;},
    setItem(key, value) {if (storageFail) throw Error('voll'); storage.set(key, value);},
    removeItem(key) {if (storageFail) throw Error('gesperrt'); storage.delete(key);}
  }}, document: {getElementById: id => nurLokal && ["lft-bsp-scope", "lft-bsp-alle"].includes(id) ? null : el(id), createElement: () => ({})},
  fetch: async (url, options) => {
    requests.push({url, options});
    if (antwort) return antwort(url, options);
    return {ok: true, json: async () => daten};
  }, L: {imageOverlay(url, bounds, options) {
    const layer = {url, bounds, options, events: {}, on(name, fn) {this.events[name] = fn; return this;},
      addTo() {active.add(this); return this;}, setOpacity(value) {this.opacity = value;}};
    created.push(layer); return layer;
  }}};
  vm.runInNewContext(source, context);
  const controller = context.window.EcpBspOverlay.init(map, {listUrl, nurLokal,
    postUrlTemplate: '/einsatz/1/bsp/{overlay_id}/einstellung', csrfToken: 'token', kannEinstellen: kann});
  return {controller, el, active, created, panes, storage, requests, api: context.window.EcpBspOverlay,
    setListe: value => {daten = value;}, setAntwort: value => {antwort = value;},
    event(id, event = 'click') {return el(id).events[event].call(el(id));}};
}

test('INHERIT ON OFF und Deckkraft werden effektiv aufgeloest', () => {
  const {api} = fixture();
  assert.equal(api.aufloesen(overlay(), null).sichtbar, true);
  assert.equal(api.aufloesen(overlay(1, {standard_sichtbar: false}), null).sichtbar, false);
  assert.equal(api.aufloesen(overlay(1, {sichtbarkeit: 'ON', standard_sichtbar: false}), null).sichtbar, true);
  assert.equal(api.aufloesen(overlay(1, {sichtbarkeit: 'OFF'}), null).sichtbar, false);
  assert.equal(api.aufloesen(overlay(1, {deckkraft: 0.1}), null).deckkraft, 0.1);
  assert.equal(api.aufloesen(overlay(), null).deckkraft, 0.6);
});

test('BSP-Pane bleibt unter Vektoren, Raster stoeren weder Touch noch Geoman', async () => {
  const f = fixture({liste: [overlay(1), overlay(2, {sichtbarkeit: 'OFF'})]});
  await flush();
  assert.equal(f.panes.bsp.style.zIndex, 250);
  assert.equal(f.panes.bsp.style.pointerEvents, 'none');
  assert.equal(f.active.size, 1);
  assert.equal(f.created[0].options.interactive, false);
  assert.equal(f.created[0].options.pmIgnore, true);
  assert.equal(f.created[0].options.pane, 'bsp');
  assert.equal(f.el('lft-bsp').hidden, false);
  assert.match(f.el('lft-bsp-auswahl').children[0].textContent, /Halle.*EG.*Nord.*Stand 2/);
});

test('lokale Ueberschreibung hat Vorrang, ist markiert und bleibt nach WS-Reload', async () => {
  const f = fixture(); await flush();
  f.event('lft-bsp-aus');
  assert.equal(f.active.size, 0);
  assert.match(f.el('lft-bsp-lokal').textContent, /Überschreibung/);
  assert.match([...f.storage.keys()][0], /einsatz\/1.*:1$/);
  await f.controller.handleEvent({type: 'lagefuehrung.bsp.changed'});
  assert.equal(f.active.size, 0);
  assert.equal(f.requests.filter(r => r.options.method === 'POST').length, 0);
  f.event('lft-bsp-reset');
  assert.equal(f.active.size, 1);
  assert.equal(f.storage.size, 0);
});

test('localStorage-Ausfall verhindert weder lokale Anzeige noch sofortiges Opt-out', async () => {
  const f = fixture({storageFail: true}); await flush();
  assert.equal(f.active.size, 1);
  f.event('lft-bsp-aus');
  assert.equal(f.active.size, 0);
  assert.match(f.el('lft-bsp-hinweis').textContent, /nur bis zum Schließen/);
  await f.controller.reload();
  assert.equal(f.active.size, 0);
});

test('lokale Deckkraft setzt sich gegen geaenderte Einsatzvorgabe durch', async () => {
  const f = fixture({kann: false, liste: [overlay(1, {kann_einstellen: false})]}); await flush();
  f.el('lft-bsp-deckkraft').value = '0.3'; f.event('lft-bsp-deckkraft', 'change');
  assert.equal(f.created[0].opacity, 0.3);
  f.setListe([overlay(1, {sichtbarkeit: 'OFF', deckkraft: 0.9, kann_einstellen: false})]);
  await f.controller.reload();
  assert.equal(f.active.size, 1);
  assert.equal(f.created[0].opacity, 0.3);
  assert.equal(f.el('lft-bsp-alle').disabled, true);
});

test('Einsatzvorgabe sendet CSRF-POST, persoenliche Vorgabe bleibt vorrangig', async () => {
  const f = fixture(); await flush();
  f.el('lft-layer-bsp').checked = false; f.event('lft-layer-bsp', 'change'); await flush();
  const post = f.requests.find(r => r.options.method === 'POST');
  assert.equal(post.url, '/einsatz/1/bsp/1/einstellung');
  assert.equal(post.options.headers['X-CSRF-Token'], 'token');
  assert.deepEqual(JSON.parse(post.options.body), {sichtbarkeit: 'OFF', deckkraft: 0.6});
  f.event('lft-bsp-reset'); await flush();
  assert.deepEqual(JSON.parse(f.requests.filter(r => r.options.method === 'POST').at(-1).options.body),
    {sichtbarkeit: 'INHERIT', deckkraft: null});
});

test('Rasterfehler entfernt Ebene still, Objektwechsel entfernt alte Overlays', async () => {
  const f = fixture({liste: [overlay(1), overlay(2)]}); await flush();
  f.created[0].events.error();
  assert.equal(f.active.size, 1);
  assert.match(f.el('lft-bsp-hinweis').textContent, /nicht verfügbar/);
  await f.controller.reload();
  assert.equal(f.active.size, 1);
  f.setListe([]);
  await f.controller.handleEvent({type: 'objektgefahren'});
  assert.equal(f.active.size, 0);
  assert.equal(f.el('lft-bsp').hidden, true);
});

test('spaete Listen-Antwort kann entfernte Overlays nicht wiederherstellen', async () => {
  const f = fixture(); await flush();
  let resolve;
  f.setAntwort(() => new Promise(r => {resolve = r;}));
  const alt = f.controller.reload();
  f.setAntwort(null); f.setListe([]);
  await f.controller.reload();
  resolve({ok: true, json: async () => [overlay()]}); await alt;
  assert.equal(f.active.size, 0);
});

test('BSP-Raster und Listen umgehen den Service-Worker-Offlinecache', () => {
  const sw = readFileSync(new URL('../../app/static/sw.js', import.meta.url), 'utf8');
  for (const path of ['/objekt-medien/bsp-overlay/1/raster?v=2-sha', '/einsatz/1/lagefuehrung/bsp-overlays.json',
    '/einsatz/1/info/bsp-overlays.json', '/lage/1/bsp-overlays.json', '/einheit/bsp-overlays.json?einheit=1', '/objekte/1/bsp-overlays.json']) {
    const listeners = new Map();
    vm.runInNewContext(sw, {self: {addEventListener: (name, fn) => listeners.set(name, fn)},
      URL, location: {origin: 'https://ecp.test'}, caches: {open() {assert.fail('Kein Offlinecache');}}});
    listeners.get('fetch')({request: {url: 'https://ecp.test' + path, method: 'GET'},
      respondWith() {assert.fail('Privater Browser-HTTP-Cache muss greifen');}});
  }
});

for (const listUrl of ['/einsatz/1/info/bsp-overlays.json', '/lage/1/bsp-overlays.json', '/einheit/bsp-overlays.json?einheit=1', '/objekte/1/bsp-overlays.json']) {
  test('nur lokale BSP-Bedienung persistiert ohne POST: ' + listUrl, async () => {
    const f = fixture({nurLokal: true, kann: false, listUrl}); await flush();
    f.el('lft-layer-bsp').checked = false; f.event('lft-layer-bsp', 'change');
    f.el('lft-bsp-deckkraft').value = '0.35'; f.event('lft-bsp-deckkraft', 'change');
    const neu = fixture({nurLokal: true, kann: false, listUrl, storage: f.storage}); await flush();
    assert.equal(neu.active.size, 0);
    assert.equal(neu.el('lft-bsp-deckkraft').value, 0.35);
    assert.equal(f.requests.some(r => r.options.method === 'POST'), false);
    neu.event('lft-bsp-reset');
    assert.equal(neu.active.size, 1);
    assert.equal(neu.storage.size, 0);
  });
  test('lokale BSP-Bedienung bleibt bei Storage-Ausfall bedienbar: ' + listUrl, async () => {
    const f = fixture({nurLokal: true, kann: false, listUrl, storageFail: true}); await flush();
    f.el('lft-layer-bsp').checked = false; f.event('lft-layer-bsp', 'change');
    await f.controller.reload();
    assert.equal(f.active.size, 0);
    assert.match(f.el('lft-bsp-hinweis').textContent, /nur bis zum Schließen/);
    f.el('lft-layer-bsp').checked = true;
    f.el('lft-bsp-deckkraft').value = '0.2'; f.event('lft-bsp-deckkraft', 'change');
    assert.equal([...f.active][0].options.opacity, 0.2);
    assert.equal(f.requests.some(r => r.options.method === 'POST'), false);
  });
}

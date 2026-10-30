import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const lesen = path => readFileSync(new URL('../../' + path, import.meta.url), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function script(template, modul) {
  const source = lesen(template).match(/<script>([\s\S]*?)<\/script>/)[1];
  const stack = [true];
  return source.split(/(\{%[\s\S]*?%\})/).map(part => {
    if (part.startsWith('{% if ')) {
      stack.push(stack.at(-1) && (part.includes('objekt_enabled') ? modul : true));
    } else if (part.startsWith('{% else')) {
      stack[stack.length - 1] = stack.at(-2) && !stack.at(-1);
    } else if (part.startsWith('{% endif')) {
      stack.pop();
    } else if (stack.at(-1)) return part;
    return '';
  }).join('').replace(/\{\{ 'true' if ist_verwalter else 'false' \}\}/g, 'true')
    .replace(/\{\{ objekt.id \}\}/g, '17').replace(/\{\{ request.state.csrf_token \}\}/g, 'csrf');
}

function fixture(template, modul = true, geomanGeladen = true) {
  const element = {dataset: {}}, scripts = [], events = {}, overlays = [], controls = [], maps = [];
  const map = {on(name, fn) {events[name] = fn;}, setView() {}, invalidateSize() {},
    pm: {addControls: opts => controls.push(opts)}};
  const context = {window: {EinsatzcockpitMapConfig: {addOsmTileLayer() {}},
    EcpBspOverlay: {init: (karte, opts) => overlays.push({karte, opts})}},
    document: {getElementById: id => ['objekt-karte', 'objekt-karte-tab'].includes(id) ? element : null,
      querySelectorAll: () => [], querySelector: () => null, createElement: () => ({}),
      head: {appendChild: s => scripts.push(s)}, body: {addEventListener: (name, fn) => {events[name] = fn;}}},
    L: {map: () => {maps.push(map); return map;}, PM: geomanGeladen ? {} : undefined},
    fetch: async url => ({ok: true, json: async () => url.endsWith('objekte.json')
      ? {objekt: {lat: 47, lng: 9}, eintraege: []} : {symbole: [], hydranten: []}}),
    setTimeout: fn => fn()};
  vm.createContext(context);
  vm.runInContext(lesen('app/static/js/objekt_karte.js'), context);
  context.initObjektKarte = context.window.initObjektKarte;
  return {maps, map, events, overlays, controls, scripts,
    run() {vm.runInContext(script(template, modul), context);},
    geomanLaden() {context.L.PM = {}; scripts.at(-1).onload();}};
}

for (const template of ['app/templates/objekt/_karte_tab.html', 'app/templates/objekt/karte.html']) {
  test(template + ': editierbare Karte mit Geoman und lokaler BSP-Ebene initialisiert nur einmal', async () => {
    const f = fixture(template); f.run(); f.run();
    f.events['objekt-tab-karte']?.(); f.events['objekt-tab-karte']?.();
    await flush();
    assert.equal(f.maps.length, 1);
    assert.equal(f.controls.length, 1);
    assert.equal(f.controls[0].drawPolygon, true);
    assert.equal(typeof f.events['pm:create'], 'function');
    assert.equal(typeof f.events.click, 'function');
    assert.equal(f.overlays.length, 1);
    assert.equal(f.overlays[0].karte, f.map);
    assert.equal(f.overlays[0].opts.nurLokal, true);
    assert.equal(f.overlays[0].opts.listUrl, '/objekte/17/bsp-overlays.json');
  });
  test(template + ': ohne Objektmodul keine BSP-Initialisierung', async () => {
    const f = fixture(template, false); f.run(); await flush();
    assert.equal(f.maps.length, 1);
    assert.equal(f.overlays.length, 0);
    assert.equal(f.scripts.length, 0);
  });
}

test('Karten-Tab wartet auf Geoman; konkurrierende Callbacks legen keine zweite Ebene an', async () => {
  const f = fixture('app/templates/objekt/_karte_tab.html', true, false);
  f.run(); f.run();
  assert.equal(f.maps.length, 0);
  assert.equal(f.scripts[0].src, '/static/js/leaflet-geoman.min.js');
  const callbacks = f.scripts.map(s => s.onload);
  f.geomanLaden(); callbacks.forEach(fn => fn()); await flush();
  assert.equal(f.maps.length, 1);
  assert.equal(f.overlays.length, 1);
  assert.equal(f.controls.length, 1);
});

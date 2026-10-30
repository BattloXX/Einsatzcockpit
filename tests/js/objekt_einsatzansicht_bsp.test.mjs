import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const template = readFileSync(new URL('../../app/templates/objekt/_karte_readonly.html', import.meta.url), 'utf8');
function fixture({modul = true, geladen = false} = {}) {
  const source = template.match(/<script>([\s\S]*?)<\/script>/)[1]
    .replace(/\{% if request.state.objekt_enabled %\}([\s\S]*?)(?:\{% else %\}([\s\S]*?))?\{% endif %\}/g,
      (_, ja, nein) => modul ? ja : (nein || ''))
    .replace(/\{\{ objekt.id \}\}/g, '17').replace(/\{\{ request.state.csrf_token \}\}/g, 'csrf');
  const el = {dataset: {}}, scripts = [], karten = [], overlays = [], karte = {};
  const context = {window: {}, document: {
    getElementById: id => id === 'objekt-karte-ro' ? el : null,
    createElement: () => ({}), head: {appendChild: script => scripts.push(script)}
  }};
  const initKarte = options => {karten.push(options); return karte;};
  const bsp = {init(map, options) {overlays.push({map, options});}};
  function bereit() {
    context.L = {};
    context.initObjektKarte = context.window.initObjektKarte = initKarte;
    context.window.EcpBspOverlay = bsp;
  }
  if (geladen) bereit();
  vm.createContext(context);
  return {scripts, karten, overlays, karte, context, bereit,
    run() {vm.runInContext(source, context);},
    lade() {
      const script = scripts.filter(s => s.src).at(-1);
      if (script.src.endsWith('/leaflet.min.js')) context.L = {};
      if (script.src.endsWith('/objekt_karte.js')) context.initObjektKarte = context.window.initObjektKarte = initKarte;
      if (script.src.endsWith('/bsp_overlay_layer.js')) context.window.EcpBspOverlay = bsp;
      script.onload();
    }
  };
}

test('Readonly-Karte laedt BSP nach Leaflet und Objektkarte und uebergibt die Karte', () => {
  const f = fixture(); f.run();
  assert.equal(f.scripts.at(-1).src, '/static/js/leaflet.min.js'); f.lade();
  assert.equal(f.scripts.at(-1).src, '/static/js/objekt_karte.js'); f.lade();
  assert.equal(f.scripts.at(-1).src, '/static/js/bsp_overlay_layer.js');
  assert.equal(f.karten.length, 0); f.lade();
  assert.equal(f.karten.length, 1);
  assert.equal(f.karten[0].editierbar, false);
  assert.equal(f.overlays[0].map, f.karte);
  assert.equal(f.overlays[0].options.listUrl, '/objekte/17/bsp-overlays.json');
  assert.equal(f.overlays[0].options.nurLokal, true);
});

test('Wiederholte HTMX-Initialisierung legt Karte und BSP nur einmal an', () => {
  const f = fixture({geladen: true});
  f.run(); f.run();
  assert.equal(f.karten.length, 1);
  assert.equal(f.overlays.length, 1);
  assert.equal(f.scripts.length, 0);
});

test('Konkurrierende Skript-Callbacks initialisieren dasselbe Element nur einmal', () => {
  const f = fixture();
  f.run(); f.run();
  const callbacks = f.scripts.filter(s => s.src).map(s => s.onload);
  f.bereit(); callbacks.forEach(callback => callback());
  assert.equal(f.karten.length, 1);
  assert.equal(f.overlays.length, 1);
});

test('Ohne Objektmodul wird kein BSP geladen oder initialisiert', () => {
  const f = fixture({modul: false, geladen: true}); f.run();
  assert.equal(f.karten.length, 1);
  assert.equal(f.overlays.length, 0);
  assert.equal(f.scripts.length, 0);
});

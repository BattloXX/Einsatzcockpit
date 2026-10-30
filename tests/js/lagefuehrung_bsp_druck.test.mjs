import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/static/js/lagefuehrung.js', import.meta.url), 'utf8');
const block = source.slice(source.indexOf('    var DRUCK_LAYER_CHECKBOX_IDS'), source.indexOf('    var btnReplay ='));
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture(saved = null) {
  const elements = new Map(), storage = new Map(), opened = [];
  if (saved) storage.set('lft_druck_settings', JSON.stringify(saved));
  function el(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', checked: false, dataset: {}, textContent: '', events: {}, children: [],
      addEventListener(name, fn) { this.events[name] = fn; },
      dispatchEvent(e) { this.events[e.type]?.call(this); },
      appendChild(child) { this.children.push(child); },
      showModal() { this.open = true; }, close() { this.open = false; }
    });
    return elements.get(id);
  }
  el('lft-druck-bsp-modus').value = 'MAP_ONLY';
  el('lft-druck-bsp-deckkraft').value = '0.6';
  const context = {
    document: {getElementById: el, createElement: () => ({})},
    localStorage: {getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v)},
    baselayerSelect: el('baselayer'), apiBase: '/einsatz/1/lagefuehrung',
    karte: {getBounds: () => ({getSouth: () => 47, getWest: () => 14, getNorth: () => 48, getEast: () => 15})},
    window: {open: url => opened.push(url)}, Event: class {constructor(type) {this.type = type;}},
    fetchJson: async () => [{id: 7, name: 'Halle', objekt_name: 'Betrieb'}]
  };
  vm.runInNewContext(block, context);
  return {el, storage, opened, event: (id, name = 'click') => el(id).events[name].call(el(id))};
}

test('Druckdialog bleibt standardmaessig MAP_ONLY und oeffnet nur auf Klick', async () => {
  const f = fixture();
  f.el('lft-layer-bsp').checked = true;
  f.event('lft-tool-druck');
  await flush();
  assert.equal(f.opened.length, 0);
  assert.equal(f.el('lft-druck-bsp-modus').value, 'MAP_ONLY');
  assert.equal(f.el('lft-druck-bsp-status').textContent, 'BSP-Overlay wird gedruckt: nein');
});

test('Bildschirm-OFF ueberschreibt gespeicherte Druckparameter nie', async () => {
  const f = fixture({bsp_modus: 'MAP_WITH_BSP', bsp_ids: '7', bsp_deckkraft: 0.8});
  f.el('lft-layer-bsp').checked = false;
  f.event('lft-tool-druck');
  await flush();
  assert.equal(f.el('lft-druck-bsp-status').textContent, 'BSP-Overlay wird gedruckt: ja');
  f.event('lft-druck-vorschau');
  const url = new URL(f.opened[0], 'https://example.test');
  assert.equal(url.searchParams.get('bsp_modus'), 'MAP_WITH_BSP');
  assert.equal(url.searchParams.get('bsp_ids'), '7');
  assert.equal(url.searchParams.get('bsp_deckkraft'), '0.8');
  assert.equal(JSON.parse(f.storage.get('lft_druck_settings')).bsp_modus, 'MAP_WITH_BSP');
});

test('Modus ist aenderbar und getrennte Druckentscheidung bleibt gespeichert', async () => {
  const f = fixture({bsp_modus: 'MAP_WITH_BSP'});
  f.event('lft-tool-druck');
  await flush();
  f.el('lft-druck-bsp-modus').value = 'MAP_ONLY';
  f.event('lft-druck-bsp-modus', 'change');
  f.event('lft-druck-vorschau');
  assert.equal(f.el('lft-druck-bsp-status').textContent, 'BSP-Overlay wird gedruckt: nein');
  assert.equal(JSON.parse(f.storage.get('lft_druck_settings')).bsp_modus, 'MAP_ONLY');
});

test('Nicht mehr verfuegbare gespeicherte Auswahl bleibt explizit angefordert', async () => {
  const f = fixture({bsp_modus: 'BSP_EXCERPT_ONLY', bsp_ids: '99'});
  f.event('lft-tool-druck');
  await flush();
  assert.equal(f.el('lft-druck-bsp-ids').value, '99');
  assert.ok(f.el('lft-druck-bsp-ids').children.some(o => o.value === '99'));
  f.event('lft-druck-vorschau');
  assert.equal(new URL(f.opened[0], 'https://example.test').searchParams.get('bsp_ids'), '99');
});

test('Alle freigegebenen fixiert die Auswahl der Vorschau fuer Veraltungspruefung', async () => {
  const f = fixture({bsp_modus: 'MAP_WITH_BSP'});
  f.event('lft-tool-druck');
  await flush();
  f.event('lft-druck-vorschau');
  assert.equal(new URL(f.opened[0], 'https://example.test').searchParams.get('bsp_ids'), '7');
  assert.equal(JSON.parse(f.storage.get('lft_druck_settings')).bsp_ids, '');
});

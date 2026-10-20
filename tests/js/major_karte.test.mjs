import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/templates/incident_major/karte.html', import.meta.url), 'utf8');

test('GSL map scripts remain valid JavaScript after template rendering', () => {
  const scripts = [...source.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)];
  for (const [, script] of scripts) {
    new vm.Script(script.replace(/\{%[\s\S]*?%\}/g, '').replace(/\{\{[\s\S]*?\}\}/g, 'null'));
  }
});

test('cross markers retain status frames, fallback text, click and tooltip behavior', () => {
  const markers = [];
  const opened = [];
  const context = {
    colorMode: 'priority', CROSS_TYPE_TAKT: {},
    CROSS_MARKERS: [
      {id: 1, marker_type: 'unterfuehrung_geflutet', lat: 47, lng: 9, type_icon_name: 'flood', status: 'unbestaetigt', status_color: '#6b7280', title: '<img>', address_line: '<addr>'},
      {id: 2, marker_type: 'mure', lat: 47, lng: 9, type_icon_name: null, type_icon: '🪨', status: 'behoben', status_color: '#22c55e'},
    ],
    document: {getElementById: () => ({checked: false})},
    crossMarkerGroup: {clearLayers() {}, addLayer: m => markers.push(m)},
    ecpMarkerIcon: (name, options) => ({name, options}),
    L: {divIcon: options => ({options}), marker: (coords, options) => ({
      ...options, coords,
      bindTooltip(html) { this.tooltip = html; },
      on(event, handler) { this[event] = handler; },
    })},
    openCrossPanel: id => opened.push(id),
  };
  vm.createContext(context);
  const escaping = source.slice(source.indexOf('  function escapeMapText'), source.indexOf('  function buildPopup'));
  const rendering = source.slice(source.indexOf('  function renderCrossMarkers'), source.indexOf('  window.toggleCrossMarkers'));
  vm.runInContext(escaping + rendering + '\nrenderCrossMarkers();', context);
  assert.equal(markers[0].icon.name, 'flood');
  assert.equal(markers[0].icon.options.border, '2px dashed rgba(255,255,255,.7)');
  assert.equal(markers[1].icon.name, null);
  assert.equal(markers[1].icon.options.text, '🪨');
  assert.equal(markers[1].icon.options.color, '#22c55e');
  assert.equal(markers[1].icon.options.border, '2px solid #fff');
  assert.equal(markers[1].icon.options.size, 28);
  assert.ok(markers[0].tooltip.includes('&lt;img&gt;'));
  assert.ok(markers[0].tooltip.includes('&lt;addr&gt;'));
  markers[0].click();
  assert.deepEqual(opened, [1]);
  markers.length = 0;
  context.document.getElementById = () => ({checked: true});
  vm.runInContext('renderCrossMarkers();', context);
  assert.equal(markers.length, 1);
  context.colorMode = 'taktisch';
  context.CROSS_TYPE_TAKT = {unterfuehrung_geflutet: {kind: 'gefahr', label: '!'}};
  const symbols = source.slice(source.indexOf('  function taktSymbolHtml'), source.indexOf('  // ── PR4:'));
  vm.runInContext(symbols, context);
  markers.length = 0;
  vm.runInContext('renderCrossMarkers();', context);
  assert.equal(markers.length, 1);
  assert.ok(markers[0].icon.options.html.includes(context.taktSymbolHtml('gefahr', '#d11', '!')));
  assert.ok(markers[0].icon.options.html.includes('box-shadow:0 0 0 2px #6b7280'));
  assert.equal(markers[0].icon.options.iconAnchor.join(','), '15,15');
});

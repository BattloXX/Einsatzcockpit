import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/static/js/weather_radar.js', import.meta.url), 'utf8');

function radarFixture(fetch) {
  const elements = new Map();
  const timers = new Map();
  const layers = new Set();
  const root = {querySelector(selector) {
    if (!elements.has(selector)) elements.set(selector, {style: {}, events: {}, addEventListener(name, fn) { this.events[name] = fn; }});
    return elements.get(selector);
  }};
  let timer = 0;
  const context = {window: {}, fetch, document: {}, ecpIcon: name => `<svg>${name}</svg>`,
    setInterval(fn, delay) { timers.set(++timer, {fn, delay}); return timer; },
    clearInterval(id) { timers.delete(id); },
    L: {tileLayer(url, options) { return {url, options, addTo() { layers.add(this); return this; }, setOpacity(v) { this.opacity = v; }}; }}
  };
  vm.runInNewContext(source, context);
  const radar = context.window.WeatherRadar.create({removeLayer(layer) { layers.delete(layer); }}, root);
  return {radar, elements, timers, layers, el: id => root.querySelector(`[data-radar-id="${id}"]`)};
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('shared radar animates, pauses, scrubs, resumes and releases layers/timers', async () => {
  const f = radarFixture(async () => ({ok: true, json: async () => ({host: 'https://example.test',
    radar: {past: [{time: 1, path: '/past'}], nowcast: [{time: 2, path: '/future'}]}})}));
  await flush();
  assert.equal(f.el('radar-scrubber').max, 1);
  assert.match(f.el('radar-time-label').innerHTML, /Prognose/);
  assert.equal(f.timers.size, 2);
  [...f.timers.values()].find(t => t.delay === 650).fn();
  assert.equal(f.el('radar-scrubber').value, 0);
  f.el('radar-scrubber').events.touchstart();
  assert.equal(f.timers.size, 1);
  f.el('radar-scrubber').value = '1';
  f.el('radar-scrubber').events.input.call(f.el('radar-scrubber'));
  assert.match(f.el('radar-time-label').innerHTML, /Prognose/);
  f.el('radar-play-btn').events.click();
  assert.equal(f.timers.size, 2);
  f.radar.destroy();
  assert.equal(f.layers.size, 0);
  assert.equal(f.timers.size, 0);
});

test('late radar response cannot resurrect a deactivated overlay', async () => {
  let resolve;
  const f = radarFixture(() => new Promise(r => { resolve = r; }));
  f.radar.destroy();
  resolve({ok: true, json: async () => ({radar: {past: [{time: 1, path: '/past'}]}})});
  await flush();
  assert.equal(f.layers.size, 0);
  assert.equal(f.timers.size, 0);
});

test('weather layer keeps the toggle and independent overlays with native GeoJSON geometries', () => {
  const layer = readFileSync(new URL('../../app/static/js/weather_layer.js', import.meta.url), 'utf8');
  new vm.Script(layer);
  assert.match(layer, /weather-layer-btn/);
  assert.match(layer, /L.control.layers/);
  assert.match(layer, /L.geoJSON/);
  assert.match(layer, /WeatherRadar.create/);
});

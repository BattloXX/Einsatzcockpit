import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/static/js/road_closure_editor.js', import.meta.url), 'utf8');
const line = { type: 'LineString', coordinates: [[9.74, 47.46], [9.75, 47.47]] };

function setup(initial = [{ street: 'Erster Weg' }, { street: 'Zweiter Weg' }]) {
  const events = [];
  const listeners = {};
  const window = {
    dispatchEvent(event) { events.push(event); listeners[event.type]?.(event); },
    addEventListener(name, callback) { listeners[name] = callback; },
  };
  let initMap;
  const document = {
    readyState: 'loading', addEventListener(name, callback) { initMap = callback; },
    getElementById(id) { return id === 'sperren-initial-sections' ? { textContent: JSON.stringify(initial) } : { value: 'Wolfurt' }; },
  };
  const context = vm.createContext({ window, document, Map, JSON, setTimeout() {},
    CustomEvent: class { constructor(type, { detail } = {}) { this.type = type; this.detail = detail; } },
  });
  vm.runInContext(source, context);
  const model = window.roadClosureSections();
  model.$watch = () => {};
  model.$el = { dataset: {}, querySelector: () => ({ value: 'csrf-test' }) };
  model.init();
  return { model, context, events, window, initMap };
}

test('Abschnittsliste serialisiert Nutzdaten und behält nach Entfernen einen aktiven Abschnitt', () => {
  const { model } = setup();
  model.add();
  assert.equal(model.sections.length, 3);
  const active = model.active;
  model.remove(active);
  assert.equal(model.sections.length, 2);
  assert.equal(model.active, model.sections[0]._key);
  model.remove(model.sections[0]._key);
  model.remove(model.sections[0]._key);
  assert.equal(model.sections.length, 1);
  assert.equal(model.payload()[0]._key, undefined);
  assert.equal(model.payload()[0].busy, undefined);
});

test('Geometrieänderung und Entfernung betreffen nur den bezeichneten Abschnitt', () => {
  const { model } = setup();
  const [first, second] = model.sections;
  model.setGeometry({ key: second._key, geometry: line });
  assert.equal(first.geometry_geojson, null);
  assert.deepEqual(second.geometry_geojson, line);
  assert.equal(second.geometry_checked, true);
  assert.equal(second.geometry_quality, 'manuell');
  model.setGeometry({ key: second._key, geometry: null });
  assert.equal(second.geometry_status, 'missing');
  assert.equal(second.geometry_checked, false);
});

test('Adressauflösung bleibt beim angefragten Abschnitt auch nach Wechsel des aktiven Abschnitts', async () => {
  const { model, context } = setup();
  let finish;
  let request;
  context.fetch = (url, options) => {
    request = { url, options };
    return new Promise(resolve => { finish = resolve; });
  };
  const first = model.sections[0];
  const pending = model.resolve(first);
  model.active = model.sections[1]._key;
  finish({ ok: true, json: async () => ({ geometry: line, quality: 'hoch', endpoints: [], hinweise: ['Eindeutig'] }) });
  await pending;
  assert.equal(request.url, '/strassensperren/abschnitt');
  assert.equal(JSON.parse(request.options.body).street, 'Erster Weg');
  assert.equal(request.options.headers['X-CSRF-Token'], 'csrf-test');
  assert.equal(first.geometry_checked, true);
  assert.equal(first.geometry_status, 'ok');
  assert.equal(model.sections[1].geometry_geojson, null);
  assert.equal(first.busy, false);
});

test('Geoman ignoriert inaktive Layer und Zeichnen ersetzt nur den aktiven Abschnitt', () => {
  const { context, window, initMap } = setup();
  const mapEvents = {};
  const groups = [];
  const map = {
    setView() { return this; }, on(name, callback) { mapEvents[name] = callback; }, removeLayer() {},
    fitBounds() {}, pm: { addControls() {} },
  };
  window.EinsatzcockpitMapConfig = { addOsmTileLayer() {} };
  context.L = {
    map() { return map; },
    geoJSON(geometry, options) {
      const callbacks = {};
      const child = { geometry, callbacks, bindTooltip() {}, on(name, callback) { callbacks[name] = callback; },
        toGeoJSON() { return { geometry: this.geometry }; } };
      const group = {
        options, geometry, child,
        addTo() { return this; },
        eachLayer(callback) { if (this.child) callback(this.child); },
        removeLayer() { this.child = null; },
      };
      groups.push(group);
      return group;
    },
    featureGroup() { return { getBounds() { return { isValid: () => true }; } }; },
  };
  initMap();
  window.dispatchEvent({ type: 'sperren-sections', detail: { active: 2, sections: [
    { _key: 1, geometry_geojson: line }, { _key: 2, geometry_geojson: line },
  ] } });
  assert.equal(groups[0].options.pmIgnore, true);
  assert.equal(groups[1].options.pmIgnore, false);
  assert.notEqual(groups[0].options.style.color, groups[1].options.style.color);
  let saved;
  window.addEventListener('sperren-geometry', e => { saved = e.detail; });
  mapEvents['pm:create']({ layer: { toGeoJSON: () => ({ geometry: line }) } });
  assert.equal(saved.key, 2);
  assert.deepEqual(saved.geometry, line);
  groups[1].child.geometry = { type: 'Point', coordinates: [9.76, 47.48] };
  groups[1].child.callbacks['pm:edit pm:dragend']();
  assert.equal(saved.geometry.type, 'Point');
  assert.equal(saved.key, 2);
  groups[1].child.callbacks['pm:remove']();
  assert.equal(saved.geometry, null);
  assert.equal(groups[0].child.geometry, line);
});

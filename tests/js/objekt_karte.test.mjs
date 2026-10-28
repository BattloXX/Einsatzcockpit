import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const quelle = readFileSync(new URL("../../app/static/js/objekt_karte.js", import.meta.url), "utf8");
const fertig = () => new Promise((resolve) => setImmediate(resolve));

function umgebung(eintraege = [], editierbar = true) {
  const marker = [], geometrien = [], anfragen = [], meldungen = [];
  let controls;
  function layer() {
    return {
      events: {},
      on(namen, callback) { namen.split(" ").forEach((name) => { this.events[name] = callback; }); return this; },
      addTo() { return this; },
      bindPopup(inhalt) { this.popup = inhalt; return this; },
      setIcon(icon) { this.icon = icon; },
    };
  }
  const karte = layer();
  karte.setView = () => {};
  karte.pm = { addControls: (optionen) => { controls = optionen; } };
  const context = {
    window: { EinsatzcockpitMapConfig: { addOsmTileLayer() {} } },
    document: { getElementById: () => null },
    alert: (text) => meldungen.push(text),
    L: {
      map: () => karte,
      divIcon: (options) => options,
      marker: (position, options) => { const m = Object.assign(layer(), options, { position }); marker.push(m); return m; },
      geoJSON: (geometry) => {
        const kind = layer();
        kind.geometry = geometry;
        kind.toGeoJSON = () => ({ type: "Feature", geometry: kind.geometry });
        geometrien.push(kind);
        return Object.assign(layer(), { eachLayer: (callback) => callback(kind) });
      },
    },
    fetch: async (url, options) => {
      if (options) {
        const daten = JSON.parse(options.body);
        anfragen.push({ url, daten });
        return { ok: true, json: async () => daten };
      }
      const daten = url.endsWith("karten-symbole.json") ? { symbole: [] }
        : url.endsWith("hydranten.json") ? { hydranten: [] }
        : { objekt: { lat: 47, lng: 9 }, eintraege };
      return { ok: true, json: async () => daten };
    },
  };
  vm.runInNewContext(quelle, context);
  return { window: context.window, marker, geometrien, anfragen, meldungen, controls: () => controls };
}

test("alle Symbolstile drehen innen, die Beschriftung bleibt ausserhalb des Drehbereichs", () => {
  const { window } = umgebung();
  window.setObjektSymbole([{ code: "bild", stil: "bild", bild: "/symbol.png" }]);
  for (const typ of ["fsd", "hauptzugang", "nebenzugang", "gefahr_ex", "hydrant_ueberflur", "sammelplatz", "feuerloescher", "bild"]) {
    const html = window.objektSymbolHtml(typ, "Zugang <West>", -15);
    assert.match(html, /^<div class="oks-wrap"><div style="transform: rotate\(345deg\)">/);
    assert.match(html, /<\/div><\/div><div class="oks__label">Zugang &lt;West&gt;/);
    assert.equal((html.match(/transform:/g) || []).length, 1);
  }
  assert.match(window.objektSymbolHtml("bmz"), /rotate\(0deg\)/);
});

test("Punkt und GeoJSON-Punkt laden ihre Rotation; Popup speichert Knopf, Slider und Reset", async () => {
  const env = umgebung([
    { id: 1, typ: "hauptzugang", lat: 47, lng: 9, rotation: 345 },
    { id: 2, typ: "bmz", geometry: { type: "Point", coordinates: [9, 47] }, rotation: 90 },
  ]);
  env.window.initObjektKarte({ elementId: "karte", objektId: 7, csrfToken: "csrf", editierbar: true });
  await fertig();
  assert.match(env.marker[0].icon.html, /rotate\(345deg\)/);
  assert.match(env.marker[1].icon.html, /rotate\(90deg\)/);
  const slider = {}, anzeige = {}, reset = {};
  const buttons = [-15, 15, 90].map((wert) => ({ dataset: { drehung: String(wert) } }));
  const popup = {
    querySelector: (selector) => ({ "[data-drehwinkel]": slider, "[data-winkel]": anzeige, "[data-reset]": reset })[selector],
    querySelectorAll: (selector) => selector === "[data-drehung]" ? buttons : [...buttons, reset, slider],
  };
  const marker = env.marker[0];
  marker.events.popupopen({ popup: { getElement: () => popup } });
  for (const [index, erwartet] of [[1, 0], [0, 345], [2, 75]]) {
    buttons[index].onclick();
    await fertig();
    assert.equal(env.anfragen.at(-1).daten.rotation, erwartet);
    assert.match(marker.icon.html, new RegExp(`rotate\\(${erwartet}deg\\)`));
  }
  slider.value = "123";
  const anzahl = env.anfragen.length;
  slider.oninput();
  assert.match(marker.icon.html, /rotate\(123deg\)/);
  assert.equal(env.anfragen.length, anzahl);
  slider.onchange();
  await fertig();
  assert.equal(env.anfragen.at(-1).daten.rotation, 123);
  reset.onclick();
  await fertig();
  assert.equal(env.anfragen.at(-1).daten.rotation, 0);
  assert.deepEqual(env.meldungen, []);
});

test("Geoman-Drehen und Editieren speichern die aktuelle Geometrie", async () => {
  const vorher = { type: "LineString", coordinates: [[9, 47], [9.1, 47]] };
  const nachher = { type: "LineString", coordinates: [[9.05, 46.95], [9.05, 47.05]] };
  const env = umgebung([{ id: 3, typ: "geometrie", geometry: vorher }]);
  env.window.initObjektKarte({ elementId: "karte", objektId: 7, csrfToken: "csrf", editierbar: true });
  await fertig();
  assert.equal(env.controls().rotateMode, true);
  const geometrie = env.geometrien[0];
  geometrie.geometry = nachher;
  for (const event of ["pm:rotateend", "pm:edit"]) {
    geometrie.events[event]();
    await fertig();
    assert.deepEqual(env.anfragen.at(-1), { url: "/objekte/7/karte/objekte/3", daten: { geometry: nachher } });
  }
});

test("readonly rendert den Winkel ohne Drehsteuerung", async () => {
  const env = umgebung([{ id: 1, typ: "fsd", lat: 47, lng: 9, rotation: 135 }]);
  env.window.initObjektKarte({ elementId: "karte", objektId: 7, editierbar: false });
  await fertig();
  assert.match(env.marker[0].icon.html, /rotate\(135deg\)/);
  assert.doesNotMatch(env.marker[0].popup(), /data-drehung/);
  assert.equal(env.marker[0].events.popupopen, undefined);
  assert.equal(env.controls(), undefined);
});

test("KI-Texte und Katalogtexte werden vollständig escaped", () => {
  const { window } = umgebung();
  window.setObjektSymbole([{ code: "ki", text: '<script>alert("x")</script>', stil: "box" }]);
  const html = window.objektSymbolHtml("ki", '<img src=x onerror="alert(1)"> &lt;script&gt;');
  assert.doesNotMatch(html, /<script|<img/);
  assert.match(html, /&lt;script&gt;alert\(&quot;x&quot;\)/);
  assert.match(html, /&amp;lt;script&amp;gt;/);
});

test("Review-Filter und Prüffähigkeit trennen Status, Position und Freigabe", () => {
  const { window } = umgebung();
  const { prueffaehig, filterPasst, warntext } = window.objektKarteReview;
  const v = { freigabe_status: "offen", position_status: "vorgeschlagen", lat: 47, lng: 9,
    operation: "hinzufuegen", warnungen: [{ code: "moegliche_dublette" }] };
  assert.equal(prueffaehig(v), true);
  for (const status of ["pruefung_erforderlich", "veraltet"]) {
    assert.equal(prueffaehig({ ...v, position_status: status }), false);
  }
  assert.equal(prueffaehig({ ...v, freigabe_status: "angenommen" }), false);
  assert.equal(prueffaehig({ ...v, lat: null, lng: null }), false);
  assert.equal(prueffaehig({ ...v, lat: null, lng: null,
    geometry_json: '{"type":"Point","coordinates":[9,47]}' }), true);
  for (const f of ["alle", "neu", "dubletten"]) assert.equal(filterPasst(v, f), true);
  for (const f of ["aenderungen", "unklar", "veraltet"]) assert.equal(filterPasst(v, f), false);
  assert.equal(filterPasst({ ...v, operation: "entfernen" }, "aenderungen"), true);
  assert.equal(filterPasst({ ...v, position_status: "veraltet" }, "veraltet"), true);
  assert.match(warntext({ code: "neuer_code<script>" }), /neuer_code<script>/);
});

test("Marker-Popups zeigen Verwaltern Legacy, bestätigt und veraltet", async () => {
  const env = umgebung([
    { id: 1, typ: "tor", lat: 47, lng: 9 },
    { id: 2, typ: "tor", lat: 47, lng: 9, position_status: "bestaetigt" },
    { id: 3, typ: "tor", lat: 47, lng: 9, position_status: "veraltet" },
  ]);
  env.window.initObjektKarte({ elementId: "karte", objektId: 7, editierbar: true });
  await fertig();
  assert.match(env.marker[0].popup(), /Legacy \/ ungeprüft/);
  assert.match(env.marker[1].popup(), /Bestätigt/);
  assert.match(env.marker[2].popup(), /Veraltet/);
});

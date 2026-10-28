import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const context = { window: {} };
vm.createContext(context);
vm.runInContext(readFileSync(new URL("../../app/static/js/objekt_karte.js", import.meta.url), "utf8"), context);
const normiert = context.window.objektKarteGeoref.planNormiert;

test("Nicht-quadratischer Plan: Ursprung oben links, unabhaengige Achsen", () => {
  for (const [lng, lat, x, y] of [[0, 0, 0, 0], [2000, -1000, 1, 1], [500, -750, 0.25, 0.75]]) {
    const p = normiert({ lng, lat }, 2000, 1000);
    assert.equal(p.x, x);
    assert.equal(p.y + 0, y);
  }
});

test("90 Grad gedrehte Seite verwendet gedrehte Bildmasse ohne zweite Rotation", () => {
  // Original (x=.25,y=.75) wird im gedrehten Rendering (x=.25,y=.25).
  const p = normiert({ lng: 250, lat: -500 }, 1000, 2000);
  assert.equal(p.x, 0.25);
  assert.equal(p.y, 0.25);
});

const fertig = () => new Promise((resolve) => setImmediate(resolve));

class Element {
  constructor(tag) { this.tagName = tag; this.childNodes = []; this.dataset = {}; this.events = {}; this.text = ""; }
  appendChild(n) { this.childNodes.push(n); return n; }
  set textContent(s) { this.text = String(s); this.childNodes = []; }
  get textContent() { return this.text + this.childNodes.map((n) => n.textContent).join(""); }
  set innerHTML(_) { throw new Error("Rohdaten-innerHTML im Editor"); }
  replaceChildren() { this.childNodes = []; this.text = ""; }
  setAttribute(k, v) { this[k] = v; }
  addEventListener(k, fn) { this.events[k] = fn; }
  showModal() { this.open = true; }
  close() { this.open = false; this.events.close(); }
  remove() { this.removed = true; }
  querySelector(s) {
    const id = s.match(/data-punkt="(\d+)"/)[1];
    return this.childNodes.find((n) => n.dataset.punkt === id);
  }
  alle(tag) { return this.childNodes.flatMap((n) => [...(n.tagName === tag ? [n] : []), ...n.alle(tag)]); }
}

function editorUmgebung(status = "gueltig") {
  const root = new Element("section"), maps = [], markers = [], posts = [], bilder = [];
  const gift = '<img src=x onerror="alert(1)">';
  const geo = { revision: 2, modell: "aehnlichkeit", status, ablehngrund: gift,
    referenzpunkte: [{ id: "extern", x: 0.25, y: 0.75, lat: 47, lng: 9, quelle: "manuell", beschreibung: gift }] };
  function layer(pos, opts) {
    return { events: {}, pos, opts, on(k, fn) { this.events[k] = fn; return this; },
      addTo(map) { this.map = map; return this; }, remove() { this.removed = true; },
      getLatLng() { return this.pos; }, bindTooltip(node) { this.tooltip = node; return this; } };
  }
  const ctx = {
    window: { EinsatzcockpitMapConfig: { addOsmTileLayer: () => layer() } },
    document: { createElement: (tag) => new Element(tag) },
    L: { CRS: { Simple: {} },
      map(node, opts) {
        const m = layer(); m.node = node; m.opts = opts;
        m.setView = () => m; m.fitBounds = (b) => { m.bounds = b; };
        m.hasLayer = () => false; maps.push(m); return m;
      },
      imageOverlay(url, bounds) { bilder.push({ url, bounds }); return layer(); },
      marker(pos, opts) { const m = layer(pos, opts); markers.push(m); return m; },
      circleMarker(pos, opts) { const m = layer(pos, opts); markers.push(m); return m; },
      divIcon: (o) => o, tileLayer: () => layer(), control: { layers: () => layer() }
    }
  };
  vm.createContext(ctx);
  const src = readFileSync(new URL("../../app/static/js/objekt_karte.js", import.meta.url), "utf8")
    .replace("  function initReview(", "  window.testEditor = georefEditor;\n  function initReview(");
  vm.runInContext(src, ctx);
  const get = async (path) => path.startsWith("/georef.json") ?
    { georeferenz: geo, bildgroesse: [2000, 1000], rotation: 0, seitenbild_url: "/plan",
      schwellen: { rmse_m: 2, max_fehler_m: 4 } } :
    { gebaeude: [{ osm_id: 5, ecken: [{ nummer: 1, lat: 47.1, lng: 9.1 }] }] };
  const post = (path, body) => new Promise((resolve, reject) => {
    posts.push({ path, body: JSON.parse(JSON.stringify(body)), resolve, reject });
  });
  let aktualisiert = 0;
  ctx.window.testEditor(root, { quelle_seite: 1, quelle_dokument_id: 3 },
    { getCenter: () => [47, 9], getZoom: () => 18 }, get, post, async () => { aktualisiert++; });
  return { root, maps, posts, markers, bilder, gift, aktualisiert: () => aktualisiert };
}

function antwort(status = "gueltig") {
  return { georeferenz: { status, rmse_m: 0.1, max_fehler_m: 0.2,
    residuen: [{ id: 1, fehler_m: 0.2 }], ablehngrund: status === "gueltig" ? null : "Zu wenige Punkte" } };
}

test("Editor escaped Beschreibungen, prueft ohne Speichern und ignoriert alte Antworten", async () => {
  const e = editorUmgebung(); await fertig();
  const dialog = e.root.childNodes[0];
  assert.ok(dialog.textContent.includes(e.gift));
  assert.equal(dialog.alle("img").length, 0);
  assert.equal(e.bilder[0].bounds[0][0], -1000);
  assert.equal(e.posts[0].body.pruefen, true);
  const speichern = dialog.alle("button").find((b) => b.textContent === "Speichern");
  assert.equal(speichern.disabled, true);
  const select = dialog.alle("select")[0];
  select.value = "affin"; select.onchange();
  e.posts[1].resolve(antwort()); await fertig();
  assert.equal(speichern.disabled, false);
  e.posts[0].resolve(antwort("abgelehnt")); await fertig();
  assert.equal(speichern.disabled, false);
  speichern.onclick();
  assert.equal(e.posts[2].body.pruefen, false);
  assert.equal(e.posts[2].body.expected_revision, 2);
  assert.equal(e.posts[2].body.expected_rotation, 0);
  e.posts[2].resolve({}); await fertig();
  assert.equal(e.aktualisiert(), 1);
  assert.equal(dialog.open, false);
  assert.ok(e.maps.every((m) => m.removed));
});

test("Planpunkt wird normiert, OSM-Ecke rastet ein, Loeschen prueft erneut", async () => {
  const e = editorUmgebung(); await fertig();
  const dialog = e.root.childNodes[0];
  e.maps[0].events.click({ latlng: { lng: 500, lat: -750 } });
  const ecke = e.markers.find((m) => m.tooltip);
  ecke.events.click();
  const body = e.posts.at(-1).body;
  assert.equal(body.punkte[1].x, 0.25);
  assert.equal(body.punkte[1].y, 0.75);
  assert.equal(body.punkte[1].lat, 47.1);
  assert.equal(body.punkte[1].quelle, "osm_gebaeude");
  dialog.alle("button").find((b) => b.textContent === "Paar löschen").onclick();
  assert.equal(e.posts.at(-1).body.punkte.length, 1);
  e.posts.at(-1).resolve(antwort("abgelehnt")); await fertig();
  assert.equal(dialog.alle("button").find((b) => b.textContent === "Speichern").disabled, true);
});

test("Veraltete Punkte werden nach Drehung nicht uebernommen", async () => {
  const e = editorUmgebung("veraltet"); await fertig();
  assert.equal(e.posts.length, 0);
  assert.ok(e.root.textContent.includes("Bildkoordinaten neu erfassen"));
  assert.equal(e.root.alle("button").filter((b) => b.textContent === "Paar löschen").length, 0);
});

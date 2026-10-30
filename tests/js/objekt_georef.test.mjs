import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const context = { window: {} };
vm.createContext(context);
vm.runInContext(readFileSync(new URL("../../app/static/js/objekt_georef_editor.js", import.meta.url), "utf8"), context);
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
  constructor(tag) { this.tagName = tag; this.childNodes = []; this.dataset = {}; this.events = {}; this.text = ""; this.style = {}; }
  appendChild(n) { this.childNodes.push(n); return n; }
  set textContent(s) { this.text = String(s); this.childNodes = []; }
  get textContent() { return this.text + this.childNodes.map((n) => n.textContent).join(""); }
  set innerHTML(_) { throw new Error("Rohdaten-innerHTML im Editor"); }
  replaceChildren() { this.childNodes = []; this.text = ""; }
  setAttribute(k, v) { this[k] = v; }
  get classList() {
    const el = this;
    return { toggle(name, an) {
      const namen = (el.className || "").split(/\s+/).filter((n) => n && n !== name);
      if (an) { namen.push(name); }
      el.className = namen.join(" ");
    } };
  }
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

function editorUmgebung(status = "gueltig", seitenModus = false) {
  const root = new Element("section"), maps = [], markers = [], posts = [], bilder = [], overlays = [], tiles = [], anfragen = [];
  const gift = '<img src=x onerror="alert(1)">';
  const geo = { revision: 2, modell: "aehnlichkeit", status, ablehngrund: gift,
    referenzpunkte: [{ id: "extern", x: 0.25, y: 0.75, lat: 47, lng: 9, quelle: "manuell", beschreibung: gift }] };
  function layer(pos, opts) {
    return { events: {}, pos, opts, on(k, fn, context) { k.split(" ").forEach((name) => { this.events[name] = context ? fn.bind(context) : fn; }); return this; },
      off(k) { k.split(" ").forEach((name) => { delete this.events[name]; }); },
      addTo(map) { this.map = map; return this; }, remove() { this.removed = true; },
      getLatLng() { return this.pos; }, bindTooltip(node) { this.tooltip = node; return this; } };
  }
  const ctx = {
    window: { EinsatzcockpitMapConfig: { addOsmTileLayer: () => layer() } },
    document: { createElement: (tag) => new Element(tag), events: {},
      addEventListener(k, fn) { this.events[k] = fn; }, removeEventListener(k) { delete this.events[k]; } },
    L: { CRS: { Simple: {} },
      map(node, opts) {
        const m = layer(); m.node = node; m.opts = opts;
        m.setView = () => m; m.fitBounds = (b) => { m.bounds = b; };
        m.pane = new Element("div"); m.getPane = () => m.pane;
        m.latLngToLayerPoint = ([lat, lng]) => ({ x: lng * (m.scale || 1), y: -lat * (m.scale || 1) });
        m.hasLayer = (l) => l.map === m && !l.removed; maps.push(m); return m;
      },
      imageOverlay(url, bounds) { bilder.push({ url, bounds }); return layer(); },
      marker(pos, opts) { const m = layer(pos, opts); markers.push(m); return m; },
      circleMarker(pos, opts) { const m = layer(pos, opts); markers.push(m); return m; },
      Layer: { extend(methods) {
        return class {
          constructor() { Object.assign(this, methods); overlays.push(this); }
          addTo(map) { this._map = map; this.onAdd(map); return this; }
          remove() { this.onRemove(this._map); this.removed = true; }
        };
      } },
      divIcon: (o) => o, tileLayer: () => { const t = layer(); tiles.push(t); return t; }, control: { layers: () => layer() }
    }
  };
  vm.createContext(ctx);
  const src = readFileSync(new URL("../../app/static/js/objekt_karte.js", import.meta.url), "utf8")
    .replace("  function initReview(", "  window.testEditor = georefEditor;\n  function initReview(");
  vm.runInContext(readFileSync(new URL("../../app/static/js/objekt_georef_editor.js", import.meta.url), "utf8"), ctx);
  vm.runInContext(src, ctx);
  const get = async (path) => path.startsWith("/georef.json") ?
    { georeferenz: geo, bildgroesse: [2000, 1000], rotation: 0, seitenbild_url: "/plan",
      schwellen: { rmse_m: 2, max_fehler_m: 4 } } :
    { gebaeude: [{ osm_id: 5, ecken: [{ nummer: 1, lat: 47.1, lng: 9.1 }] }] };
  const post = (path, body) => new Promise((resolve, reject) => {
    posts.push({ path, body: JSON.parse(JSON.stringify(body)), resolve, reject });
  });
  let aktualisiert = 0;
  if (seitenModus) {
    root.dataset = { basisUrl: "/objekte/7/karte", dokumentId: "3", seite: "1", csrf: "csrf-token",
      lat: "47", lng: "9", zurueckUrl: "/zurueck" };
    ctx.document.getElementById = (id) => id === "georef-editor" ? root : null;
    ctx.fetch = async (url, options) => {
      anfragen.push({ url, options });
      const path = url.replace(root.dataset.basisUrl, "");
      const data = options.method === "POST" ? await post(path, JSON.parse(options.body)) : await get(path);
      return { ok: true, text: async () => JSON.stringify(data) };
    };
    ctx.document.events.DOMContentLoaded();
  } else { ctx.window.testEditor(root, { quelle_seite: 1, quelle_dokument_id: 3 },
    { getCenter: () => [47, 9], getZoom: () => 18 }, get, post, async () => { aktualisiert++; }); }
  return { root, maps, posts, markers, bilder, gift, ctx, overlays, tiles, anfragen, aktualisiert: () => aktualisiert };
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


const { affineMatrix, naechsterSchritt } = context.window.objektGeorefEditor;

for (const [name, tl, tr, bl, erwartet] of [
  ["Translation und unabhaengige Skalierung", { x: 10, y: 20 }, { x: 410, y: 20 }, { x: 10, y: 320 }, [2, 0, 0, 3, 10, 20]],
  ["90 Grad Rotation", { x: 10, y: 20 }, { x: 10, y: 420 }, { x: -290, y: 20 }, [0, 2, -3, 0, 10, 20]],
  ["Scherung", { x: -5, y: 7 }, { x: 395, y: 107 }, { x: 45, y: 307 }, [2, 0.5, 0.5, 3, -5, 7]],
]) {
  test(`affineMatrix: ${name} bildet alle drei Ecken ab`, () => {
    const m = affineMatrix(tl, tr, bl, 200, 100);
    assert.deepEqual([m.a, m.b, m.c, m.d, m.e, m.f], erwartet);
    for (const [x, y, ziel] of [[0, 0, tl], [200, 0, tr], [0, 100, bl]]) {
      assert.equal(m.a * x + m.c * y + m.e, ziel.x);
      assert.equal(m.b * x + m.d * y + m.f, ziel.y);
    }
  });
}

test("Schrittlogik fuehrt durch vier Ecken und erlaubt weitere Punkte", () => {
  for (let n = 0; n < 4; n++) {
    assert.equal(naechsterSchritt(Array(n), null).text, `Ecke ${n + 1} von 4 · jetzt im Plan klicken`);
    assert.equal(naechsterSchritt(Array(n), {}).aktiv, "karte");
  }
  assert.equal(naechsterSchritt(Array(4), null).text, "Vier Ecken erfasst · jetzt Prüfen");
  assert.equal(naechsterSchritt(Array(4), {}).aktiv, "karte");
});

const knopf = (node, text) => node.alle("button").find((b) => b.textContent === text);

test("Schrittanzeige, Orthofoto, Esc und Backspace mit Eingabefeldschutz", async () => {
  const e = editorUmgebung("veraltet"); await fertig();
  const dialog = e.root.childNodes[0];
  const schritt = dialog.alle("p").find((p) => p["aria-live"] === "polite");
  assert.equal(schritt.textContent, "Ecke 1 von 4 · jetzt im Plan klicken");
  for (let i = 0; i < 4; i++) {
    assert.match(e.maps[0].node.className, /ogx__aktiv/);
    e.maps[0].events.click({ latlng: { lng: 500, lat: -750 } });
    assert.match(e.maps[1].node.className, /ogx__aktiv/);
    assert.equal(schritt.textContent, `Ecke ${i + 1} von 4 · jetzt auf der Karte klicken`);
    e.maps[1].events.click({ latlng: { lat: 47 + i, lng: 9 } });
    assert.equal(e.posts.at(-1).body.punkte[i].quelle, "orthofoto_klick");
  }
  assert.equal(schritt.textContent, "Vier Ecken erfasst · jetzt Prüfen");
  knopf(dialog, "Weitere Punkte hinzufügen").onclick();
  assert.match(e.maps[0].node.className, /ogx__aktiv/);
  e.maps[0].events.click({ latlng: { lng: 100, lat: -100 } });
  let verhindert = 0;
  const taste = (key, tagName = "DIV") => e.ctx.document.events.keydown({ key, target: { tagName }, preventDefault() { verhindert++; } });
  taste("Escape");
  assert.equal(verhindert, 1);
  const vorher = e.posts.length;
  e.maps[1].events.click({ latlng: { lat: 47, lng: 9 } });
  assert.equal(e.posts.length, vorher); // Esc hat den vorgemerkten Punkt entfernt.
  taste("Backspace", "INPUT");
  assert.equal(e.posts.length, vorher);
  taste("Backspace");
  assert.equal(e.posts.at(-1).body.punkte.length, 3);
  knopf(dialog, "Letztes Paar entfernen").onclick();
  assert.equal(e.posts.at(-1).body.punkte.length, 2);
});

test("Gueltige Vorschau nutzt affine Matrix, Hinweise als Text, Slider und Zoom aktualisieren", async () => {
  const e = editorUmgebung(); await fertig();
  const a = antwort();
  a.georeferenz.hinweise = [{ code: "probe", text: e.gift }];
  a.georeferenz.vorschau_ecken = [
    { lat: 20, lng: 10 }, { lat: 20, lng: 410 }, { lat: -280, lng: 410 }, { lat: -280, lng: 10 },
  ];
  e.posts[0].resolve(a); await fertig();
  const dialog = e.root.childNodes[0];
  const hinweise = dialog.alle("ul")[0];
  assert.equal(hinweise.role, "status");
  assert.equal(hinweise.alle("li")[0].textContent, e.gift);
  assert.equal(hinweise.alle("img").length, 0);
  assert.equal(e.bilder.length, 1); // imageOverlay ausschliesslich im Planfenster.
  const overlay = e.overlays[0];
  assert.equal(overlay.bild.style.transform, "matrix(0.2,0,0,0.3,10,-20)");
  assert.equal(overlay.bild.style.opacity, "0.5");
  e.maps[1].scale = 2; e.maps[1].events.zoom();
  assert.equal(overlay.bild.style.transform, "matrix(0.4,0,0,0.6,20,-40)");
  const slider = dialog.alle("input").find((i) => i.type === "range");
  slider.value = "80"; slider.oninput();
  assert.ok(overlay.removed);
  assert.equal(e.overlays.at(-1).bild.style.opacity, "0.8");
  const check = dialog.alle("input").find((i) => i.type === "checkbox");
  assert.equal(check.checked, true);
  check.checked = false; check.onchange();
  assert.ok(e.overlays.at(-1).removed);
  check.checked = true; check.onchange();
  e.maps[0].events.click({ latlng: { lng: 10, lat: -10 } });
  assert.ok(e.overlays.at(-1).removed);
  assert.equal(hinweise.alle("li").length, 0);
});

test("Speichern blockiert Aenderungen, Verwerfen und alte Pruefantworten", async () => {
  const e = editorUmgebung(); await fertig();
  const dialog = e.root.childNodes[0];
  e.posts[0].resolve(antwort()); await fertig();
  const speichern = knopf(dialog, "Speichern");
  speichern.onclick();
  const vorher = e.posts.length;
  e.maps[0].events.click({ latlng: { lng: 500, lat: -750 } });
  knopf(dialog, "Letztes Paar entfernen").onclick();
  knopf(dialog, "Georeferenz verwerfen").onclick();
  speichern.onclick();
  knopf(dialog, "Schließen").onclick();
  assert.equal(dialog.open, true);
  assert.equal(e.posts.length, vorher);
  assert.equal(e.posts.at(-1).body.punkte.length, 1);
  e.posts.at(-1).resolve({ georeferenz: { revision: 3 } }); await fertig();
  assert.equal(dialog.open, false);
  assert.equal(e.ctx.document.events.keydown, undefined);
});

test("Seite startet automatisch, bleibt nach Speichern offen und verwendet neue Revision", async () => {
  const e = editorUmgebung("gueltig", true); await fertig();
  assert.equal(e.root.tagName, "section");
  assert.equal(e.root.alle("dialog").length, 0);
  assert.equal(e.posts[0].body.dokument_id, 3);
  assert.equal(e.anfragen[0].url, "/objekte/7/karte/georef.json?dokument_id=3&seite=1");
  assert.equal(e.anfragen[0].options.headers["X-CSRF-Token"], "csrf-token");
  const mutation = e.anfragen.find((r) => r.options.method === "POST");
  assert.equal(mutation.options.headers["X-CSRF-Token"], "csrf-token");
  assert.equal(mutation.options.headers["Content-Type"], "application/json");
  e.posts[0].resolve(antwort()); await fertig();
  knopf(e.root, "Speichern").onclick();
  e.posts[1].resolve({ georeferenz: { revision: 3 } }); await fertig();
  assert.match(e.root.textContent, /Georeferenz gespeichert/);
  assert.equal(e.root.alle("a")[0].href, "/zurueck");
  assert.ok(e.maps.every((m) => !m.removed));
  knopf(e.root, "Prüfen").onclick();
  assert.equal(e.posts.at(-1).body.expected_revision, 3);
});


for (const [status, body, text] of [
  [409, JSON.stringify({ detail: "Seite wurde inzwischen gedreht" }), "Seite wurde inzwischen gedreht"],
  [422, JSON.stringify({ detail: [{ msg: "Ungültige Punkte" }] }), "Ungültige Punkte"],
  [503, "Kartenquelle derzeit nicht erreichbar", "Kartenquelle derzeit nicht erreichbar"],
]) {
  test(`Eigener Fetch-Helfer zeigt Fehlertext bei ${status} sicher an`, async () => {
    const e = editorUmgebung("veraltet", true); await fertig();
    e.ctx.fetch = async () => ({ ok: false, status, text: async () => body });
    knopf(e.root, "Prüfen").onclick(); await fertig();
    assert.ok(e.root.textContent.includes(text));
    assert.equal(knopf(e.root, "Speichern").disabled, true);
  });
}

test("Seite verwirft mit Revision und ignoriert laufende alte Pruefung", async () => {
  const e = editorUmgebung("gueltig", true); await fertig();
  knopf(e.root, "Georeferenz verwerfen").onclick();
  assert.equal(e.posts[1].path, "/georef/verwerfen");
  assert.equal(e.posts[1].body.expected_revision, 2);
  assert.equal(e.posts[1].body.expected_rotation, 0);
  e.posts[0].resolve(antwort()); await fertig();
  assert.equal(knopf(e.root, "Speichern").disabled, true);
  e.posts[1].resolve({ georeferenz: { revision: 2 } }); await fertig();
  assert.ok(e.root.textContent.includes("Georeferenz verworfen"));
  assert.equal(knopf(e.root, "Georeferenz verwerfen").disabled, true);
  assert.equal(e.root.alle("button").filter((b) => b.textContent === "Paar löschen").length, 0);
});

const { bspLagegenauigkeit, bspStatus } = context.window.objektGeorefEditor;

test("BSP-Lagegenauigkeit: Klartext und Schwellen dienen nur der Darstellung", () => {
  for (const [rmse_m, wort] of [[0, "gut"], [0.4, "gut"], [0.5, "gut"], [0.51, "prüfen"], [2, "prüfen"], [2.01, "ungenau"], [null, "unbekannt"]]) {
    const text = bspLagegenauigkeit({ status: "gueltig", rmse_m, punktzahl: 5 });
    assert.match(text, new RegExp("Lagegenauigkeit: " + wort));
    assert.match(text, /5 Referenzpunkte/);
  }
  assert.match(bspLagegenauigkeit({ status: "gueltig", rmse_m: 0.4, punktzahl: 5 }), /Abweichung 0,4 m/);
  assert.match(bspLagegenauigkeit({ status: "gueltig", rmse_m: 0.002, punktzahl: 3 }), /Abweichung unter 1 cm/);
  assert.match(bspLagegenauigkeit({ status: "ungueltig", rmse_m: 0.1, punktzahl: 3 }), /neu prüfen/);
  assert.match(bspLagegenauigkeit(null), /nach dem Speichern/);
});

test("BSP-Statuschips: jeder Zustand hat Text, App-Badge und Systemicon", () => {
  for (const [status, text, badge, icon] of [
    ["entwurf", "Entwurf", "muted", "file-text"], ["freigegeben", "Freigegeben", "success", "circle-check"],
    ["veraltet", "Veraltet", "exercise", "alert-triangle"], ["zurueckgezogen", "Zurückgezogen", "muted", "archive"],
  ]) { assert.deepEqual(Array.from(bspStatus(status)), [text, badge, icon]); }
});

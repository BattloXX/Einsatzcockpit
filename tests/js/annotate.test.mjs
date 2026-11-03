import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const quelle = readFileSync(new URL("../../app/static/js/annotate.js", import.meta.url), "utf8");
// Autosave-Funktionen mit kleinem Konva-Double, ohne Browser/Canvas-Abhaengigkeit.
const autosave = quelle.slice(quelle.indexOf("  function flachesPng("), quelle.indexOf("  function setStatus("));

function umgebung({ overlay = true, breite = 1200, hoehe = 800, fehler = false } = {}) {
  let bildSichtbar = true, transformerSichtbar = true;
  const exporte = [], requests = [], puffer = new Map();
  const context = {
    cfg: { overlay, canWrite: true, saveUrl: "/api/annotation/objekt_seite/7", csrf: "csrf-test" },
    natW: breite, natH: hoehe, dirty: true, saving: false,
    imageLayer: {
      visible(v) { if (v !== undefined) bildSichtbar = v; return bildSichtbar; },
      hide() { bildSichtbar = false; },
    },
    tr: {
      visible: () => transformerSichtbar,
      hide() { transformerSichtbar = false; },
      show() { transformerSichtbar = true; },
    },
    stage: {
      width: () => 600, height: () => 400,
      x: () => -175, y: () => 30, scaleX: () => 1.5, scaleY: () => 1.5,
      toDataURL(opts) {
        assert.equal(transformerSichtbar, false);
        if (fehler) throw new Error("Canvas fehlgeschlagen");
        exporte.push({ opts: { ...opts }, bildSichtbar });
        return bildSichtbar ? "data:image/png;base64,flat" : "data:image/png;base64,overlay";
      },
    },
    snapshot: () => '{"className":"Layer","children":[]}',
    setStatus() {},
    fetch: async (url, opts) => { requests.push({ url, ...opts }); throw new Error("offline"); },
    sessionStorage: {
      setItem: (k, v) => puffer.set(k, v), getItem: (k) => puffer.get(k), removeItem: (k) => puffer.delete(k),
    },
  };
  vm.createContext(context);
  vm.runInContext(autosave, context);
  return { context, exporte, requests, puffer, sichtbar: () => [bildSichtbar, transformerSichtbar] };
}

for (const [breite, hoehe] of [[1200, 800], [800, 1200], [3600, 2400]]) {
  test(`Flachbild und transparenter Overlay haben gleiche Seitenmasse (${breite}x${hoehe}), auch bei Zoom/Pan`, () => {
    const u = umgebung({ breite, hoehe });
    u.context.flachesPng();
    u.context.flachesPng(true);
    assert.deepEqual(u.exporte[0].opts, u.exporte[1].opts);
    const opts = u.exporte[0].opts;
    const faktor = Math.min(1, 2400 / Math.max(breite, hoehe));
    assert.equal(opts.width * opts.pixelRatio, breite * faktor);
    assert.equal(opts.height * opts.pixelRatio, hoehe * faktor);
    assert.equal(opts.x, -175);
    assert.equal(opts.y, 30);
    assert.equal(u.exporte[0].bildSichtbar, true);
    assert.equal(u.exporte[1].bildSichtbar, false);
    assert.deepEqual(u.sichtbar(), [true, true]);
  });
}

test("Exportfehler stellt Hintergrund und Transformer wieder her", () => {
  const u = umgebung({ fehler: true });
  assert.throws(() => u.context.flachesPng(true), /Canvas fehlgeschlagen/);
  assert.deepEqual(u.sichtbar(), [true, true]);
});

for (const overlay of [true, false]) {
  test(`Speichern und Offline-Puffer enthalten Overlay nur mit Konfiguration (${overlay})`, async () => {
    const u = umgebung({ overlay });
    await u.context.save();
    const request = u.requests[0];
    const payload = JSON.parse(request.body);
    assert.equal(request.headers["X-CSRF-Token"], "csrf-test");
    assert.equal(payload.png, "data:image/png;base64,flat");
    assert.equal(payload.annotation_json, '{"className":"Layer","children":[]}');
    assert.equal(payload.overlay_png, overlay ? "data:image/png;base64,overlay" : undefined);
    assert.equal(u.puffer.get("anno_buffer_" + request.url), request.body);
    if (!overlay) {
      assert.equal(u.exporte[0].opts.width, undefined);
      assert.equal(u.exporte[0].opts.pixelRatio, 2);
    }
    u.context.fetch = async (url, opts) => { u.requests.push({ url, ...opts }); return { ok: true }; };
    u.context.resendBuffered();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(u.requests[1].body, request.body);
    assert.equal(u.puffer.size, 0);
  });
}

const symbole = {};
vm.createContext(symbole);
vm.runInContext(quelle.slice(quelle.indexOf("  function recentKey("), quelle.indexOf("  function flaecheSwatch(")), symbole);

test("Symbolsuche findet Namen und Code ohne Beachtung der Grossschreibung", () => {
  const item = { name: "Brandmeldezentrale", code: "BMZ_01" };
  for (const q of ["brandmelde", " bmz_01 ", "", "ZENTRALE"]) {
    assert.equal(symbole.symbolMatches(item, q), true);
  }
  assert.equal(symbole.symbolMatches(item, "hydrant"), false);
  assert.equal(symbole.symbolMatches({ name: "Wasser", such: "loeschmittel" }, "loesch"), true);
});

test("Objekt-Bildsymbole behalten die geschuetzte same-origin URL", () => {
  assert.equal(symbole.objektSymbolSrc({ stil: "bild", bild: "/objekt-medien/symbol/17" }), "/objekt-medien/symbol/17");
});

for (const [stil, form, farbe] of [
  ["box", "rect", "#b71921"], ["gruen", "rect", "#15803d"], ["rot", "rect", "#b71921"],
  ["dreieck", "path", "#facc15"], ["hydrant", "circle", "#dc2626"],
  ["pfeil-voll", "text", "#b71921"], ["pfeil-leer", "text", "#f87171"],
]) {
  test(`Objektstil ${stil} wird als eigenstaendiges SVG gerendert`, () => {
    const src = symbole.objektSymbolSrc({ stil, text: 'BMZ<&"' });
    assert.ok(src.startsWith("data:image/svg+xml;charset=utf-8,"));
    const svg = decodeURIComponent(src.split(",")[1]);
    assert.ok(svg.includes(`<${form} `));
    assert.ok(svg.includes(farbe));
    assert.ok(svg.includes("BMZ&lt;&amp;&quot;"));
    assert.equal(svg.includes("foreignObject"), false);
    assert.equal(svg.includes("href="), false);
  });
}

test("Zuletzt verwendet bleibt zwischen Reitern getrennt, begrenzt und dedupliziert", () => {
  const storage = new Map();
  symbole.localStorage = { getItem: (k) => storage.get(k), setItem: (k, v) => storage.set(k, v) };
  symbole.recentAdd("symbole", "bmz");
  for (let i = 0; i < 10; i++) symbole.recentAdd("symbole", String(i), "objekt");
  symbole.recentAdd("symbole", "9", "objekt");
  assert.equal(symbole.recentRead("objekt").length, 8);
  assert.equal(symbole.recentRead("objekt")[0].id, "9");
  assert.equal(symbole.recentRead("tz")[0].id, "bmz");
});

for (const src of ["/objekt-medien/symbol/17", symbole.objektSymbolSrc({ stil: "box", text: "BMZ" })]) {
  test(`Hydrate stellt gespeicherte Objektgruppe wieder her (${src.startsWith("data:") ? "SVG" : "Bild"})`, () => {
    let bild;
    const attrs = { annoType: "symbol", symbolSrc: src, objektSymbolCode: "bmz" };
    const context = {
      imgCache: {}, annLayer: { batchDraw() {} },
      Image: class { complete = true; },
    };
    vm.createContext(context);
    vm.runInContext(quelle.slice(quelle.indexOf("  function hydrate("), quelle.indexOf("  // ── Zeichnen")), context);
    const node = {
      getAttr: (key) => attrs[key], getClassName: () => "Group",
      findOne: () => ({ image: (im) => { bild = im; } }),
    };
    context.hydrate(node);
    assert.equal(bild.src, src);
    assert.equal(bild.crossOrigin, undefined);
    assert.equal(attrs.objektSymbolCode, "bmz");
  });
}

for (const status of [200, 404, 500]) {
  test(`Objektkatalog zeigt optionalen Reiter nur bei Erfolg (HTTP ${status})`, async () => {
    const context = {
      cfg: { objektSymbole: true }, pickerManifest: {}, objektItems: null, TZ: { symbole: {}, flaechen: {} },
      imgCache: {}, renderPicker() {}, objektSymbolSrc: symbole.objektSymbolSrc,
      fetch: async (url) => ({
        ok: url.includes("karten-symbole") ? status === 200 : true,
        json: async () => ({ symbole: url.includes("karten-symbole") ? [{ code: "bmz", name: "BMZ", stil: "box" }] : [] }),
      }),
    };
    vm.createContext(context);
    vm.runInContext(quelle.slice(quelle.indexOf("  function loadManifest("), quelle.indexOf("  // ── Init")), context);
    context.loadManifest();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(context.objektItems !== null, status === 200);
    if (status === 200) assert.equal(context.objektItems[0].objektSymbolCode, "bmz");
  });
}

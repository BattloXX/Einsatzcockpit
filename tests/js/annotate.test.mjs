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

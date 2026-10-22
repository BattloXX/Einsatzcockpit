// APP-01: Pull-to-Refresh-Schnittstelle von native-bridge.js (ELNative.refresh / ptrBlocked).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const quelle = readFileSync(new URL("../../app/static/js/native-bridge.js", import.meta.url), "utf8");

function umgebung({ online = true, felder = [], overlays = [], scrollY = 0 } = {}) {
  const aufrufe = { done: 0, failed: [], reload: 0, events: [] };
  const listener = new Map();
  const window = {
    scrollY,
    ECPtr: {
      done() { aufrufe.done += 1; },
      failed(msg) { aufrufe.failed.push(msg); },
      setBlocked() {},
    },
    location: { reload() { aufrufe.reload += 1; } },
    addEventListener(name, fn) { listener.set(name, [...(listener.get(name) || []), fn]); },
    dispatchEvent(event) {
      aufrufe.events.push(event);
      (listener.get(event.type) || []).forEach((fn) => fn(event));
      return true;
    },
  };
  const document = {
    readyState: "loading",
    scrollingElement: { scrollTop: 0 },
    documentElement: {},
    addEventListener() {},
    querySelectorAll(selector) {
      if (selector === "input, textarea, select") return felder;
      if (selector === '[contenteditable="true"]') return [];
      return overlays;
    },
    body: {},
  };
  const kontext = {
    window, document, console,
    navigator: { onLine: online },
    CustomEvent: class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
    localStorage: { getItem() { return null; }, setItem() {} },
  };
  window.document = document;
  window.navigator = kontext.navigator;
  vm.runInNewContext(quelle, kontext);
  return { window, aufrufe, native: window.ELNative, listener };
}

const sauberesFeld = { type: "text", value: "", defaultValue: "", disabled: false, tagName: "INPUT" };
const geaendertesFeld = { type: "text", value: "Neu", defaultValue: "", disabled: false, tagName: "INPUT" };

test("offline: Hinweis statt Aktualisierung, kein Reload", async () => {
  const { native, aufrufe } = umgebung({ online: false });
  assert.equal(await native.refresh(), false);
  assert.equal(aufrufe.reload, 0);
  assert.match(aufrufe.failed[0], /Keine Internetverbindung/);
});

test("Seite mit Resync-Handler wird ohne Reload aktualisiert", async () => {
  const { native, aufrufe, window } = umgebung();
  window.addEventListener("ec:resync", (event) => {
    event.detail.handled = true;
    event.detail.promises.push(Promise.resolve());
  });
  assert.equal(await native.refresh(), true);
  assert.equal(aufrufe.reload, 0);
  assert.equal(aufrufe.done, 1);
  assert.equal(aufrufe.events[0].detail.source, "pull");
});

test("Seite ohne Handler wird neu geladen, solange nichts ungespeichert ist", async () => {
  const { native, aufrufe } = umgebung({ felder: [sauberesFeld] });
  assert.equal(await native.refresh(), true);
  assert.equal(aufrufe.reload, 1);
});

test("ungespeicherte Eingaben verhindern den Reload", async () => {
  const { native, aufrufe } = umgebung({ felder: [geaendertesFeld] });
  assert.equal(await native.refresh(), false);
  assert.equal(aufrufe.reload, 0);
  assert.match(aufrufe.failed[0], /Ungespeicherte Eingaben/);
});

test("ptrBlocked: Karte, gescrollter Container, Dialog und Eingaben sperren die Geste", () => {
  const frei = { matches: () => false, scrollTop: 0, parentElement: null };
  assert.equal(umgebung().native.ptrBlocked(frei), false);

  const karte = { matches: (s) => s.includes(".leaflet-container"), scrollTop: 0, parentElement: null };
  assert.equal(umgebung().native.ptrBlocked({ matches: () => false, scrollTop: 0, parentElement: karte }), true);

  const gescrollt = { matches: () => false, scrollTop: 40, parentElement: null };
  assert.equal(umgebung().native.ptrBlocked(gescrollt), true);

  assert.equal(umgebung({ scrollY: 10 }).native.ptrBlocked(frei), true);

  const dialog = { offsetParent: {}, getClientRects: () => [1] };
  assert.equal(umgebung({ overlays: [dialog] }).native.ptrBlocked(frei), true);
  const verstecktes = { offsetParent: null, getClientRects: () => [] };
  assert.equal(umgebung({ overlays: [verstecktes] }).native.ptrBlocked(frei), false);

  assert.equal(umgebung({ felder: [geaendertesFeld] }).native.ptrBlocked(frei), true);
});

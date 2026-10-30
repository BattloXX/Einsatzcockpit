// APP-01: Pull-to-Refresh-Schnittstelle von native-bridge.js (ELNative.refresh / ptrBlocked).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const ptrQuelle = readFileSync(new URL("../../app/static/js/native-ptr.js", import.meta.url), "utf8");
const quelle = readFileSync(new URL("../../app/static/js/native-bridge.js", import.meta.url), "utf8");

function umgebung({ online = true, felder = [], overlays = [], scrollY = 0, fullBridge = true, now = () => 0 } = {}) {
  const aufrufe = { done: 0, failed: [], reload: 0, events: [], blocked: [] };
  const listener = new Map();
  const docListener = new Map();
  const window = {
    scrollY,
    getComputedStyle(el) { return { overflowY: el.overflowY || "visible" }; },
    ECPtr: {
      done() { aufrufe.done += 1; },
      failed(msg) { aufrufe.failed.push(msg); },
      setBlocked(value) { aufrufe.blocked.push(value); },
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
    addEventListener(name, fn) { docListener.set(name, [...(docListener.get(name) || []), fn]); },
    elementFromPoint() { return null; },
    querySelectorAll(selector) {
      if (selector === 'input, textarea, select, [contenteditable="true"]') return felder;
      if (selector === '[contenteditable="true"]') return [];
      return overlays;
    },
    body: {},
  };
  const kontext = {
    window, document, console,
    Date: { now },
    navigator: { onLine: online },
    CustomEvent: class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    setTimeout, clearTimeout, setInterval() { return 1; }, clearInterval() {},
    localStorage: { getItem() { return null; }, setItem() {} },
  };
  window.document = document;
  window.navigator = kontext.navigator;
  vm.runInNewContext(ptrQuelle, kontext);
  if (fullBridge) vm.runInNewContext(quelle, kontext);
  return { window, document, aufrufe, native: window.ELNative, listener,
    emit(type, event) { (docListener.get(type) || []).forEach(fn => fn(event)); },
  };
}

const sauberesFeld = { closest: () => null, type: "text", value: "", defaultValue: "", disabled: false, tagName: "INPUT" };
const geaendertesFeld = { closest: (s) => s.startsWith("input,") ? geaendertesFeld : null, type: "text", value: "Neu", defaultValue: "", disabled: false, tagName: "INPUT" };

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
  const { native, aufrufe, emit } = umgebung({ felder: [geaendertesFeld] });
  emit("input", { target: geaendertesFeld, isTrusted: true });
  assert.equal(await native.refresh(), false);
  assert.equal(aufrufe.reload, 0);
  assert.match(aufrufe.failed[0], /Ungespeicherte Eingaben/);
});

test("ptrBlocked: Karte, gescrollter Container, Dialog und Eingaben sperren die Geste", () => {
  const frei = { matches: () => false, scrollTop: 0, parentElement: null };
  assert.equal(umgebung().native.ptrBlocked(frei), false);

  const karte = { matches: (s) => s.includes(".leaflet-container"), scrollTop: 0, parentElement: null };
  assert.equal(umgebung().native.ptrBlocked({ matches: () => false, scrollTop: 0, parentElement: karte }), true);

  const gescrollt = { nodeType: 1, scrollHeight: 200, clientHeight: 100, overflowY: "auto", matches: () => false, scrollTop: 40, parentElement: null };
  assert.equal(umgebung().native.ptrBlocked(gescrollt), true);

  assert.equal(umgebung({ scrollY: 10 }).native.ptrBlocked(frei), true);

  const dialog = { offsetParent: {}, getClientRects: () => [1] };
  assert.equal(umgebung({ overlays: [dialog] }).native.ptrBlocked(frei), true);
  const verstecktes = { offsetParent: null, getClientRects: () => [] };
  assert.equal(umgebung({ overlays: [verstecktes] }).native.ptrBlocked(frei), false);

  const u = umgebung({ felder: [geaendertesFeld] });
  u.emit("input", { target: geaendertesFeld, isTrusted: true });
  assert.equal(u.native.ptrBlocked(frei), true);
});

function feld(props = {}) {
  const f = { type: 'text', tagName: 'INPUT', value: 'Neu', defaultValue: '', ...props };
  f.closest = (selector) => selector.startsWith('input,') ? f : (f.ignored ? f : null);
  return f;
}
function scrollbereich(props = {}) {
  return { nodeType: 1, scrollHeight: 200, clientHeight: 100, overflowY: 'auto',
    scrollTop: 0, parentElement: null, matches: () => false, ...props };
}

test('Vorbelegung und synthetische Events sperren nicht; echte Eingabe und Loeschen schon', () => {
  const f = feld();
  const u = umgebung({ felder: [f] });
  assert.equal(u.native.ptrBlocked(), false);
  u.emit('input', { target: f, isTrusted: false });
  assert.equal(u.native.ptrBlocked(), false);
  u.emit('input', { target: f, isTrusted: true });
  assert.equal(u.native.ptrBlocked(), true);
  f.value = f.defaultValue;
  assert.equal(u.native.ptrBlocked(), false);
  f.defaultValue = 'Vorhanden';
  f.value = '';
  assert.equal(u.native.ptrBlocked(), true);
});

test('Such-/Filterfelder, data-no-dirty und deaktivierte Felder bleiben frei', () => {
  for (const props of [{ type: 'search', ignored: true }, { ignored: true }, { disabled: true }]) {
    const f = feld(props);
    const u = umgebung({ felder: [f] });
    u.emit('input', { target: f, isTrusted: true });
    assert.equal(u.native.ptrBlocked(), false);
  }
});

test('Checkbox, Select und Contenteditable werden erst nach Eingabe geschuetzt', () => {
  for (const props of [
    { type: 'checkbox', checked: true, defaultChecked: false },
    { tagName: 'SELECT', options: [{ selected: true, defaultSelected: false }] },
    { isContentEditable: true, textContent: '' },
  ]) {
    const f = feld(props);
    const u = umgebung({ felder: [f] });
    assert.equal(u.native.ptrBlocked(), false);
    u.emit('input', { target: f, isTrusted: true });
    assert.equal(u.native.ptrBlocked(), true);
  }
});

test('Naechster echter Scrollbereich entscheidet auch bei gescrollter aeusserer Seite', () => {
  const aussen = scrollbereich({ scrollTop: 80 });
  const innen = scrollbereich({ parentElement: aussen });
  const ziel = scrollbereich({ overflowY: 'hidden', scrollTop: 12, parentElement: innen });
  const u = umgebung({ scrollY: 1 });
  assert.equal(u.native.ptrBlocked(ziel), false);
  innen.scrollTop = 10;
  assert.equal(u.native.ptrBlocked(ziel), true);
  innen.scrollTop = -1;
  assert.equal(u.native.ptrBlocked(ziel), false);
  // Auch ein aeusserer Karten-/Editorbereich bleibt gesperrt.
  aussen.matches = () => true;
  assert.equal(u.native.ptrBlocked(ziel), true);
});

test('Touchpunkt, touchmove und scroll aktualisieren Sperre ohne Scroll-Toast', () => {
  const ziel = scrollbereich({ scrollTop: 20 });
  const u = umgebung();
  u.document.elementFromPoint = () => ziel;
  u.emit('touchstart', { target: {}, touches: [{ clientX: 5, clientY: 10 }] });
  assert.equal(u.aufrufe.blocked.at(-1), true);
  u.emit('touchmove', { touches: [{ clientY: 80 }] });
  u.emit('touchmove', { touches: [{ clientY: 90 }] });
  assert.equal(u.aufrufe.failed.length, 0);
  ziel.scrollTop = 0;
  u.emit('scroll', { target: u.document });
  assert.equal(u.aufrufe.blocked.at(-1), false);
  ziel.scrollTop = 5;
  u.emit('touchmove', { touches: [{ clientY: 100 }] });
  assert.equal(u.aufrufe.blocked.at(-1), true);
  assert.equal(u.aufrufe.failed.length, 0);
});

test('Scrollsperre beendet direkten Refresh still, auch offline und im inneren Scrollbereich', async () => {
  for (const online of [true, false]) {
    for (const innen of [true, false]) {
      const u = umgebung({ online, scrollY: innen ? 0 : 20 });
      if (innen) {
        u.emit('touchstart', { target: scrollbereich({ scrollTop: 20 }), touches: [{ clientY: 10 }] });
      }
      assert.equal(await u.native.refresh(), false);
      assert.equal(u.aufrufe.done, 1);
      assert.equal(u.aufrufe.failed.length, 0);
      assert.equal(u.aufrufe.reload, 0);
      assert.equal(u.aufrufe.events.length, 0);
    }
  }
});

test('Dialog, Karte/Editor und Eingaben: einmal je Geste, 30s Cooldown je Grund und Seite', async () => {
  let zeit = 0;
  const overlays = [{ offsetParent: {} }];
  const f = feld();
  const u = umgebung({ overlays, felder: [f], now: () => zeit });
  const ziel = scrollbereich();
  const start = () => u.emit('touchstart', { target: ziel, touches: [{ clientY: 10 }] });
  const move = () => u.emit('touchmove', { touches: [{ clientY: 80 }] });
  start();
  move();
  zeit = 30000;
  move(); // Selbst nach Ablauf des Cooldowns kein zweiter Hinweis in derselben Geste.
  assert.equal(u.aufrufe.failed.length, 1);
  u.emit('touchend', {});
  start();
  move();
  assert.equal(u.aufrufe.failed.length, 2);
  assert.match(u.aufrufe.failed.at(-1), /Dialog/);
  zeit = 59999;
  start();
  move();
  assert.equal(u.aufrufe.failed.length, 2);
  assert.equal(await u.native.refresh(), false);
  assert.equal(u.aufrufe.done, 1); // Unterdrueckter Toast darf keinen Spinner zuruecklassen.
  overlays.length = 0;
  ziel.matches = () => true;
  start();
  move();
  assert.match(u.aufrufe.failed.at(-1), /Karte, Editor/);
  ziel.matches = () => false;
  u.emit('input', { target: f, isTrusted: true });
  start();
  move();
  assert.match(u.aufrufe.failed.at(-1), /Ungespeicherte/);
  assert.equal(u.aufrufe.failed.length, 4);
  start();
  move();
  assert.equal(await u.native.refresh(), false);
  assert.equal(u.aufrufe.failed.length, 4);
  zeit += 30000;
  assert.equal(await u.native.refresh(), false);
  assert.equal(u.aufrufe.failed.length, 5);
  const v = umgebung({ overlays: [{ offsetParent: {} }], now: () => zeit });
  assert.equal(await v.native.refresh(), false);
  assert.equal(v.aufrufe.failed.length, 1); // Neue Seite hat einen eigenen Cooldown.
});

test('Eingaben verhindern auch Resync; abgelehnte Auftraege melden Fehler', async () => {
  const f = feld();
  const u = umgebung({ felder: [f] });
  u.window.addEventListener('ec:resync', e => { e.detail.handled = true; });
  u.emit('input', { target: f, isTrusted: true });
  assert.equal(await u.native.refresh(), false);
  assert.equal(u.aufrufe.events.length, 0);
  const v = umgebung();
  v.window.addEventListener('ec:resync', e => {
    e.detail.handled = true;
    e.detail.promises.push(Promise.reject(new Error('HTTP 500')));
  });
  assert.equal(await v.native.refresh(), false);
  assert.equal(v.aufrufe.done, 0);
  assert.match(v.aufrufe.failed[0], /fehlgeschlagen/);
});


test('Eigenstaendige Seiten bieten Refresh ohne GPS-/Push-Bruecke; pagehide gibt Sperre frei', async () => {
  const u = umgebung({ fullBridge: false });
  assert.equal(await u.native.refresh(), true);
  assert.equal(u.aufrufe.reload, 1);
  u.window.dispatchEvent({ type: 'pagehide' });
  assert.equal(u.aufrufe.blocked.at(-1), false);
});

test('Refresh prueft aktuelle Sperre erneut und wartet auf den Resync-Auftrag', async () => {
  const u = umgebung({ overlays: [{ offsetParent: {} }] });
  assert.equal(await u.native.refresh(), false);
  assert.equal(u.aufrufe.reload, 0);
  let finish;
  const v = umgebung();
  v.window.addEventListener('ec:resync', e => {
    e.detail.handled = true;
    e.detail.promises.push(new Promise(resolve => { finish = resolve; }));
  });
  const pending = v.native.refresh();
  assert.equal(v.aufrufe.done, 0);
  finish();
  assert.equal(await pending, true);
  assert.equal(v.aufrufe.done, 1);
});

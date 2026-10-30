// APP-01: Pull-to-Refresh-Schnittstelle von native-bridge.js (ELNative.refresh / ptrBlocked).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const ptrQuelle = readFileSync(new URL("../../app/static/js/native-ptr.js", import.meta.url), "utf8");
const quelle = readFileSync(new URL("../../app/static/js/native-bridge.js", import.meta.url), "utf8");

function umgebung({ online = true, felder = [], overlays = [], scrollY = 0, fullBridge = true, now = () => 0, fetchMock, loadViewer = false } = {}) {
  const aufrufe = { done: 0, failed: [], reload: 0, events: [], blocked: [], notices: [], assigned: [] };
  const listener = new Map();
  const docListener = new Map();
  const window = {
    scrollY,
    alert(message) { aufrufe.notices.push(message); },
    getComputedStyle(el) { return { overflowY: el.overflowY || "visible" }; },
    ECPtr: {
      done() { aufrufe.done += 1; },
      failed(msg) { aufrufe.failed.push(msg); },
      setBlocked(value) { aufrufe.blocked.push(value); },
    },
    location: { href: 'https://ecp.test/objekte/1', origin: 'https://ecp.test', reload() { aufrufe.reload += 1; }, assign(url) { aufrufe.assigned.push(url); } },
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
    querySelector() { return null; },
    body: {},
  };
  const kontext = {
    window, document, console, URL, fetch: fetchMock,
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
  if (loadViewer) vm.runInNewContext(readFileSync(new URL('../../app/static/js/dokumentviewer.js', import.meta.url), 'utf8'), kontext);
  return { window, document, aufrufe, native: window.ELNative, listener,
    emit(type, event) { return Promise.all((docListener.get(type) || []).map(fn => fn(event))); },
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

function downloadKlick(href, attribute) {
  const attrs = { [attribute]: '' };
  const link = { href, hasAttribute(name) { return name in attrs; }, getAttribute(name) { return attrs[name] ?? null; } };
  return {
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    target: { closest(selector) {
      return selector.split(', ').includes(`a[${attribute}]`) ? link : null;
    } },
  };
}

test('Native Downloads: download, Viewer-Link und neuer Tab nutzen genau einen Handoff mit Query', async () => {
  for (const attribute of ['download', 'data-dv-download', 'target="_blank"']) {
    const requests = [], opened = [];
    const path = '/objekt-medien/seite/7/pdf?download=1';
    const tokenUrl = 'https://ecp.test' + path + '&nt=test-token';
    const u = umgebung({ fetchMock: async (url, options) => {
      requests.push([url, JSON.parse(options.body).path]);
      return { ok: true, json: async () => ({ url: tokenUrl }) };
    } });
    u.window.Capacitor = { isNativePlatform: () => true, Plugins: {
      Browser: { open: async ({ url }) => { opened.push(url); } },
    } };
    const event = downloadKlick('https://ecp.test' + path, attribute);
    await u.emit('click', event);
    assert.equal(event.defaultPrevented, true);
    assert.deepEqual(requests, [['/api/v1/device/native-link', path]]);
    assert.deepEqual(opened, [tokenUrl]);
  }
});

test('Web-Downloads bleiben beim Browser; fremde Origins erhalten kein Handoff-Token', async () => {
  const opened = [], requests = [];
  const u = umgebung({ fetchMock: async (url) => { requests.push(url); return { ok: false }; } });
  const event = downloadKlick('https://ecp.test/datei?download=1', 'download');
  await u.emit('click', event);
  assert.equal(event.defaultPrevented, false);
  u.window.Capacitor = { isNativePlatform: () => true, Plugins: {
    Browser: { open: async ({ url }) => { opened.push(url); } },
  } };
  const external = downloadKlick('https://other.test/datei?download=1', 'download');
  await u.emit('click', external);
  assert.equal(external.defaultPrevented, true);
  assert.deepEqual(opened, ['https://other.test/datei?download=1']);
  assert.deepEqual(requests, []);
});

test('openUrl authentifiziert auch relative Objekt-URLs mit Filter und Download-Query', async () => {
  const requests = [], opened = [];
  const path = '/objekte/1/dokumente/sammel-pdf?nur_einsatzdruck=1&download=1';
  const u = umgebung({ fetchMock: async (_url, options) => {
    requests.push(JSON.parse(options.body).path);
    return { ok: true, json: async () => ({ url: 'https://ecp.test' + path + '&nt=token' }) };
  } });
  u.window.Capacitor = { isNativePlatform: () => true, Plugins: {
    Browser: { open: async ({ url }) => { opened.push(url); } },
  } };
  await u.native.openUrl(path + '#page=2');
  assert.deepEqual(requests, [path]);
  assert.deepEqual(opened, ['https://ecp.test' + path + '&nt=token#page=2']);
});

function mitDateiPlugin(options = {}) {
  const u = umgebung({ loadViewer: true, fetchMock: async () => { throw Error('Kein Handoff mit NativeFiles'); } });
  const calls = [];
  u.window.Capacitor = { isNativePlatform: () => true, Plugins: {
    NativeFiles: {
      download: async args => { calls.push(['download', { ...args }]); return { started: true }; },
      openFile: async args => { calls.push(['openFile', { ...args }]); return { opened: true }; },
      ...options,
    },
    Browser: { open: async () => { throw Error('NativeFiles hat Vorrang'); } },
  } };
  return { ...u, calls };
}

test('NativeFiles: absolute Same-Origin-URLs, Dateiname und kein nt/Handoff', async () => {
  const u = mitDateiPlugin();
  await u.native.downloadFile('/objekt-medien/seite/7/pdf?nt=alt&download=1', 'Plan.pdf');
  await u.native.openFile('https://ecp.test/plaene/3/pdf?version_id=4&nt=alt#page=2');
  assert.deepEqual(u.calls, [
    ['download', { url: 'https://ecp.test/objekt-medien/seite/7/pdf?download=1', filename: 'Plan.pdf' }],
    ['openFile', { url: 'https://ecp.test/plaene/3/pdf?version_id=4#page=2' }],
  ]);
  assert.deepEqual(u.aufrufe.notices, []);
});

test('Keine App zum Öffnen: NativeFiles.download übernimmt; andere Fehler werden angezeigt', async () => {
  const u = mitDateiPlugin({ openFile: async () => { throw Error('Keine App zum Öffnen vorhanden'); } });
  await u.native.openFile('/plan.pdf');
  assert.deepEqual(u.calls, [['download', { url: 'https://ecp.test/plan.pdf' }]]);
  assert.deepEqual(u.aufrufe.notices, []);
  const v = mitDateiPlugin({ openFile: async () => { throw Error('HTTP 403'); } });
  await v.native.openFile('/plan.pdf');
  assert.deepEqual(v.calls, []);
  assert.match(v.aufrufe.notices[0], /HTTP 403/);
});

test('Datei-API gibt fremde Origins und nicht-HTTP-URLs niemals an NativeFiles weiter', async () => {
  const u = mitDateiPlugin();
  await u.native.downloadFile('https://other.test/plan.pdf');
  await u.native.openFile('javascript:alert(1)');
  assert.deepEqual(u.calls, []);
  assert.equal(u.aufrufe.notices.length, 2);
});

test('Alte App ohne Plugins: Hinweis und Navigation nur für bestätigte Attachment-Routen', async () => {
  const u = umgebung();
  u.window.Capacitor = { isNativePlatform: () => true, Plugins: {} };
  await u.native.downloadFile('/objekt-medien/dokument/7/original?version=2#page=1');
  assert.deepEqual(u.aufrufe.assigned, ['https://ecp.test/objekt-medien/dokument/7/original?version=2&download=1#page=1']);
  await u.native.downloadFile('/druck/dokument.pdf?document_type=einsatzinfo');
  await u.native.downloadFile('/unbekannt.pdf');
  await u.native.openFile('/plan.pdf');
  assert.equal(u.aufrufe.assigned.length, 1);
  assert.equal(u.aufrufe.notices.length, 4);
  assert.ok(u.aufrufe.notices.every(msg => msg === 'Bitte die App aktualisieren, um Dateien herunterzuladen.'));
});

test('Dateihinweise nutzen den vorhandenen Alpine-Toast', async () => {
  const u = umgebung();
  const app = {};
  u.document.querySelector = () => app;
  u.window.Alpine = { $data: el => {
    assert.equal(el, app);
    return { addToast: (message, type) => u.aufrufe.notices.push([message, type]) };
  } };
  u.window.Capacitor = { isNativePlatform: () => true };
  await u.native.openFile('/plan.pdf');
  assert.deepEqual(u.aufrufe.notices, [['Bitte die App aktualisieren, um Dateien herunterzuladen.', 'warn']]);
});

test('Klicks: download-Attribute, Viewer-Download und Query laden; PDF-Tabs öffnen', async () => {
  for (const [attribute, path, action] of [
    ['download', '/datei', 'download'],
    ['data-dv-download', '/datei', 'download'],
    ['target="_blank"', '/plan.pdf?download=1', 'download'],
    ['target="_blank"', '/plaene/3/pdf', 'openFile'],
    ['target="_blank"', '/objekt-medien/dokument/7/original', 'openFile'],
  ]) {
    const u = mitDateiPlugin();
    const event = downloadKlick('https://ecp.test' + path, attribute);
    await u.emit('click', event);
    assert.equal(event.defaultPrevented, true);
    assert.deepEqual(u.calls, [[action, { url: 'https://ecp.test' + path }]]);
    // Ein bereits verarbeiteter Klick wird nicht erneut ausgeführt.
    await u.emit('click', event);
    assert.equal(u.calls.length, 1);
  }
});

test('Viewer-Original und Auszug nutzen auch bei Dateipfaden ohne PDF-Suffix NativeFiles', async () => {
  for (const attribute of ['data-dv-original', 'data-dv-excerpt']) {
    const u = mitDateiPlugin();
    const link = { href: 'https://ecp.test/datei/7?auswahl=1-3',
      hasAttribute: name => name === attribute, getAttribute: () => null };
    const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; },
      target: { closest: selector => selector.includes('a[target=') ? link : null } };
    await u.emit('click', event);
    assert.deepEqual(u.calls, [['openFile', { url: link.href }]]);
    assert.equal(event.defaultPrevented, true);
  }
});

test('Druckdialog doLocal: gemeinsame PDF und UAS-Pfade gehen über openUrl an NativeFiles', async () => {
  const template = readFileSync(new URL('../../app/templates/_print_dialog.html', import.meta.url), 'utf8');
  const helper = template.slice(template.indexOf('  function localUrlFor(c)'), template.indexOf('  function showTarget(t)'));
  const u = mitDateiPlugin();
  const context = { window: u.window, URLSearchParams };
  vm.runInNewContext(helper + '\nwindow.testDoLocal = doLocal;', context);
  // doLocal selbst wartet nicht auf das Promise; der Plugin-Aufruf erfolgt synchron.
  u.window.testDoLocal({ document_type: 'einsatzinfo', incident_id: 42 });
  u.window.testDoLocal({ localUrl: '/uas/einsatz/9/pdf/gesamt' });
  assert.deepEqual(u.calls, [
    ['openFile', { url: 'https://ecp.test/druck/dokument.pdf?document_type=einsatzinfo&incident_id=42' }],
    ['openFile', { url: 'https://ecp.test/uas/einsatz/9/pdf/gesamt' }],
  ]);
});

test('Normale neue Tabs nutzen weiterhin Browser und den Same-Origin-Handoff', async () => {
  const opened = [], paths = [];
  const u = umgebung({ fetchMock: async (_url, options) => {
    paths.push(JSON.parse(options.body).path);
    return { ok: true, json: async () => ({ url: 'https://ecp.test/kontakte?nt=neu' }) };
  } });
  u.window.Capacitor = { isNativePlatform: () => true, Plugins: {
    Browser: { open: async ({ url }) => opened.push(url) },
    NativeFiles: { openFile: () => assert.fail('Kein Dokument') },
  } };
  await u.emit('click', downloadKlick('https://ecp.test/kontakte', 'target="_blank"'));
  assert.deepEqual(paths, ['/kontakte']);
  assert.deepEqual(opened, ['https://ecp.test/kontakte?nt=neu']);
});

test('Viewer-defaultPrevented: Capture-Dateiaktion wird vom geladenen Viewer nicht erneut geöffnet', async () => {
  const u = mitDateiPlugin();
  const link = { href: 'https://ecp.test/plan.pdf',
    hasAttribute: () => false, getAttribute: () => null,
    closest() { assert.fail('Viewer darf den bereits behandelten Klick nicht verarbeiten'); } };
  const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; },
    target: { closest: () => link } };
  await u.emit('click', event);
  assert.equal(event.defaultPrevented, true);
  assert.deepEqual(u.calls, [['openFile', { url: link.href }]]);
});

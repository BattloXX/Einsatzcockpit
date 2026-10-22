// APP-02: Verbindungs-Banner (conn-status.js) zeigt Unterbrechung samt Stand und loest genau einen Resync aus.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const quelle = readFileSync(new URL("../../app/static/js/conn-status.js", import.meta.url), "utf8");

function umgebung() {
  const listener = new Map();
  const dispatched = [];
  const angehaengt = [];
  const window = {
    addEventListener(name, fn) { listener.set(name, [...(listener.get(name) || []), fn]); },
    dispatchEvent(event) {
      dispatched.push(event);
      (listener.get(event.type) || []).forEach((fn) => fn(event));
    },
  };
  const document = {
    readyState: "complete",
    addEventListener(name, fn) { listener.set(name, [...(listener.get(name) || []), fn]); },
    createElement() { return { style: {}, setAttribute() {}, parentNode: null }; },
    body: {
      appendChild(el) { el.parentNode = document.body; angehaengt.push(el); },
      removeChild(el) { el.parentNode = null; angehaengt.splice(angehaengt.indexOf(el), 1); },
    },
  };
  const navigator = { onLine: true };
  let zeit = 1_000_000;
  const timers = [];
  const DateStub = class extends Date {
    static now() { return zeit; }
    constructor(...args) { super(...(args.length ? args : [zeit])); }
  };
  vm.runInNewContext(quelle, {
    window, document, navigator, Date: DateStub, console,
    CustomEvent: class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    sessionStorage: { getItem() { return null; }, setItem() {} },
    setTimeout(fn) { timers.push(fn); return timers.length; }, clearTimeout() {},
  });
  const sende = (name, detail) => window.dispatchEvent({ type: name, detail });
  return { sende, dispatched, angehaengt, navigator, vor(ms) { zeit += ms; }, timers, window };
}

test("offline zeigt Banner mit Stand, online blendet aus und loest keinen zweiten Resync aus", () => {
  const u = umgebung();
  u.navigator.onLine = false;
  u.sende("offline");
  assert.equal(u.angehaengt.length, 1);
  assert.match(u.angehaengt[0].textContent, /Keine Verbindung zum Einsatzcockpit · Stand /);
  u.navigator.onLine = true;
  u.sende("online");
  assert.equal(u.angehaengt.length, 0);
  // app.js loest bei "online" den Resync aus; conn-status darf nichts zusaetzlich senden
  assert.equal(u.dispatched.filter((e) => e.type === "ec:resync").length, 0);
});

test("kurzer WebSocket-Abriss bleibt unsichtbar, laengerer wird angezeigt und einmalig nachgeladen", () => {
  const u = umgebung();
  u.sende("ec:ws-state", { open: false });
  u.vor(2000);
  u.timers.forEach((fn) => fn());
  assert.equal(u.angehaengt.length, 0);
  u.sende("ec:ws-state", { open: true });
  assert.equal(u.dispatched.filter((e) => e.type === "ec:resync").length, 0);

  u.sende("ec:ws-state", { open: false });
  u.vor(9000);
  u.timers.forEach((fn) => fn());
  assert.equal(u.angehaengt.length, 1);
  assert.match(u.angehaengt[0].textContent, /Verbindung unterbrochen · Stand /);
  u.sende("ec:ws-state", { open: true });
  assert.equal(u.angehaengt.length, 0);
  const resyncs = u.dispatched.filter((e) => e.type === "ec:resync");
  assert.equal(resyncs.length, 1);
  assert.equal(resyncs[0].detail.source, "ws-reconnect");
});

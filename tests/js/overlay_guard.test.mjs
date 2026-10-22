// ECP-06: overlay-guard.js raeumt leere/haengende Overlays auf, ohne Eingaben zu verwerfen.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const quelle = readFileSync(new URL("../../app/static/js/overlay-guard.js", import.meta.url), "utf8");

function umgebung({ offen = true, text = "", dirty = false, breite = 800, menueOffen = true, mitInhalt = false } = {}) {
  const listener = new Map();
  const protokoll = { geschlossen: 0 };
  const body = {
    textContent: text,
    dataset: dirty ? { dirty: "1" } : {},
    querySelector: () => (mitInhalt ? {} : null),
  };
  const modal = {
    open: offen,
    querySelector: (s) => (s === "#cardDetailBody" ? body : null),
    close() { this.open = false; protokoll.geschlossen += 1; },
    getBoundingClientRect: () => ({ left: 100, right: 500, top: 100, bottom: 400 }),
  };
  const appState = { mobileMenuOpen: menueOffen };
  const document = {
    hidden: false,
    getElementById: (id) => (id === "cardDetailModal" ? modal : null),
    querySelector: (s) => (s === '[x-data="appState()"]' ? {} : null),
    addEventListener: (n, fn) => listener.set(n, [...(listener.get(n) || []), fn]),
  };
  const window = {
    innerWidth: breite,
    Alpine: { $data: () => appState },
    addEventListener: (n, fn) => listener.set("w:" + n, [...(listener.get("w:" + n) || []), fn]),
  };
  vm.runInNewContext(quelle, { window, document });
  const ausloesen = (name, ev = {}) => (listener.get(name) || []).forEach((fn) => fn(ev));
  return { modal, appState, protokoll, ausloesen, window };
}

test("leeres offenes Detail-Modal wird nach Rueckkehr in die App geschlossen", () => {
  const u = umgebung({ text: "   " });
  u.ausloesen("visibilitychange");
  assert.equal(u.modal.open, false);
});

test("Modal mit Inhalt oder Eingabefeldern bleibt offen", () => {
  const mitText = umgebung({ text: "Auftrag Details" });
  mitText.ausloesen("htmx:afterSettle");
  assert.equal(mitText.modal.open, true);
  const mitFeld = umgebung({ text: "", mitInhalt: true });
  mitFeld.ausloesen("htmx:afterSettle");
  assert.equal(mitFeld.modal.open, true);
});

test("Tipp auf die abgedunkelte Flaeche schliesst das Modal, Tipp ins Modal nicht", () => {
  const u = umgebung({ text: "Details" });
  u.ausloesen("click", { target: u.modal, clientX: 10, clientY: 10 });
  assert.equal(u.modal.open, false);
  const v = umgebung({ text: "Details" });
  v.ausloesen("click", { target: v.modal, clientX: 200, clientY: 200 });
  assert.equal(v.modal.open, true);
});

test("ungespeicherte Eingaben werden durch einen Tipp auf die Flaeche nicht verworfen", () => {
  const u = umgebung({ text: "Details", dirty: true });
  u.ausloesen("click", { target: u.modal, clientX: 10, clientY: 10 });
  assert.equal(u.modal.open, true);
});

test("Mobil-Menue wird bei breitem Fenster zurueckgesetzt, bei schmalem nicht", () => {
  const breit = umgebung({ text: "x", breite: 1024 });
  breit.ausloesen("w:resize");
  assert.equal(breit.appState.mobileMenuOpen, false);
  const schmal = umgebung({ text: "x", breite: 800 });
  schmal.ausloesen("w:resize");
  assert.equal(schmal.appState.mobileMenuOpen, true);
});

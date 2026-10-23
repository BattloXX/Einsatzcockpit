// ECP-03: Tooltip mit vollem Namen nur bei abgeschnittenem Titel.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const quelle = readFileSync(new URL("../../app/static/js/titel-tooltips.js", import.meta.url), "utf8");

function element(text, { breit, sichtbar }) {
  const attribute = new Map();
  const inner = { textContent: text, scrollWidth: breit, clientWidth: sichtbar };
  return {
    firstElementChild: inner,
    textContent: text,
    attribute,
    setAttribute: (k, v) => attribute.set(k, v),
    getAttribute: (k) => attribute.get(k),
    hasAttribute: (k) => attribute.has(k),
    removeAttribute: (k) => attribute.delete(k),
  };
}

function laden(elemente) {
  const kontext = {
    window: { addEventListener() {} },
    document: { addEventListener() {}, querySelectorAll: () => elemente },
    requestAnimationFrame: (fn) => fn(),
  };
  vm.runInNewContext(quelle, kontext);
  return kontext.window.ECTitelTooltips;
}

test("abgeschnittener Titel erhaelt Tooltip mit vollem Namen und Tastaturfokus", () => {
  const lang = element("Einsatzabschnitt Nord - Bereitstellungsraum", { breit: 320, sichtbar: 120 });
  laden([lang]).aktualisiere();
  assert.equal(lang.getAttribute("data-tip"), "Einsatzabschnitt Nord - Bereitstellungsraum");
  assert.equal(lang.getAttribute("aria-label"), "Einsatzabschnitt Nord - Bereitstellungsraum");
  assert.equal(lang.getAttribute("tabindex"), "0");
});

test("vollstaendig sichtbarer Titel hat keinen Tooltip und ein veralteter wird entfernt", () => {
  const kurz = element("Aufträge", { breit: 80, sichtbar: 120 });
  kurz.setAttribute("data-tip", "alt");
  kurz.setAttribute("aria-label", "alt");
  laden([kurz]).aktualisiere();
  assert.equal(kurz.hasAttribute("data-tip"), false);
  assert.equal(kurz.hasAttribute("aria-label"), false);
});

test("umbenannter Titel wird bei der naechsten Aktualisierung uebernommen", () => {
  const el = element("Alt", { breit: 300, sichtbar: 100 });
  const api = laden([el]);
  api.aktualisiere();
  el.firstElementChild.textContent = "Neuer sehr langer Name";
  api.aktualisiere();
  assert.equal(el.getAttribute("data-tip"), "Neuer sehr langer Name");
});

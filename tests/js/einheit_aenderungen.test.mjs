import assert from "node:assert/strict";
import test from "node:test";

import { istGeaendert, vergleicheZustand } from "../../app/static/js/einheit_aenderungen.js";

const auftrag = (dispatch_id, priority = "normal") => ({ dispatch_id, bezeichnung: `Auftrag ${dispatch_id}`, priority });

test("vergleicheZustand erkennt einen Wechsel des nächsten Auftrags nur mit vorherigem Auftrag", () => {
  const vorher = { naechster_dispatch_id: 2, kategorien: { weitere: [auftrag(2), auftrag(3)] } };
  const nachher = { naechster_dispatch_id: 3, kategorien: { weitere: [auftrag(3), auftrag(2)] } };
  assert.equal(vergleicheZustand(vorher, nachher).reihenfolgeGeaendert, true);
  assert.equal(vergleicheZustand({}, nachher).reihenfolgeGeaendert, false);
});

test("vergleicheZustand erkennt nur neue Aufträge mit höherer Priorität bei laufendem Auftrag", () => {
  const vorher = { kategorien: { aktuell: auftrag(1, "normal"), weitere: [auftrag(2, "normal")] } };
  const dringend = { kategorien: { aktuell: auftrag(1, "normal"), weitere: [auftrag(2), auftrag(3, "dringend")] } };
  const gleich = { kategorien: { aktuell: auftrag(1, "normal"), weitere: [auftrag(2), auftrag(3, "normal")] } };
  const ohneLaufend = { kategorien: { weitere: [auftrag(3, "sofort")] } };
  assert.deepEqual(vergleicheZustand(vorher, dringend).hoeherePrioritaet.map((item) => item.dispatch_id), [3]);
  assert.deepEqual(vergleicheZustand(vorher, gleich).hoeherePrioritaet, []);
  assert.deepEqual(vergleicheZustand({}, ohneLaufend).hoeherePrioritaet, []);
});

test("vergleicheZustand erkennt zurückgezogene zuvor offene Aufträge", () => {
  const vorher = { kategorien: { weitere: [auftrag(2)] } };
  const nachher = { kategorien: { weitere: [], zurueckgezogen: [auftrag(2)] } };
  assert.deepEqual(vergleicheZustand(vorher, nachher).zurueckgezogen.map((item) => item.dispatch_id), [2]);
});

test("istGeaendert vergleicht Auftragsversionen", () => {
  assert.equal(istGeaendert({ version: 3 }, 2), true);
  assert.equal(istGeaendert({ version: 2 }, 2), false);
  assert.equal(istGeaendert({ version: 1 }, null), false);
});

test("Fortschritt ist keine Umreihung: bisheriger nächster läuft jetzt", () => {
  const vorher = { naechster_dispatch_id: 1, kategorien: { aktuell: null, weitere: [{ dispatch_id: 1 }, { dispatch_id: 2 }] } };
  const nachher = { naechster_dispatch_id: 2, kategorien: { aktuell: { dispatch_id: 1 }, weitere: [{ dispatch_id: 2 }] } };
  assert.equal(vergleicheZustand(vorher, nachher).reihenfolgeGeaendert, false);
});

test("Umreihung durch die Leitstelle wird erkannt", () => {
  const vorher = { naechster_dispatch_id: 1, kategorien: { weitere: [{ dispatch_id: 1 }, { dispatch_id: 2 }] } };
  const nachher = { naechster_dispatch_id: 2, kategorien: { weitere: [{ dispatch_id: 2, bezeichnung: "B" }, { dispatch_id: 1 }] } };
  const ergebnis = vergleicheZustand(vorher, nachher);
  assert.equal(ergebnis.reihenfolgeGeaendert, true);
  assert.equal(ergebnis.naechster.bezeichnung, "B");
});

import test from "node:test";
import assert from "node:assert/strict";
import { auftragsfuehrung } from "../../app/static/js/einheit_ablauf.js";

const auftrag = (status, erlaubt, extra = {}) => ({
  auftragsart: "abarbeitung", einheit_status: status, erlaubte_status: erlaubt, phase: "disponiert", ...extra,
});

test("geführte Abarbeitung wählt den nächsten vom Backend erlaubten Schritt", () => {
  for (const [von, nach, text] of [
    ["zugewiesen", "bestaetigt", "Auftrag übernehmen"], ["bestaetigt", "anfahrt", "Anfahrt melden"],
    ["anfahrt", "vor_ort", "Vor Ort melden"], ["vor_ort", "in_arbeit", "In Arbeit setzen"],
    ["in_arbeit", "abgeschlossen", "Fertig melden"],
  ]) {
    const aktion = auftragsfuehrung(auftrag(von, ["nicht_durchfuehrbar", nach]));
    assert.equal(aktion.status, nach);
    assert.equal(aktion.text, text);
    assert.equal(aktion.art, "status");
  }
});

test("Erkundungsergebnis ist eine UI-Aktion vor dem erlaubten Abschluss, kein neuer Status", () => {
  const aktion = auftragsfuehrung(auftrag("vor_ort", ["bestaetigt", "abgeschlossen"], { auftragsart: "erkundung" }));
  assert.equal(aktion.art, "ergebnis");
  assert.equal(aktion.text, "Erkundungsergebnis erfassen");
  assert.equal(aktion.status, "abgeschlossen");
  assert.equal(auftragsfuehrung(auftrag("in_arbeit", [], { auftragsart: "erkundung" })).art, "pruefen");
});

test("UI-Reihenfolge ersetzt keine Freigabe und lässt erlaubte direkte Sprünge zu", () => {
  assert.equal(auftragsfuehrung(auftrag("zugewiesen", ["vor_ort"])).status, "vor_ort");
  const gesperrt = auftragsfuehrung(auftrag("vor_ort", ["bestaetigt", "nicht_durchfuehrbar"], { phase: "erkundung" }));
  assert.equal(gesperrt.art, "warten");
  assert.equal(gesperrt.text, "Freigabe ausstehend");
  assert.equal(gesperrt.status, null);
});

test("keine Teilmenge der Serveraktionen ermöglicht einen nicht freigegebenen Übergang", () => {
  const werte = ["bestaetigt", "anfahrt", "vor_ort", "in_arbeit", "abgeschlossen", "nicht_durchfuehrbar"];
  for (const art of ["abarbeitung", "erkundung"]) for (const von of ["zugewiesen", ...werte]) {
    for (let maske = 0; maske < 2 ** werte.length; maske++) {
      const erlaubt = werte.filter((_, i) => maske & (1 << i));
      const aktion = auftragsfuehrung(auftrag(von, erlaubt, { auftragsart: art }));
      assert.ok(aktion.status === null || erlaubt.includes(aktion.status));
      if (art === "erkundung") assert.notEqual(aktion.status, "in_arbeit");
    }
  }
});

test("Warten, Abschluss, Rückzug, Lesezugang, Konflikt und unbekannte Daten bleiben eindeutig", () => {
  assert.equal(auftragsfuehrung(null).art, "warten");
  assert.equal(auftragsfuehrung(auftrag("bestaetigt", ["anfahrt"]), { ausstehend: "anfahrt" }).text, "Übermittlung ausstehend");
  assert.equal(auftragsfuehrung(auftrag("zugewiesen", ["bestaetigt"]), { schreibbar: false }).text, "Nur lesen");
  assert.equal(auftragsfuehrung(auftrag("zugewiesen", ["bestaetigt"]), { konflikt: true }).art, "pruefen");
  assert.equal(auftragsfuehrung(auftrag("zugewiesen", ["bestaetigt"], { withdrawn_at: "2026-10-24" })).art, "warten");
  assert.equal(auftragsfuehrung(auftrag("abgeschlossen", [])).text, "Fertig gemeldet");
  assert.equal(auftragsfuehrung(auftrag("abgeschlossen", [], { auftragsart: "erkundung" })).text, "Erkundet");
  for (const art of [null, undefined, "", "__proto__", "constructor"]) {
    assert.equal(auftragsfuehrung(auftrag("zugewiesen", ["bestaetigt"], { auftragsart: art })).art, "pruefen");
  }
  assert.equal(auftragsfuehrung(auftrag("zugewiesen", null)).art, "pruefen");
});

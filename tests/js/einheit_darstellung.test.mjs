import test from "node:test";
import assert from "node:assert/strict";
import { auftragsartAnzeige, prioritaetAnzeige } from "../../app/static/js/einheit_darstellung.js";

test("vorhandene Auftragsarten haben genau eine gemeinsame Anzeige", () => {
  assert.deepEqual(auftragsartAnzeige("erkundung"), {
    label: "ERKUNDUNG", icon: "search", klasse: "einheit-art--erkundung",
  });
  assert.deepEqual(auftragsartAnzeige("abarbeitung"), {
    label: "AUFTRAG", icon: "tools", klasse: "einheit-art--abarbeitung",
  });
});

test("fehlende, falsche und prototypgleiche Werte werden nicht umklassifiziert", () => {
  for (const wert of [undefined, null, "", "unbekannt", "ERKUNDUNG", "__proto__", "constructor", 0, false, {}]) {
    assert.deepEqual(auftragsartAnzeige(wert), {
      label: "ART UNBEKANNT", icon: "help-circle", klasse: "einheit-art--unbekannt",
    });
  }
  assert.equal(auftragsartAnzeige({ auftrag: "Erkundung", phase: "erkundung" }).label, "ART UNBEKANNT");
});

test("Prioritäten bleiben unabhängig und fehlende Priorität bleibt neutral", () => {
  const klassen = ["red", "orange", "yellow", "muted"];
  for (const [index, prio] of ["sofort", "dringend", "normal", "aufschiebbar"].entries()) {
    assert.equal(prioritaetAnzeige(prio).chipKlasse, `einheit-chip--${klassen[index]}`);
    assert.equal(prioritaetAnzeige(prio).klasse, `einheit-prio--${prio}`);
  }
  for (const wert of [null, undefined, "", "erkundung", "__proto__"]) {
    assert.equal(prioritaetAnzeige(wert).klasse, "einheit-prio--keine");
    assert.equal(prioritaetAnzeige(wert).label, "ohne Priorität");
  }
});

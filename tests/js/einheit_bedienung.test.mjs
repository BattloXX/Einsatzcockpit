import test from "node:test";
import assert from "node:assert/strict";
import { gefahrenHinweise } from "../../app/static/js/einheit_bedienung.js";

test("Gefahrenübersicht bewahrt Umgebungs- und Objekthinweise ohne neue Klassifikation", () => {
  const detail = { gefahren: [{ id: 4, title: "Stromleitung", type_label: "Strom" }], objekte: [
    { id: 9, bezeichnung: "Werkstatt", gefahren: [{ name: "Gasflaschen", un_nummer: "1978", detail: "Im Keller" }] },
  ] };
  const vorher = JSON.stringify(detail);
  assert.deepEqual(gefahrenHinweise(detail), [
    { key: "lage:4", titel: "Stromleitung", text: "Strom", quelle: "Umgebung" },
    { key: "objekt:9:0", titel: "Gasflaschen · UN 1978", text: "Im Keller", quelle: "Werkstatt" },
  ]);
  assert.equal(JSON.stringify(detail), vorher);
});

test("leere, ältere und gleichnamige Gefahrendaten bleiben benutzbar und eindeutig", () => {
  assert.deepEqual(gefahrenHinweise(null), []);
  assert.deepEqual(gefahrenHinweise({}), []);
  const liste = gefahrenHinweise({ gefahren: [{ title: "Gefahr" }, { title: "Gefahr" }],
    objekte: [{ id: 1 }, { id: 2, gefahren: [{ name: "Gefahr" }, { name: "Gefahr" }] }] });
  assert.equal(new Set(liste.map(g => g.key)).size, 4);
  assert.equal(liste.length, 4); // Keine Gefahren aufgrund gleichlautender Namen ausblenden.
});

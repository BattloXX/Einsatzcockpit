import test from "node:test";
import assert from "node:assert/strict";
import { kartenziel } from "../../app/static/js/einheit_kartenziel.js";

const ziel = (id = 1, extra = {}) => ({ dispatch_id: id, lat: 47, lng: 9, gefahren: [], ...extra });
function modell(auftraege = [ziel(), ziel(2)]) {
  const cache = {};
  const m = { ...kartenziel(), lageId: 10, einheitId: 20, kartenAuftraege: auftraege, fokusAuftrag: auftraege[0],
    headers: () => ({}), uebersichtKarte: { hinweise: () => {} },
    outbox: { metaLaden: async key => cache[key], metaSpeichern: async (key, data) => { cache[key] = data; } },
  };
  Object.defineProperties(m, {
    kartenZiel: { get() { return this.ermittleKartenZiel(); } },
    kartenHinweise: { get() { return this.ermittleKartenHinweise(); } },
  });
  return { m, cache };
}
const antwort = (id, titel = "Sperre") => ({ ok: true, status: 200, headers: new Headers(),
  json: async () => ({ auftrag: { dispatch_id: id }, strassensperren_status: "verfuegbar",
    strassensperren: [{ id, title: titel, restriction_label: "Vollsperre" }] }),
});

test("verspätete Antworten und Cache-Schreibvorgänge bleiben beim gewählten Auftrag", async () => {
  const { m, cache } = modell();
  let fertig;
  const original = globalThis.fetch;
  globalThis.fetch = url => url.endsWith("/1") ? new Promise(resolve => { fertig = resolve; }) : Promise.resolve(antwort(2));
  try {
    const alt = m.kartenZielLaden();
    m.karteAuswahlId = 2;
    await m.kartenZielLaden();
    fertig(antwort(1, "Falsche Sperre"));
    await alt;
    assert.equal(m.kartenDaten.dispatch_id, 2);
    assert.equal(m.kartenHinweise.strassensperren[0].title, "Sperre");
    assert.equal(cache[m.kartenCacheKey()][1], undefined);
    assert.equal(m.kartenLaden, false);
  } finally { globalThis.fetch = original; }
});

test("Offline-Cache verlangt eigene Lage, Einheit, Auftrag und unverändertes Ziel", async () => {
  const { m, cache } = modell();
  cache[m.kartenCacheKey()] = { 1: { dispatch_id: 1, lat: 47, lng: 9,
    strassensperren: [{ title: "Bekannt" }], strassensperren_status: "verfuegbar" } };
  const original = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("offline"); };
  try {
    await m.kartenZielLaden(true);
    assert.equal(m.kartenDaten.offline, true);
    assert.match(m.ermittleKartenSperrenText(), /letzten geladenen Stand/);
    m.kartenAuftraege[0].lat = 48;
    assert.equal(m.kartenHinweise.strassensperren, undefined);
    await m.kartenZielLaden(true);
    assert.equal(m.kartenDaten, null);
    m.karteAuswahlId = 2;
    await m.kartenZielLaden();
    assert.equal(m.kartenDaten, null);
    m.einheitId = 21;
    m.karteAuswahlId = 1;
    await m.kartenZielLaden(true);
    assert.equal(m.kartenDaten, null);
  } finally { globalThis.fetch = original; }
});

test("401/403/404 greifen nicht auf Offline-Daten zurück", async () => {
  const original = globalThis.fetch;
  try {
    for (const status of [401, 403, 404]) {
      const { m, cache } = modell();
      cache[m.kartenCacheKey()] = { 1: { dispatch_id: 1, lat: 47, lng: 9, strassensperren: [{ title: "Alt" }] } };
      globalThis.fetch = async () => ({ ok: false, status });
      await m.kartenZielLaden();
      assert.equal(m.kartenDaten, null);
      assert.equal(cache[m.kartenCacheKey()], null);
      assert.equal(m.kartenLaden, false);
    }
  } finally { globalThis.fetch = original; }
});

test("Zugriffsentzug verwendet auch bei einem Cache-Schreibfehler keine gespeicherten Hinweise", async () => {
  const { m, cache } = modell();
  cache[m.kartenCacheKey()] = { 1: { dispatch_id: 1, lat: 47, lng: 9, strassensperren: [{ title: "Alt" }] } };
  m.outbox.metaSpeichern = async () => { throw new Error("Speicher nicht verfügbar"); };
  const original = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 403 });
  try {
    await m.kartenZielLaden();
    assert.equal(m.kartenDaten, null);
    assert.equal(m.kartenHinweise.strassensperren, undefined);
  } finally { globalThis.fetch = original; }
});

test("zurückgezogene oder nicht passende Detailantwort löscht alte Zielhinweise", async () => {
  const original = globalThis.fetch;
  try {
    for (const auftrag of [{ dispatch_id: 1, withdrawn_at: "2026-10-24" }, { dispatch_id: 2 }]) {
      const { m } = modell();
      m.kartenDaten = { dispatch_id: 1, lat: 47, lng: 9, strassensperren: [{ title: "Alt" }] };
      globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ auftrag }) });
      await m.kartenZielLaden();
      assert.equal(m.kartenDaten, null);
      assert.equal(m.kartenHinweise.strassensperren, undefined);
    }
  } finally { globalThis.fetch = original; }
});

test("Zurückziehen und Zugangsende entfernen die Auswahl, Zustand liefert aktuelle Gefahren", () => {
  const { m } = modell();
  m.karteAuswahlId = 2;
  m.kartenDaten = { dispatch_id: 2, lat: 47, lng: 9, gefahren: [{ title: "Veraltet" }] };
  m.kartenAuftraege[1].gefahren = [{ title: "Aktuelle Gefahr" }];
  assert.equal(m.kartenHinweise.gefahren[0].title, "Aktuelle Gefahr");
  m.kartenAuftraege.pop();
  assert.equal(m.kartenZiel.dispatch_id, 1);
  assert.deepEqual(m.kartenHinweise.gefahren, []);
  m.zugangBeendet = true;
  assert.equal(m.kartenZiel, null);
  assert.equal(m.kartenHinweise, null);
});

import test from "node:test";
import assert from "node:assert/strict";
import { meldungsverlauf, verbindeEintraege, vergleicheEintraege } from "../../app/static/js/einheit_verlauf.js";

const auftraege = [
  { dispatch_id: 10, site_id: 3, bezeichnung: "Schule", auftragsart: "erkundung", auftrag: "Keller prüfen" },
  { dispatch_id: 11, site_id: 3, bezeichnung: "Schule", auftragsart: "abarbeitung", auftrag: "Keller auspumpen" },
  { dispatch_id: 12, site_id: 4, bezeichnung: "B", auftragsart: "abarbeitung", auftrag: "Zufahrt prüfen" },
];
const eintrag = (id, ts = "2026-10-24T10:00:00.000000+00:00") => ({ id: `1:${id}`, ts, text: `Meldung ${id}`, eigene: true });
const lokal = (uuid, werte = {}) => ({ client_uuid: uuid, einheit_id: 5, dispatch_id: 10,
  erfasst_at: "2026-10-24T10:00:00.000Z", typ: "meldung", status: "fehler_netz", payload: { text: "Lokal" }, ...werte });

function kontext(outbox = {}) {
  const ctx = { ...meldungsverlauf({ lage_id: 2, einheit_id: 5 }),
    headers: () => ({}), outboxListe: [], verlauf: [], meldungsZiele: auftraege,
    outboxStatus: () => "Übertragung fehlgeschlagen", zugangBeendet: false, outbox: {
      metaLaden: async () => null, metaSpeichern: async () => {}, ...outbox,
    },
  };
  return ctx;
}

function antwort(rows, cursor = null, status = 200) {
  return new Response(JSON.stringify({ eintraege: rows, cursor, einheit_id: 5, lage_id: 2 }), { status });
}

test("Serverbestätigung ersetzt lokale Meldung und Receipt anhand UUID oder Entity", () => {
  const server = [{ ...eintrag(8), client_uuid: "a" }];
  const receipt = lokal("a", { server_bestaetigt_at: "2026-10-24T10:01:00Z", antwort: { entity_type: "site_log", entity_id: 8 } });
  const rows = verbindeEintraege(server, [lokal("a")], [receipt], auftraege, 5);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].id, "1:8");
  assert.equal(verbindeEintraege([{ ...eintrag(8), client_uuid: null }], [], [receipt], auftraege, 5).length, 1);
});

test("Lokale Zuordnung bleibt exakt; Einsatzstellenansicht darf beide eigenen Aufträge derselben Stelle zeigen", () => {
  const rows = verbindeEintraege([], [lokal("a"), lokal("b", { dispatch_id: 11 }),
    lokal("fremd", { einheit_id: 9 }), lokal("andere-stelle", { dispatch_id: 12 }),
    lokal("unbekannter-auftrag", { dispatch_id: 99 })], [], auftraege, 5, { dispatchId: 10 });
  assert.deepEqual(rows.map(r => r.client_uuid), ["a", "b"]);
  assert.deepEqual(rows.map(r => r.dispatch_id), [10, 11]);
});

test("Normaler Status bleibt in der Zentrale kompakt ausgeblendet, fehlgeschlagener Status bleibt sichtbar", () => {
  const normal = lokal("a", { typ: "status", payload: { status: "anfahrt" }, status: "ausstehend" });
  const fehler = lokal("b", { typ: "status", payload: { status: "vor_ort" }, status: "fehler" });
  assert.deepEqual(verbindeEintraege([], [normal, fehler], [], auftraege, 5).map(r => r.text), ["Vor Ort"]);
  assert.equal(verbindeEintraege([], [normal, fehler], [], auftraege, 5, { mitStatus: true }).length, 2);
});

test("Gleiche Zeitpunkte werden numerisch und mit Mikrosekunden stabil geordnet", () => {
  const rows = [eintrag(12), eintrag(2), eintrag(4, "2026-10-24T10:00:00.000001+00:00")].sort(vergleicheEintraege);
  assert.deepEqual(rows.map(r => r.id), ["1:2", "1:12", "1:4"]);
  assert.equal(vergleicheEintraege(eintrag(2), eintrag(2, "2026-10-24T10:00:00.000Z")), 0);
});

test("Erfassungszeit und Schnellmeldungsinhalt bleiben beim bestätigten Receipt erhalten", () => {
  const receipt = lokal("a", { payload: { schnellmeldung: "akute_gefahr", schnellmeldung_label: "Akute Gefahr festgestellt" },
    server_bestaetigt_at: "2026-10-24T11:00:00Z" });
  const [row] = verbindeEintraege([], [], [receipt], auftraege, 5);
  assert.equal(row.ts, receipt.erfasst_at);
  assert.equal(row.text, "Akute Gefahr festgestellt");
  assert.equal(row.hervorhebung, "gefahr");
  assert.equal(row.status, "uebermittelt");
});

test("Nachladen und Live-Aktualisieren erhalten ältere Einträge mit identischem Zeitstempel", async () => {
  const original = globalThis.fetch;
  const script = [antwort([eintrag(30), eintrag(29)], "cursor1"), antwort([eintrag(28), eintrag(27)], "cursor2"),
    antwort([eintrag(31), eintrag(30)], "cursor3")];
  globalThis.fetch = async () => script.shift();
  try {
    const c = kontext();
    await c.verlaufLaden(10);
    await c.verlaufLaden(10, true);
    await c.verlaufLaden(10);
    assert.deepEqual(c.verlaufStand(10).eintraege.map(r => r.id), ["1:31", "1:30", "1:29", "1:28", "1:27"]);
    assert.equal(c.verlaufStand(10).cursor, "cursor2");
  } finally { globalThis.fetch = original; }
});

test("Netzausfall lädt den vorhandenen kontextbezogenen Cache, 403 löscht ihn ohne Fallback", async () => {
  const original = globalThis.fetch;
  let gespeicherterCache = { "auftrag:10": { eintraege: [eintrag(1)], cursor: null } };
  const c = kontext({ metaLaden: async key => {
    assert.equal(key, "meldungsverlauf:2:5"); return gespeicherterCache;
  }, metaSpeichern: async (_, wert) => { gespeicherterCache = wert; } });
  try {
    globalThis.fetch = async () => { throw new Error("offline"); };
    await c.verlaufLaden(10);
    assert.equal(c.verlaufStand(10).offline, true);
    assert.equal(c.verlaufStand(10).eintraege.length, 1);
    globalThis.fetch = async () => antwort([], null, 403);
    await c.verlaufLaden(10);
    assert.equal(c.verlaufStand(10).eintraege.length, 0);
    assert.equal(gespeicherterCache["auftrag:10"], undefined);
  } finally { globalThis.fetch = original; }
});

test("Verspätete Antwort nach Widerruf darf Verlauf und Cache nicht wieder füllen", async () => {
  const original = globalThis.fetch;
  let freigeben, cache;
  globalThis.fetch = () => new Promise(resolve => { freigeben = resolve; });
  try {
    const c = kontext({ metaSpeichern: async (_, wert) => { cache = wert; } });
    const laden = c.verlaufLaden(10);
    await c.verlaufZugangBeenden();
    freigeben(antwort([eintrag(7)]));
    await laden;
    assert.deepEqual(c.meldungsVerlaeufe, {});
    assert.deepEqual(cache, {});
  } finally { globalThis.fetch = original; }
});

test("Abweichende Einheitenantwort wird nicht angezeigt oder gespeichert", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ einheit_id: 9, lage_id: 2, eintraege: [eintrag(9)] }));
  try {
    const c = kontext();
    await c.verlaufLaden();
    assert.equal(c.verlaufStand().eintraege.length, 0);
  } finally { globalThis.fetch = original; }
});

test("Fehlender Offline-Speicher ändert einen erfolgreichen Abruf nicht in einen Netzausfall", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => antwort([eintrag(1)]);
  try {
    const c = kontext({ metaSpeichern: async () => { throw new Error("QuotaExceededError"); } });
    await c.verlaufLaden();
    assert.equal(c.verlaufStand().offline, false);
    assert.equal(c.verlaufStand().eintraege.length, 1);
    assert.match(c.verlaufStand().fehler, /Offline-Speicherung/);
  } finally { globalThis.fetch = original; }
});

test("Kennzeichnung und Inhalt werden ohne redundante Überschrift angezeigt", () => {
  const c = kontext();
  assert.equal(c.verlaufInhalt({ hervorhebung: "auftrag", text: "Neuer Auftrag" }), "");
  assert.equal(c.verlaufInhalt({ hervorhebung: "unterstuetzung", text: "Unterstützung benötigt\nPumpe" }), "Pumpe");
  assert.equal(c.verlaufInhalt({ hervorhebung: "gefahr", text: "Akute Gefahr festgestellt\nGas" }), "Akute Gefahr festgestellt\nGas");
});

test("Alte unverbundene Empfangsquittungen werden nicht als zweite historische Meldung erfunden", () => {
  const receipt = { typ: "meldung", dispatch_id: 10, client_uuid: "alt", server_bestaetigt_at: "2026-10-24T10:00:00Z",
    antwort: { entity_type: "site_log", entity_id: null, meldung_text: "Ergebnis" } };
  const rows = verbindeEintraege([eintrag(1)], [], [receipt], auftraege, 5);
  assert.equal(rows.length, 1);
});

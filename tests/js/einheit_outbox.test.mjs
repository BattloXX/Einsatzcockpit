import test from "node:test";
import assert from "node:assert/strict";
import { IDBFactory } from "fake-indexeddb";
import { createOutbox } from "../../app/static/js/einheit_outbox.js";

const ok = (aktion_id = 1, extra = {}) => new Response(JSON.stringify({ aktion_id, ...extra }), { status: 200, headers: { "Content-Type": "application/json" } });
const antwort = (status, body = {}, headers = {}) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

function umgebung(skript = [], optionen = {}) {
  let zeit = new Date("2026-01-01T10:00:00.000Z");
  const anfragen = [];
  const fetch = async (url, init) => {
    anfragen.push({ url, ...init });
    const naechste = skript.shift();
    if (naechste instanceof Error) throw naechste;
    if (typeof naechste === "function") return naechste(url, init);
    return naechste || ok();
  };
  const basis = { indexedDB: new IDBFactory(), fetch, now: () => zeit, uuid: (() => { let i = 0; return () => `00000000-0000-4000-8000-${String(++i).padStart(12, "0")}`; })(), ...optionen };
  return { basis, anfragen, vor: (ms) => { zeit = new Date(zeit.getTime() + ms); } };
}

async function leer() { await new Promise((resolve) => setTimeout(resolve, 0)); }
async function warteAuf(pruefung) {
  for (let i = 0; i < 500; i++) {
    if (await pruefung()) return;
    await leer();
  }
  throw new Error("asynchrone Aktion wurde nicht fertig");
}
async function eintrag(outbox, typ = "status", dispatch_id = 1, payload = { status: "anfahrt" }) { return outbox.erfassen({ typ, dispatch_id, einheit_id: 9, auftrag_version: 1, payload }); }

test("Eintrag ueberlebt Neustart und Sequenz bleibt monoton", async () => {
  const env = umgebung([new Error("offline")]);
  const a = createOutbox({ ...env.basis, dbName: "neustart" });
  await eintrag(a); await warteAuf(async () => (await a.liste())[0]?.status === "fehler_netz");
  const b = createOutbox({ ...env.basis, dbName: "neustart" });
  const alt = await b.liste();
  assert.equal(alt[0].status, "fehler_netz");
  const neu = await eintrag(b); assert.equal(neu.seq, 2);
});

test("unlesbares Foto verhindert das gesamte Erkundungspaket vor dem ersten Versand", async () => {
  const env = umgebung(); const o = createOutbox(env.basis);
  await assert.rejects(o.erfassenPaket([
    { typ: "meldung", dispatch_id: 1, payload: { text: "Ergebnis" } },
    { typ: "foto", dispatch_id: 1, blob: { arrayBuffer() { throw new Error("Datei nicht lesbar"); } } },
    { typ: "status", dispatch_id: 1, payload: { status: "abgeschlossen" } },
  ]), /Datei nicht lesbar/);
  assert.deepEqual(await o.liste(), []);
  assert.equal(env.anfragen.length, 0);
});

test("abgebrochene Paket-Transaktion hinterlässt weder Teilergebnis noch Abschluss", async () => {
  const env = umgebung([], { uuid: () => "gleiche-id", fetch: () => new Promise(() => {}) });
  const o = createOutbox(env.basis);
  await assert.rejects(o.erfassenPaket([
    { typ: "meldung", dispatch_id: 1 }, { typ: "status", dispatch_id: 1, payload: { status: "abgeschlossen" } },
  ]));
  assert.deepEqual(await o.liste(), []);
  const gespeichert = await o.erfassen({ typ: "status", dispatch_id: 1, payload: { status: "bestaetigt" } });
  assert.equal(gespeichert.seq, 1);
});

test("Ergebnis, Foto und Abschluss sind vor jedem POST vollständig gespeichert", async () => {
  const env = umgebung(); let o; let nummer = 0;
  const basis = { ...env.basis, fetch: async () => {
    assert.equal((await o.liste()).length + (await o.verlauf()).length, 3);
    return ok(++nummer);
  } };
  o = createOutbox(basis);
  const daten = { dispatch_id: 1, einheit_id: 9, auftrag_version: 2 };
  const paket = await o.erfassenPaket([
    { ...daten, typ: "meldung", payload: { text: "Ergebnis" } },
    { ...daten, typ: "foto", blob: new Blob(["foto"], { type: "image/jpeg" }) },
    { ...daten, typ: "status", payload: { status: "abgeschlossen", ausgangsstatus: "vor_ort" } },
  ]);
  assert.deepEqual(paket.map(item => item.seq), [1, 2, 3]);
  assert.equal(new Set(paket.map(item => item.client_uuid)).size, 3);
  await warteAuf(async () => (await o.verlauf()).length === 3);
});

test("paralleles Erfassen in zwei Tabs vergibt eindeutige Sequenzen", async () => {
  const env = umgebung([], { fetch: () => new Promise(() => {}) });
  const a = createOutbox(env.basis); const b = createOutbox(env.basis);
  await Promise.all([eintrag(a, "status", 1), eintrag(b, "status", 2)]);
  assert.deepEqual((await a.liste()).map(item => item.seq), [1, 2]);
});

test("blockierte oder haengende IndexedDB-Open-Anfragen werden mit Timeout abgebrochen", async () => {
  const blockiert = { open() { const request = {}; queueMicrotask(() => request.onblocked()); return request; } };
  const haengt = { open() { return {}; } };
  const env = umgebung();
  await assert.rejects(createOutbox({ ...env.basis, indexedDB: blockiert, openTimeoutMs: 20 }).zustandLaden(), /blockiert/);
  await assert.rejects(createOutbox({ ...env.basis, indexedDB: haengt, openTimeoutMs: 5 }).bereinigen(), /rechtzeitig/);
});

test("IndexedDB-Open wird nach Fehler erneut versucht", async () => {
  const echt = new IDBFactory();
  let opens = 0;
  const indexedDB = {
    open(...args) {
      opens += 1;
      if (opens > 1) return echt.open(...args);
      const request = { error: new Error("erste Oeffnung fehlgeschlagen") };
      queueMicrotask(() => request.onerror());
      return request;
    },
  };
  const env = umgebung();
  const outbox = createOutbox({ ...env.basis, indexedDB, openTimeoutMs: 20 });
  await assert.rejects(outbox.zustandLaden(), /erste Oeffnung/);
  assert.equal(await outbox.zustandLaden(), undefined);
  assert.equal(opens, 2);
});

test("Statusfolge bleibt je Auftrag nach Netzfehler streng geordnet", async () => {
  const env = umgebung([new Error("offline"), ok(1), ok(2), ok(3)]);
  const o = createOutbox(env.basis);
  await eintrag(o, "status", 4, { status: "anfahrt" }); await warteAuf(async () => (await o.liste())[0]?.status === "fehler_netz");
  await eintrag(o, "status", 4, { status: "vor_ort" }); await eintrag(o, "status", 4, { status: "in_arbeit" });
  await leer(); assert.equal(env.anfragen.length, 1);
  env.vor(2000); await o.flush();
  assert.deepEqual(env.anfragen.map((x) => JSON.parse(x.body).status), ["anfahrt", "anfahrt", "vor_ort", "in_arbeit"]);
});

test("haengendes Foto blockiert die Statusspur nicht", async () => {
  let fotoGestartet;
  const env = umgebung();
  env.basis.fetch = async (url, init) => {
    env.anfragen.push({ url, ...init });
    return url.endsWith("/foto") ? new Promise((resolve) => { fotoGestartet = resolve; }) : ok(2);
  };
  const o = createOutbox(env.basis);
  await o.erfassen({ typ: "foto", dispatch_id: 1, einheit_id: 9, auftrag_version: 1, payload: { kommentar: "Lage" }, blob: new Blob(["foto"]), dateiname: "foto.jpg" });
  await warteAuf(() => env.anfragen.length === 1);
  await eintrag(o, "status", 1); await o.flush(); await warteAuf(() => env.anfragen.length === 2);
  assert.equal(env.anfragen.length, 2); assert.match(env.anfragen[1].url, /status$/);
  fotoGestartet(ok(1));
});

test("nur 2xx mit aktion_id und ohne Offline-Header wird bestaetigt", async () => {
  const env = umgebung([antwort(200, {}), new Error("netz"), antwort(200, { aktion_id: 3 }, { "X-EC-Offline": "1" })]);
  const o = createOutbox(env.basis);
  await eintrag(o, "status", 1); await warteAuf(() => env.anfragen.length === 1);
  await eintrag(o, "status", 2); await warteAuf(() => env.anfragen.length === 2);
  await eintrag(o, "status", 3); await warteAuf(() => env.anfragen.length === 3);
  await warteAuf(async () => (await o.liste()).every((x) => x.status === "fehler_netz"));
  assert.equal((await o.zaehler()).ausstehend, 3);
});

test("Retry verwendet dieselbe client_uuid", async () => {
  const env = umgebung([new Error("timeout"), ok(7)]); const o = createOutbox(env.basis);
  const x = await eintrag(o); await warteAuf(async () => (await o.liste())[0]?.status === "fehler_netz");
  env.vor(2000); await o.flush();
  assert.equal(JSON.parse(env.anfragen[0].body).client_uuid, x.client_uuid);
  assert.equal(JSON.parse(env.anfragen[1].body).client_uuid, x.client_uuid);
});

test("409 bleibt Konflikt, lesbar und blockiert Folgeeintrag nicht", async () => {
  const env = umgebung([antwort(409, { code: "auftrag_zurueckgezogen" }), ok(2)]); const o = createOutbox(env.basis);
  await eintrag(o, "status", 8, { status: "anfahrt", grund: "Test" }); await eintrag(o, "status", 8, { status: "vor_ort" });
  await warteAuf(async () => env.anfragen.length === 2 && (await o.liste()).length === 1);
  const liste = await o.liste(); assert.equal(liste[0].status, "konflikt"); assert.equal(liste[0].payload.grund, "Test"); assert.equal(env.anfragen.length, 2);
});

test("403 kein_einheitenkontext bleibt Konflikt ohne Auto-Retry", async () => {
  const env = umgebung([antwort(403, { code: "kein_einheitenkontext" })]); const o = createOutbox(env.basis);
  await eintrag(o); await warteAuf(async () => (await o.liste())[0]?.status === "konflikt"); await o.flush();
  assert.equal(env.anfragen.length, 1); assert.equal((await o.liste())[0].status, "konflikt");
});

test("Aktivkonflikt bewahrt die konkret zu unterbrechende Disposition und Originalversion", async () => {
  const env = umgebung([antwort(409, { code: "aktiver_auftrag", details: { dispatch_id: 17, bezeichnung: "Schule" } })]);
  const o = createOutbox(env.basis);
  await o.erfassen({ typ: "status", dispatch_id: 8, einheit_id: 1, auftrag_version: 3,
    payload: { status: "anfahrt", ausgangsstatus: "bestaetigt" } });
  await warteAuf(async () => (await o.liste())[0]?.status === "konflikt");
  const [item] = await o.liste();
  assert.deepEqual(item.konflikt_details, { dispatch_id: 17, bezeichnung: "Schule" });
  assert.equal(item.auftrag_version, 3);
  assert.equal(item.payload.ausgangsstatus, "bestaetigt");
  const body = JSON.parse(env.anfragen[0].body);
  assert.equal(body.auftrag_version, 3);
  assert.equal(body.ausgangsstatus, "bestaetigt");
});

test("terminale Zugangssperre behaelt Eintraege und stoppt Flush", async () => {
  let terminal = null;
  const env = umgebung([antwort(401, { code: "zugang_widerrufen" })]);
  const o = createOutbox({ ...env.basis, onTerminal: (code) => { terminal = code; } });
  await eintrag(o); await warteAuf(async () => (await o.liste())[0]?.status === "blockiert_zugang");
  await o.flush();
  assert.equal(terminal, "zugang_widerrufen");
  assert.equal(env.anfragen.length, 1);
  assert.equal((await o.liste())[0].payload.status, "anfahrt");
});

test("Bereinigung entfernt nur alten Verlauf", async () => {
  const env = umgebung([ok(1)]); const o = createOutbox(env.basis);
  await eintrag(o); await warteAuf(async () => (await o.verlauf()).length === 1);
  env.vor(73 * 60 * 60 * 1000); await o.bereinigen();
  assert.equal((await o.verlauf()).length, 0);
});

test("422 braucht manuelles erneutes Senden; 503 staffelt Backoff", async () => {
  const env = umgebung([antwort(422, { code: "ungueltig" }), ok(2), antwort(503, {}), antwort(503, {}), antwort(503, {})]); const o = createOutbox(env.basis);
  const x = await eintrag(o); await warteAuf(async () => (await o.liste())[0]?.status === "fehler");
  await o.erneutSenden(x.client_uuid); assert.equal(env.anfragen.length, 2);
  await eintrag(o, "status", 2);
  await warteAuf(async () => (await o.liste()).find((v) => v.dispatch_id === 2)?.status === "fehler_netz");
  let y = (await o.liste()).find((v) => v.dispatch_id === 2); assert.equal(new Date(y.naechster_versuch_at).getTime() - env.basis.now().getTime(), 2000);
  env.vor(2000); await o.flush(); y = (await o.liste()).find((v) => v.dispatch_id === 2); assert.equal(new Date(y.naechster_versuch_at).getTime() - env.basis.now().getTime(), 4000);
  env.vor(4000); await o.flush(); y = (await o.liste()).find((v) => v.dispatch_id === 2); assert.equal(new Date(y.naechster_versuch_at).getTime() - env.basis.now().getTime(), 8000);
});

test("Foto-FormData enthaelt alle Felder und Blob bleibt nach Abbruch", async () => {
  const blob = new Blob(["bild"], { type: "image/jpeg" }); const env = umgebung([new Error("abbruch"), ok(2)]); const o = createOutbox({ ...env.basis, csrfToken: () => "csrf" });
  const x = await o.erfassen({ typ: "foto", dispatch_id: 3, einheit_id: 9, auftrag_version: 1, payload: { kommentar: "Kommentar" }, blob, dateiname: "bild.jpg" }); await warteAuf(async () => (await o.liste())[0]?.status === "fehler_netz");
  assert.equal((await o.liste())[0].hat_foto, true); env.vor(2000); await o.flush(); await warteAuf(() => env.anfragen.length === 2);
  const form = env.anfragen[1].body; assert.equal(form.get("client_uuid"), x.client_uuid); assert.equal(form.get("kommentar"), "Kommentar"); assert.equal(form.get("_csrf"), "csrf"); assert.equal(form.get("file").name, "bild.jpg");
});

test("Material wird vor dem zugehoerigen Material-Foto bestaetigt", async () => {
  let materialBestaetigen;
  const env = umgebung();
  env.basis.fetch = async (url, init) => {
    env.anfragen.push({ url, ...init });
    if (url.endsWith("/material")) return new Promise((resolve) => { materialBestaetigen = resolve; });
    return ok(2);
  };
  const o = createOutbox({ ...env.basis, csrfToken: () => "csrf" });
  const material = await eintrag(o, "material", 3, { positionen: [{ bezeichnung: "Pumpe", menge: 1 }] });
  await warteAuf(() => env.anfragen.length === 1);
  await o.erfassen({ typ: "material_foto", dispatch_id: 3, einheit_id: 9, auftrag_version: 1,
    payload: { material_client_uuid: material.client_uuid }, blob: new Blob(["bild"]), dateiname: "material.jpg" });
  await leer(); assert.equal(env.anfragen.length, 1);
  materialBestaetigen(ok(1));
  await warteAuf(() => env.anfragen.length === 2);
  assert.deepEqual(env.anfragen.map((x) => x.url), ["/einheit/api/auftrag/3/material", "/einheit/api/auftrag/3/material_foto"]);
});

test("Material-Foto wartet auf ausstehendes Material nach Netzfehler", async () => {
  const env = umgebung([new Error("offline")]); const o = createOutbox(env.basis);
  const material = await eintrag(o, "material", 3, { positionen: [{ bezeichnung: "Pumpe", menge: 1 }] });
  await warteAuf(async () => (await o.liste())[0]?.status === "fehler_netz");
  await o.erfassen({ typ: "material_foto", dispatch_id: 3, einheit_id: 9, auftrag_version: 1,
    payload: { material_client_uuid: material.client_uuid }, blob: new Blob(["bild"]), dateiname: "material.jpg" });
  await leer(); assert.equal(env.anfragen.length, 1);
});

test("Konflikt beim Material setzt Material-Foto auf material_nicht_angelegt", async () => {
  const env = umgebung([antwort(409, { code: "ungueltig" })]); const o = createOutbox(env.basis);
  const material = await eintrag(o, "material", 3, { positionen: [{ bezeichnung: "Pumpe", menge: 1 }] });
  await o.erfassen({ typ: "material_foto", dispatch_id: 3, einheit_id: 9, auftrag_version: 1,
    payload: { material_client_uuid: material.client_uuid }, blob: new Blob(["bild"]), dateiname: "material.jpg" });
  await warteAuf(async () => (await o.liste()).some((item) => item.typ === "material_foto" && item.status === "konflikt"));
  const foto = (await o.liste()).find((item) => item.typ === "material_foto");
  assert.equal(foto.letzter_fehler, "material_nicht_angelegt");
});

test("Material-Foto-FormData enthaelt material_client_uuid", async () => {
  const env = umgebung([ok(1)]); const o = createOutbox({ ...env.basis, csrfToken: () => "csrf" });
  await o.erfassen({ typ: "material_foto", dispatch_id: 3, einheit_id: 9, auftrag_version: 1,
    payload: { material_client_uuid: "material-uuid" }, blob: new Blob(["bild"]), dateiname: "material.jpg" });
  await warteAuf(() => env.anfragen.length === 1);
  const form = env.anfragen[0].body;
  assert.equal(form.get("material_client_uuid"), "material-uuid");
  assert.equal(form.get("kommentar"), null);
  assert.equal(form.get("_csrf"), "csrf");
});

test("Zaehler und onChange reagieren auf Zustandsaenderungen", async () => {
  const env = umgebung([antwort(422, { code: "x" })]); const o = createOutbox(env.basis); let aenderungen = 0; const abmelden = o.onChange(() => aenderungen++);
  await eintrag(o); await warteAuf(async () => (await o.zaehler()).fehler === 1);
  assert.deepEqual(await o.zaehler(), { ausstehend: 0, fehler: 1, konflikt: 0, gesamt: 1 }); assert.ok(aenderungen >= 2); abmelden();
});

test("Ressourcenpflege verwendet den eigenen JSON-Endpunkt", async () => {
  const env = umgebung([ok(12)]); const o = createOutbox(env.basis);
  await o.erfassen({
    typ: "ressource_personal", einheit_id: 9,
    payload: { operation: "setzen", gesamt: 4, fuehrung: 1 },
  });
  await warteAuf(() => env.anfragen.length === 1);
  assert.equal(env.anfragen[0].url, "/einheit/api/ressource/personal");
  const body = JSON.parse(env.anfragen[0].body);
  assert.equal(body.gesamt, 4);
  assert.ok(body.client_uuid);
});

test("Simulation-Header und getrennte Datenbanken", async () => {
  const env = umgebung([ok(1)]); const a = createOutbox({ ...env.basis, simEinheitId: 17, dbName: "a" }); const b = createOutbox({ ...env.basis, dbName: "b" });
  await eintrag(a); await warteAuf(() => env.anfragen.length === 1); assert.equal(env.anfragen[0].headers["X-EC-Einheit-Sim"], "17"); assert.equal((await b.liste()).length, 0);
});

test("parallele flush-Aufrufe senden nur einmal", async () => {
  const env = umgebung([ok(1)]); const o = createOutbox(env.basis); await eintrag(o); await Promise.all([o.flush(), o.flush(), o.flush()]); assert.equal(env.anfragen.length, 1);
});

test("Verlauf enthaelt Bestaetigung und Hinweis", async () => {
  const env = umgebung([ok(5, { hinweis: "auftrag_geaendert" })]); const o = createOutbox(env.basis); await eintrag(o);
  await warteAuf(async () => (await o.verlauf()).length === 1);
  const verlauf = await o.verlauf(); assert.equal(verlauf.length, 1); assert.ok(verlauf[0].server_bestaetigt_at); assert.equal(verlauf[0].antwort.hinweis, "auftrag_geaendert");
});

test("Foto wird als frischer Blob mit vollem Inhalt gesendet", async () => {
  const env = umgebung([ok(5)]); const o = createOutbox({ ...env.basis, dbName: "foto-inhalt" });
  await o.erfassen({ typ: "foto", dispatch_id: 4, einheit_id: 9, auftrag_version: 1, payload: {}, blob: new Blob(["bilddaten"], { type: "image/jpeg" }), dateiname: "a.jpg" });
  await warteAuf(() => env.anfragen.length === 1);
  const datei = env.anfragen[0].body.get("file");
  assert.equal(await datei.text(), "bilddaten"); assert.equal(datei.type, "image/jpeg");
  assert.ok(env.anfragen[0].body.get("client_uuid"));
});

test("leeres Foto wird nicht gesendet und lesbar als Fehler markiert", async () => {
  const env = umgebung(); const o = createOutbox({ ...env.basis, dbName: "foto-leer" });
  await o.erfassen({ typ: "foto", dispatch_id: 4, einheit_id: 9, auftrag_version: 1, payload: {}, blob: new Blob([]), dateiname: "a.jpg" });
  await warteAuf(async () => (await o.liste())[0]?.status === "fehler");
  assert.equal((await o.liste())[0].letzter_fehler, "foto_daten_fehlen"); assert.equal(env.anfragen.length, 0);
});

test("zufallsUuid liefert auch ohne crypto.randomUUID eine gueltige v4-UUID", async () => {
  const { zufallsUuid } = await import("../../app/static/js/einheit_outbox.js");
  const v4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  const mitGetRandomValues = { getRandomValues(bytes) { bytes.fill(255); return bytes; } };
  assert.match(zufallsUuid(mitGetRandomValues), v4);
  assert.match(zufallsUuid(undefined), v4);
  assert.equal(zufallsUuid({ randomUUID: () => "fest" }), "fest");
  assert.notEqual(zufallsUuid(undefined), zufallsUuid(undefined));
});

test("Ergebnisreferenz und UUID bleiben beim Offline-Neustart und Versand erhalten", async () => {
  const env = umgebung([new Error("offline")]);
  const erste = createOutbox({ ...env.basis, dbName: "ergebnis-referenz" });
  const ergebnisUuid = "11111111-1111-4111-8111-111111111111";
  await erste.erfassenPaket([
    { typ: "meldung", dispatch_id: 1, client_uuid: ergebnisUuid,
      payload: { art: "lagemeldung", schnellmeldung: "keine_massnahmen", erkundungsergebnis: true } },
    { typ: "status", dispatch_id: 1, payload: { status: "abgeschlossen", ergebnis_client_uuid: ergebnisUuid } },
  ]);
  await warteAuf(async () => (await erste.liste())[0]?.status === "fehler_netz");
  const neu = createOutbox({ ...env.basis, dbName: "ergebnis-referenz" });
  const liste = await neu.liste();
  assert.equal(liste[0].client_uuid, ergebnisUuid);
  assert.equal(liste[1].payload.ergebnis_client_uuid, ergebnisUuid);
  await neu.erneutSenden(liste[0].client_uuid);
  await warteAuf(async () => (await neu.liste()).length === 0);
  const bodies = env.anfragen.map(r => typeof r.body === "string" ? JSON.parse(r.body) : null);
  assert.equal(bodies.at(-1).ergebnis_client_uuid, ergebnisUuid);
  assert.equal(bodies.at(-2).client_uuid, ergebnisUuid);
});

test("Foto-Vorschau bleibt lokal; bestätigter Verlauf behält Erfassungszeit und Inhalt ohne Binärdaten", async () => {
  let verbunden = false;
  const env = umgebung([], { fetch: async () => { if (!verbunden) throw new Error("offline"); return ok(); } });
  const o = createOutbox(env.basis);
  const [meldung, foto] = await o.erfassenPaket([
    { typ: "meldung", dispatch_id: 1, einheit_id: 9, payload: { art: "note", text: "Ergebnis" } },
    { typ: "foto", dispatch_id: 1, einheit_id: 9, blob: new Blob(["bild"], { type: "image/png" }), dateiname: "bild.png" },
  ]);
  await warteAuf(async () => (await o.liste()).every(e => e.status === "fehler_netz"));
  const bild = await o.fotoVorschau(foto.client_uuid);
  assert.equal(bild.type, "image/png");
  assert.equal(await bild.text(), "bild");
  assert.equal((await o.liste())[0].blob_daten, undefined);
  verbunden = true;
  await o.erneutSenden(meldung.client_uuid);
  await o.erneutSenden(foto.client_uuid);
  await warteAuf(async () => (await o.liste()).length === 0);
  const history = await o.verlauf();
  const text = history.find(e => e.client_uuid === meldung.client_uuid);
  assert.equal(text.einheit_id, 9);
  assert.equal(text.erfasst_at, meldung.erfasst_at);
  assert.equal(text.payload.text, "Ergebnis");
  const receiptFoto = history.find(e => e.client_uuid === foto.client_uuid);
  assert.equal(receiptFoto.blob_daten, undefined);
  assert.equal(receiptFoto.blob, undefined);
});

test("hängender POST wird begrenzt und mit derselben UUID wiederholbar", async () => {
  let abgebrochen = false;
  const env = umgebung([(_url, init) => {
    init.signal.addEventListener("abort", () => { abgebrochen = true; });
    return new Promise(() => {});
  }, ok(2)], { requestTimeoutMs: 10 });
  const o = createOutbox(env.basis);
  const item = await eintrag(o);
  await warteAuf(async () => (await o.liste())[0]?.status === "fehler_netz");
  assert.equal(abgebrochen, true);
  env.vor(2000);
  await o.flush();
  assert.equal((await o.liste()).length, 0);
  assert.equal(JSON.parse(env.anfragen[1].body).client_uuid, item.client_uuid);
});

test("auch ein hängender JSON-Antwortkörper wird nicht als Erfolg bestätigt", async () => {
  const env = umgebung([{ ok: true, status: 200, headers: new Headers(), json: () => new Promise(() => {}) }],
    { requestTimeoutMs: 10 });
  const o = createOutbox(env.basis);
  await eintrag(o);
  await warteAuf(async () => (await o.liste())[0]?.status === "fehler_netz");
  assert.equal((await o.verlauf()).length, 0);
});

test("aktive Foto-Wiederholung erzeugt keinen sofortigen Retry-Kreislauf", async () => {
  let antworten;
  const env = umgebung([new Error("offline"), () => new Promise(resolve => { antworten = resolve; })]);
  const o = createOutbox(env.basis);
  await o.erfassen({ typ: "foto", dispatch_id: 1, einheit_id: 9, blob: new Blob(["bild"]) });
  await warteAuf(async () => (await o.liste())[0]?.status === "fehler_netz");
  env.vor(2000); await o.flush();
  await warteAuf(() => env.anfragen.length === 2);
  assert.equal(await o.naechsterVersuchIn(), null);
  antworten(ok(2));
  await warteAuf(async () => (await o.liste()).length === 0);
});

test("Zugangsende hält auch verspätete Antworten und manuelles Wiederholen gesperrt", async () => {
  for (const spaet of [ok(1), antwort(503), new Error("offline")]) {
    let antworten, ablehnen;
    const env = umgebung([() => new Promise((resolve, reject) => { antworten = resolve; ablehnen = reject; })]);
    const o = createOutbox(env.basis);
    const item = await eintrag(o);
    await warteAuf(() => env.anfragen.length === 1);
    await o.blockiereZugang();
    if (spaet instanceof Error) ablehnen(spaet); else antworten(spaet);
    await leer(); await leer();
    assert.equal((await o.liste())[0]?.status, "blockiert_zugang");
    assert.equal((await o.verlauf()).length, 0);
    await o.erneutSenden(item.client_uuid);
    assert.equal((await o.liste())[0]?.status, "blockiert_zugang");
    assert.equal(await o.naechsterVersuchIn(), null);
    assert.equal(env.anfragen.length, 1);
  }
});

test("zwei Tabs erhalten nur eine lokale Quittung zur selben Serveraktion", async () => {
  const offen = [];
  const env = umgebung([], { fetch: async () => new Promise(resolve => offen.push(resolve)) });
  const a = createOutbox(env.basis), b = createOutbox(env.basis);
  await eintrag(a);
  await warteAuf(() => offen.length === 1);
  const zweiter = b.flush();
  await warteAuf(() => offen.length === 2);
  offen.forEach(resolve => resolve(ok(7)));
  await zweiter;
  await warteAuf(async () => (await a.liste()).length === 0);
  await leer();
  assert.equal((await a.verlauf()).length, 1);
});

test("Einheitenwechsel sendet weder fremde noch kontextlose alte Ressourcenpflege", async () => {
  const env = umgebung([new Error("offline"), new Error("offline")]);
  const alt = createOutbox({ ...env.basis, einheitId: 9 });
  await alt.erfassen({ typ: "ressource_personal", einheit_id: 9, payload: { gesamt: 7 } });
  await warteAuf(async () => (await alt.liste())[0]?.status === "fehler_netz");
  // Alteintrag ohne Einheit: auch dafür ist die aktuelle Zuordnung kein Sendebeweis.
  const legacy = createOutbox(env.basis);
  await legacy.erfassen({ typ: "ressource_personal", payload: { gesamt: 8 } });
  await warteAuf(async () => (await legacy.liste()).length === 2);
  const anzahl = env.anfragen.length;
  env.vor(2000);
  const neu = createOutbox({ ...env.basis, einheitId: 10 });
  await neu.flush();
  assert.equal(env.anfragen.length, anzahl);
  assert.ok((await neu.liste()).every(e => e.status === "konflikt" && e.letzter_fehler === "einheit_gewechselt"));
});

test("CSRF-Abgleich ist wiederholbar, Berechtigungsfehler bleiben Konflikte", async () => {
  let token = "alt";
  const env = umgebung([antwort(403, { detail: "CSRF-Token fehlt oder ungültig" }), ok(2)]);
  const o = createOutbox({ ...env.basis, einheitId: 9, csrfToken: () => token });
  const item = await eintrag(o);
  await warteAuf(async () => (await o.liste())[0]?.status === "fehler_netz");
  token = "neu"; env.vor(2000); await o.flush();
  assert.equal((await o.liste()).length, 0);
  assert.equal(env.anfragen[1].headers["X-CSRF-Token"], "neu");
  assert.equal(env.anfragen[1].headers["X-EC-Einheit-Kontext"], "9");
  assert.equal(JSON.parse(env.anfragen[1].body).client_uuid, item.client_uuid);
});

test("bestätigte Wiederverbindung wiederholt Netzrückstände sofort, keine fachlichen Konflikte", async () => {
  const env = umgebung([new Error("offline"), antwort(409, { code: "auftrag_geaendert" }), ok(3)]);
  const o = createOutbox(env.basis);
  const netz = await eintrag(o, "status", 1);
  await warteAuf(async () => (await o.liste())[0]?.status === "fehler_netz");
  await eintrag(o, "status", 2);
  await warteAuf(async () => (await o.liste()).some(item => item.status === "konflikt"));
  await o.flush({ wiederverbunden: true }); // Die 2-s-Rückoff-Frist wurde noch nicht erreicht.
  assert.equal((await o.liste()).length, 1);
  assert.equal((await o.liste())[0].status, "konflikt");
  assert.equal(JSON.parse(env.anfragen[2].body).client_uuid, netz.client_uuid);
});

test("onChange liefert neue Einträge erst nach lokalem Commit, noch vor dem ersten POST", async () => {
  const env = umgebung([], { fetch: () => new Promise(() => {}) });
  const o = createOutbox(env.basis);
  let neu;
  o.onChange(items => { if (items) neu = items; });
  const item = await o.erfassen({ typ: "foto", dispatch_id: 1, einheit_id: 9, blob: new Blob(["bild"]) });
  assert.equal(neu[0].client_uuid, item.client_uuid);
  assert.equal(neu[0].hat_foto, true);
  assert.equal(neu[0].blob_daten, undefined);
  assert.equal(neu[0].status, "ausstehend");
  assert.equal((await o.liste())[0].client_uuid, neu[0].client_uuid);
  assert.equal((await o.verlauf()).length, 0);
});

test("offene Kontextpruefung sperrt automatische, manuelle und Foto-Flushes vor jedem POST", async () => {
  let freigeben;
  const pruefung = new Promise(resolve => { freigeben = resolve; });
  let pruefungen = 0;
  const env = umgebung([], { vorSenden: async () => { pruefungen++; return pruefung; } });
  const o = createOutbox(env.basis);
  await o.erfassenPaket([
    { typ: "status", dispatch_id: 1, payload: { status: "bestaetigt" } },
    { typ: "status", dispatch_id: 1, payload: { status: "anfahrt" } },
    { typ: "foto", dispatch_id: 2, blob: new Blob(["foto"]) },
  ]);
  const lauf = o.flush({ wiederverbunden: true });
  await warteAuf(() => pruefungen >= 2);
  assert.equal(env.anfragen.length, 0);
  // Wie beim 401 des Kontext-Endpunkts: alle Originaldaten lokal erhalten.
  await o.blockiereZugang();
  freigeben(true); // Selbst eine verspaetete positive Antwort darf die Sperre nicht aufheben.
  await lauf;
  await o.erneutSenden((await o.liste())[0].client_uuid);
  assert.equal(env.anfragen.length, 0);
  assert.deepEqual((await o.liste()).map(item => item.status), Array(3).fill("blockiert_zugang"));
  assert.equal((await o.verlauf()).length, 0);
});

test("jeder POST prueft den Kontext und liest danach den erneuerten CSRF-Cookie", async () => {
  let pruefungen = 0;
  let token = "alt";
  const env = umgebung([], {
    vorSenden: async () => { pruefungen++; token = "erneuert"; return true; },
    csrfToken: () => token,
  });
  const o = createOutbox(env.basis);
  await o.erfassenPaket([
    { typ: "status", dispatch_id: 1, payload: { status: "bestaetigt" } },
    { typ: "status", dispatch_id: 1, payload: { status: "anfahrt" } },
    { typ: "foto", dispatch_id: 2, blob: new Blob(["foto"]) },
  ]);
  await warteAuf(async () => (await o.verlauf()).length === 3);
  assert.equal(pruefungen, 3);
  assert.equal(env.anfragen.length, 3);
  assert.ok(env.anfragen.every(anfrage => anfrage.headers["X-CSRF-Token"] === "erneuert"));
  const foto = env.anfragen.find(anfrage => anfrage.body instanceof FormData);
  assert.equal(foto.body.get("_csrf"), "erneuert");
  assert.deepEqual(env.anfragen.filter(anfrage => typeof anfrage.body === "string")
    .map(anfrage => JSON.parse(anfrage.body).status), ["bestaetigt", "anfahrt"]);
});

test("Netzfehler bei Kontextpruefung behaelt Originaleintrag und bisherige Retry-Frist", async () => {
  let erreichbar = false;
  const env = umgebung([], { vorSenden: async () => {
    if (!erreichbar) throw new Error("offline");
    return true;
  } });
  const o = createOutbox(env.basis);
  const gespeichert = await eintrag(o);
  await warteAuf(async () => (await o.liste())[0]?.status === "fehler_netz");
  assert.equal(env.anfragen.length, 0);
  assert.equal((await o.liste())[0].versuche, 1);
  erreichbar = true;
  env.vor(1999); await o.flush();
  assert.equal(env.anfragen.length, 0);
  env.vor(1); await o.flush();
  assert.equal(env.anfragen.length, 1);
  assert.equal(JSON.parse(env.anfragen[0].body).client_uuid, gespeichert.client_uuid);
  assert.equal((await o.verlauf()).length, 1);
});

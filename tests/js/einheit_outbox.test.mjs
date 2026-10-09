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

test("Zaehler und onChange reagieren auf Zustandsaenderungen", async () => {
  const env = umgebung([antwort(422, { code: "x" })]); const o = createOutbox(env.basis); let aenderungen = 0; const abmelden = o.onChange(() => aenderungen++);
  await eintrag(o); await warteAuf(async () => (await o.zaehler()).fehler === 1);
  assert.deepEqual(await o.zaehler(), { ausstehend: 0, fehler: 1, konflikt: 0, gesamt: 1 }); assert.ok(aenderungen >= 2); abmelden();
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

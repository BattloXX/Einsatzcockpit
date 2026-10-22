// ECP-01: Objekt-Auswahl der Erstellen-Dialoge (objekt_picker.js).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const quelle = readFileSync(new URL("../../app/static/js/objekt_picker.js", import.meta.url), "utf8");

function umgebung(objekte = [], { speicher = new Map(), ok = true } = {}) {
  const anfragen = [];
  const kontext = {
    window: {},
    sessionStorage: {
      getItem: (k) => (speicher.has(k) ? speicher.get(k) : null),
      setItem: (k, v) => speicher.set(k, v),
      removeItem: (k) => speicher.delete(k),
    },
    fetch: async (url) => {
      anfragen.push(url);
      return { ok, status: ok ? 200 : 500, json: async () => ({ objekte }) };
    },
    Date,
  };
  vm.runInNewContext(quelle, kontext);
  return { picker: (key, id) => kontext.window.objektPicker(key, id), anfragen, speicher };
}

const halle = { id: 1, name: "#1 Lagerhalle", vulgoname: "Alte Halle", adresse: "Hauptstrasse 5, Wolfurt", bma: "B-77" };
const schule = { id: 2, name: "#2 Schule", vulgoname: null, adresse: "Schulweg 1", bma: null };

test("laedt Objekte einmalig und sucht in Name, Vulgoname, Adresse und BMA-Nr.", async () => {
  const { picker, anfragen } = umgebung([halle, schule]);
  const p = picker("neu", null);
  await p.ladeObjekte();
  await p.ladeObjekte();
  assert.equal(anfragen.length, 1);
  assert.equal(anfragen[0], "/api/uebernahme/objekte");
  p.suche = "b-77";
  assert.deepEqual(p.treffer.map((o) => o.id), [1]);
  p.suche = "schulweg";
  assert.deepEqual(p.treffer.map((o) => o.id), [2]);
  p.suche = "alte halle";
  assert.deepEqual(p.treffer.map((o) => o.id), [1]);
});

test("mit Einsatz-ID wird der Einsatz fuer die Sortierung uebergeben", async () => {
  const { picker, anfragen } = umgebung([halle]);
  await picker("meldung-5", 5).ladeObjekte();
  assert.equal(anfragen[0], "/api/uebernahme/objekte?incident_id=5");
});

test("Auswahl bleibt bei Eingabefehlern erhalten und kann entfernt werden", () => {
  const env = umgebung([halle]);
  const a = env.picker("neu", null);
  a.waehle(halle);
  assert.equal(a.auswahl.id, 1);
  const b = env.picker("neu", null); // Dialog neu geoeffnet / Seite nach Fehler neu geladen
  b.init();
  assert.equal(b.auswahl.id, 1);
  b.entferne();
  const c = env.picker("neu", null);
  c.init();
  assert.equal(c.auswahl, null);
});

test("abgelaufene Auswahl (> 10 Minuten) wird nicht wiederhergestellt", () => {
  const speicher = new Map([["ec_objekt_picker:neu", JSON.stringify({ objekt: halle, at: Date.now() - 11 * 60 * 1000 })]]);
  const p = umgebung([], { speicher }).picker("neu", null);
  p.init();
  assert.equal(p.auswahl, null);
});

test("Ladefehler wird angezeigt, ohne die Eingabe zu blockieren", async () => {
  const p = umgebung([], { ok: false }).picker("neu", null);
  await p.ladeObjekte();
  assert.match(p.fehler, /nicht geladen/);
  assert.equal(p.geladen, false);
});

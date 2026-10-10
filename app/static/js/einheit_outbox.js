/* Persistente Offline-Outbox fuer den Einheitenmodus; bewusst ohne DOM-Zugriffe. */
export function createOutbox({
  indexedDB,
  fetch,
  now = () => new Date(),
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  uuid = () => crypto.randomUUID(),
  csrfToken = () => "",
  simEinheitId = null,
  dbName = "ec-einheit",
  onTerminal = () => {},
}) {
  const listeners = new Set();
  const inBearbeitung = new Set();
  let dbPromise;
  let statusKette = Promise.resolve();
  let terminal = false;

  function melden() {
    for (const listener of listeners) listener();
  }

  function db() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(dbName, 1);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains("outbox")) database.createObjectStore("outbox", { keyPath: "client_uuid" });
        if (!database.objectStoreNames.contains("verlauf")) database.createObjectStore("verlauf", { keyPath: "id", autoIncrement: true });
        if (!database.objectStoreNames.contains("entwuerfe")) database.createObjectStore("entwuerfe");
        if (!database.objectStoreNames.contains("zustand")) database.createObjectStore("zustand");
        if (!database.objectStoreNames.contains("meta")) database.createObjectStore("meta");
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return dbPromise;
  }

  async function mitStore(namen, modus, arbeit) {
    const database = await db();
    return new Promise((resolve, reject) => {
      const transaktion = database.transaction(namen, modus);
      let wert;
      transaktion.oncomplete = () => resolve(wert);
      transaktion.onerror = () => reject(transaktion.error);
      transaktion.onabort = () => reject(transaktion.error);
      wert = arbeit(transaktion, (neu) => { wert = neu; });
    });
  }

  async function lesen(name, schluessel) {
    const database = await db();
    return new Promise((resolve, reject) => {
      const request = database.transaction(name).objectStore(name).get(schluessel);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function alle(name) {
    const database = await db();
    return new Promise((resolve, reject) => {
      const request = database.transaction(name).objectStore(name).getAll();
      request.onsuccess = () => resolve(request.result || []);
      request.onerror = () => reject(request.error);
    });
  }

  async function speichern(name, wert, schluessel) {
    await mitStore([name], "readwrite", (tx) => {
      if (schluessel === undefined) tx.objectStore(name).put(wert);
      else tx.objectStore(name).put(wert, schluessel);
    });
  }

  function zeitwert(value) {
    return new Date(value).getTime();
  }

  function faellig(eintrag, zeit = now()) {
    return eintrag.status === "ausstehend" || (
      eintrag.status === "fehler_netz" &&
      eintrag.naechster_versuch_at !== null &&
      zeitwert(eintrag.naechster_versuch_at) <= zeit.getTime()
    );
  }

  function kopieOhneBlob(eintrag) {
    const { blob, ...rest } = eintrag;
    return { ...rest, hat_foto: Boolean(blob) };
  }

  function header() {
    const headers = { "X-CSRF-Token": csrfToken() };
    if (simEinheitId !== null && simEinheitId !== undefined) headers["X-EC-Einheit-Sim"] = String(simEinheitId);
    return headers;
  }

  function rueckoff(versuche) {
    return Math.min(2000 * (2 ** Math.max(0, versuche - 1)), 60000);
  }

  async function setze(eintrag, aenderung) {
    const aktuell = await lesen("outbox", eintrag.client_uuid);
    if (!aktuell) return null;
    const neu = { ...aktuell, ...aenderung };
    await speichern("outbox", neu);
    melden();
    return neu;
  }

  async function bestaetigen(eintrag, antwort) {
    await mitStore(["outbox", "verlauf"], "readwrite", (tx) => {
      tx.objectStore("outbox").delete(eintrag.client_uuid);
      tx.objectStore("verlauf").add({
        client_uuid: eintrag.client_uuid,
        typ: eintrag.typ,
        dispatch_id: eintrag.dispatch_id,
        server_bestaetigt_at: now().toISOString(),
        antwort,
      });
    });
    const eintraege = await alle("verlauf");
    if (eintraege.length > 200) {
      eintraege.sort((a, b) => a.id - b.id);
      await mitStore(["verlauf"], "readwrite", (tx) => {
        for (const alt of eintraege.slice(0, eintraege.length - 200)) tx.objectStore("verlauf").delete(alt.id);
      });
    }
    melden();
  }

  async function senden(eintrag) {
    if (inBearbeitung.has(eintrag.client_uuid)) return "blockiert";
    inBearbeitung.add(eintrag.client_uuid);
    try {
      // Ein anderer Flush kann seit seinem Snapshot bereits erfolgreich gewesen sein.
      const aktuell = await lesen("outbox", eintrag.client_uuid);
      if (!aktuell || !faellig(aktuell)) return "blockiert";
      eintrag = aktuell;
      let response;
      try {
        const url = eintrag.typ.startsWith("ressource_")
          ? `/einheit/api/ressource/${eintrag.typ.slice("ressource_".length)}`
          : `/einheit/api/auftrag/${eintrag.dispatch_id}/${eintrag.typ}`;
        if (eintrag.typ === "foto") {
          const form = new FormData();
          form.append("file", eintrag.blob, eintrag.dateiname || "foto");
          form.append("client_uuid", eintrag.client_uuid);
          form.append("erfasst_at", eintrag.erfasst_at);
          form.append("kommentar", eintrag.payload?.kommentar || "");
          form.append("_csrf", csrfToken());
          response = await fetch(url, { method: "POST", headers: header(), body: form, credentials: "same-origin" });
        } else {
          response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json", ...header() },
            body: JSON.stringify({
              client_uuid: eintrag.client_uuid, erfasst_at: eintrag.erfasst_at,
              auftrag_version: eintrag.auftrag_version, ...eintrag.payload,
            }),
            credentials: "same-origin",
          });
        }
      } catch (error) {
        const versuche = eintrag.versuche + 1;
        await setze(eintrag, {
          status: "fehler_netz", versuche, letzter_fehler: error?.message || "netzwerkfehler",
          naechster_versuch_at: new Date(now().getTime() + rueckoff(versuche)).toISOString(),
        });
        return "blockiert";
      }
      let antwort;
      try { antwort = await response.json(); } catch { antwort = null; }
      if (response.ok && response.headers.get("X-EC-Offline") !== "1" && antwort?.aktion_id) {
        await bestaetigen(eintrag, antwort);
        return "weiter";
      }
      const code = antwort?.code || `http_${response.status}`;
      if (response.status === 401 && /^zugang_(widerrufen|abgelaufen|ungueltig)$/.test(code)) {
        await blockiereZugang();
        onTerminal(code);
        return "blockiert";
      }
      if (response.status === 409 || response.status === 403 || response.status === 404) {
        await setze(eintrag, { status: "konflikt", letzter_fehler: code, naechster_versuch_at: null });
        return "weiter";
      }
      if (response.status >= 500 || response.status === 408 || response.status === 429 || response.ok) {
        const versuche = eintrag.versuche + 1;
        await setze(eintrag, {
          status: "fehler_netz", versuche, letzter_fehler: code,
          naechster_versuch_at: new Date(now().getTime() + rueckoff(versuche)).toISOString(),
        });
        return "blockiert";
      }
      await setze(eintrag, { status: "fehler", letzter_fehler: code, naechster_versuch_at: null });
      return "weiter";
    } finally {
      inBearbeitung.delete(eintrag.client_uuid);
    }
  }

  async function statusSpurenFlushen() {
    const eintraege = (await alle("outbox")).sort((a, b) => a.seq - b.seq);
    const spuren = new Map();
    for (const eintrag of eintraege.filter((item) => item.typ !== "foto" && (item.status === "ausstehend" || item.status === "fehler_netz"))) {
      const key = String(eintrag.dispatch_id);
      if (!spuren.has(key)) spuren.set(key, []);
      spuren.get(key).push(eintrag);
    }
    await Promise.all([...spuren.values()].map(async (spur) => {
      for (const eintrag of spur) {
        if (!faellig(eintrag) || inBearbeitung.has(eintrag.client_uuid)) break;
        if (await senden(eintrag) === "blockiert") break;
      }
    }));
  }

  async function fotoSpurFlushen() {
    const eintraege = (await alle("outbox")).sort((a, b) => a.seq - b.seq);
    const fotos = eintraege.filter((item) => item.typ === "foto" && faellig(item) && !inBearbeitung.has(item.client_uuid));
    await Promise.all(fotos.map(senden));
  }

  async function flush() {
    if (terminal) return;
    // Fotos warten nie auf die Statusspur und umgekehrt nicht auf haengende Uploads.
    void fotoSpurFlushen();
    const lauf = statusKette.then(statusSpurenFlushen);
    statusKette = lauf.catch(() => undefined);
    return lauf;
  }

  async function blockiereZugang() {
    terminal = true;
    const eintraege = await alle("outbox");
    await mitStore(["outbox"], "readwrite", (tx) => {
      for (const eintrag of eintraege) {
        if (eintrag.status === "ausstehend" || eintrag.status === "fehler_netz") {
          tx.objectStore("outbox").put({ ...eintrag, status: "blockiert_zugang", letzter_fehler: "zugang_beendet", naechster_versuch_at: null });
        }
      }
    });
    melden();
  }

  async function bereinigen(alterStunden = 72) {
    const grenze = now().getTime() - alterStunden * 60 * 60 * 1000;
    const verlauf = await alle("verlauf");
    const entwuerfe = await alle("entwuerfe");
    await mitStore(["verlauf", "entwuerfe"], "readwrite", (tx) => {
      for (const eintrag of verlauf) if (zeitwert(eintrag.server_bestaetigt_at) < grenze) tx.objectStore("verlauf").delete(eintrag.id);
      for (const entwurf of entwuerfe) {
        if (entwurf?.saved_at && zeitwert(entwurf.saved_at) < grenze) tx.objectStore("entwuerfe").delete(entwurf.key);
      }
    });
  }

  return {
    async erfassen({ typ, dispatch_id, einheit_id, auftrag_version, payload = {}, blob = null, dateiname = null }) {
      if (!new Set(["status", "meldung", "foto", "ressource_personal", "ressource_ausstattung"]).has(typ)) {
        throw new Error("ungueltiger_typ");
      }
      const alte = await lesen("meta", "seq");
      const seq = (alte || 0) + 1;
      const eintrag = {
        client_uuid: uuid(), typ, dispatch_id, einheit_id, auftrag_version, payload, blob, dateiname,
        erfasst_at: now().toISOString(), seq, versuche: 0, status: "ausstehend", letzter_fehler: null,
        naechster_versuch_at: null,
      };
      await mitStore(["outbox", "meta"], "readwrite", (tx) => { tx.objectStore("outbox").add(eintrag); tx.objectStore("meta").put(seq, "seq"); });
      melden();
      void flush();
      return eintrag;
    },
    async flush() { return flush(); },
    blockiereZugang,
    bereinigen,
    async erneutSenden(client_uuid) {
      const eintrag = await lesen("outbox", client_uuid);
      if (eintrag) await setze(eintrag, { status: "ausstehend", naechster_versuch_at: null });
      await flush();
    },
    async verwerfen(client_uuid) { await mitStore(["outbox"], "readwrite", (tx) => tx.objectStore("outbox").delete(client_uuid)); melden(); },
    async liste() { return (await alle("outbox")).sort((a, b) => a.seq - b.seq).map(kopieOhneBlob); },
    async verlauf(limit = 200) { return (await alle("verlauf")).sort((a, b) => b.id - a.id).slice(0, limit); },
    async zaehler() {
      const items = await alle("outbox");
      return {
        ausstehend: items.filter((x) => x.status === "ausstehend" || x.status === "fehler_netz").length,
        fehler: items.filter((x) => x.status === "fehler").length,
        konflikt: items.filter((x) => x.status === "konflikt").length, gesamt: items.length,
      };
    },
    onChange(cb) { listeners.add(cb); return () => listeners.delete(cb); },
    async entwurfSpeichern(key, data) { await speichern("entwuerfe", { key, data, saved_at: now().toISOString() }, key); },
    async entwurfLaden(key) { const entwurf = await lesen("entwuerfe", key); return entwurf?.data || entwurf; },
    async entwurfLoeschen(key) { await mitStore(["entwuerfe"], "readwrite", (tx) => tx.objectStore("entwuerfe").delete(key)); },
    async zustandSpeichern(obj) { await speichern("zustand", obj, "aktuell"); },
    async zustandLaden() { return lesen("zustand", "aktuell"); },
    async zustandLeeren() { await mitStore(["zustand"], "readwrite", (tx) => tx.objectStore("zustand").clear()); },
    async naechsterVersuchIn() {
      const zeit = now().getTime();
      const werte = (await alle("outbox")).filter((x) => x.status === "fehler_netz" && x.naechster_versuch_at)
        .map((x) => Math.max(0, zeitwert(x.naechster_versuch_at) - zeit));
      return werte.length ? Math.min(...werte) : null;
    },
  };
}

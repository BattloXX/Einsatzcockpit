/* Gemeinsame Darstellung vorhandener Servereinträge und der vorhandenen Outbox. */
const STATUS = { zugewiesen: "Zugewiesen", bestaetigt: "Übernommen / bestätigt", anfahrt: "Anfahrt",
  vor_ort: "Vor Ort", in_arbeit: "In Arbeit", abgeschlossen: "Fertig gemeldet", nicht_durchfuehrbar: "Nicht durchführbar" };
const FEHLER = new Set(["fehler", "fehler_netz", "konflikt", "blockiert_zugang"]);
const kopie = wert => JSON.parse(JSON.stringify(wert));

export function vergleicheEintraege(a, b) {
  const norm = ts => {
    const wert = (ts || "").replace("+00:00", "Z");
    return /T\d{2}:\d{2}:\d{2}Z$/.test(wert) ? wert.replace("Z", ".000000Z")
      : wert.replace(/\.(\d+)Z$/, (_, f) => `.${f.padEnd(6, "0")}Z`);
  };
  const zeit = norm(a.ts).localeCompare(norm(b.ts));
  if (zeit) return zeit;
  const ka = a.id.split(":"), kb = b.id.split(":");
  return (Number(ka[0]) - Number(kb[0])) || (Number(ka[1]) - Number(kb[1])) || a.id.localeCompare(b.id);
}

export function verbindeEintraege(server, lokal, bestaetigt, auftraege, einheitId, { dispatchId = null, mitStatus = false } = {}) {
  const ziele = new Map(auftraege.map(a => [a.dispatch_id, a]));
  const stelle = dispatchId == null ? null : ziele.get(dispatchId)?.site_id;
  const alle = new Map(server.map(e => [e.id, e]));
  const uuids = new Set(server.map(e => e.client_uuid).filter(Boolean));
  for (const item of [...bestaetigt, ...lokal]) {
    const auftrag = ziele.get(item.dispatch_id);
    if (!auftrag || (item.einheit_id != null && item.einheit_id !== einheitId)
      || (dispatchId != null && auftrag.site_id !== stelle) || !["meldung", "foto", "status"].includes(item.typ)) continue;
    const offen = item.server_bestaetigt_at == null;
    if (item.typ === "status" && !mitStatus && !FEHLER.has(item.status)) continue;
    const entity = { site_log: "1", site_media: "2" }[item.antwort?.entity_type];
    // Alte reine Empfangsquittungen sind keine zusätzliche historische Meldung.
    if (!offen && !item.erfasst_at && (item.typ === "status" || !entity || !item.antwort?.entity_id)) continue;
    if (uuids.has(item.client_uuid) || (entity && alle.has(`${entity}:${item.antwort.entity_id}`))) continue;
    const payload = item.payload || {};
    const text = item.antwort?.meldung_text || [payload.schnellmeldung_label, payload.text,
      ...Object.values(payload.felder || {})].filter(Boolean).join("\n")
      || (item.typ === "foto" ? payload.kommentar || "Foto zur Einsatzstelle"
        : item.typ === "status" ? STATUS[payload.status || item.antwort?.einheit_status] || "Status gemeldet" : "Meldung");
    let hervorhebung = "";
    if (payload.schnellmeldung === "akute_gefahr" || item.antwort?.hervorhebung === "gefahr") hervorhebung = "gefahr";
    if (payload.schnellmeldung === "unterstuetzung" || item.antwort?.hervorhebung === "unterstuetzung") hervorhebung = "unterstuetzung";
    const fehler = offen && FEHLER.has(item.status);
    alle.set(`lokal:${item.client_uuid}`, { id: `lokal:${item.client_uuid}`, client_uuid: item.client_uuid,
      dispatch_id: item.dispatch_id, site_id: auftrag.site_id, stelle: auftrag.bezeichnung,
      auftragsart: auftrag.auftragsart, bezug: "Auftrag: " + (auftrag.auftrag || "Auftrag ohne zusätzlichen Text"),
      ts: item.erfasst_at || item.server_bestaetigt_at, text, absender: "Eigene Einheit", eigene: true,
      status: offen ? item.status : "uebermittelt", art: item.typ, kompakt: item.typ === "status",
      hervorhebung, fehler, lokal: offen, original: offen ? item : null,
      foto: null, hat_foto: item.typ === "foto" && offen && item.hat_foto,
    });
  }
  return [...alle.values()].sort(vergleicheEintraege);
}

export function meldungsverlauf(config) {
  const cacheName = `meldungsverlauf:${config.lage_id}:${config.einheit_id}`;
  let cache = null;
  let fotoGeneration = 0;
  let generation = 0;
  return {
    meldungsVerlaeufe: {}, meldungsFotoURLs: {},
    verlaufStand(id = null) {
      const key = id == null ? "zentral" : `auftrag:${id}`;
      return this.meldungsVerlaeufe[key] || { eintraege: [], cursor: null, geladen: false, laden: false, offline: false, fehler: "" };
    },
    verlaufEintraege(id) {
      if (id === undefined) return [];
      const zustand = this.verlaufStand(id);
      return verbindeEintraege(zustand.eintraege, this.outboxListe, this.verlauf, this.meldungsZiele, config.einheit_id,
        { dispatchId: id, mitStatus: id != null });
    },
    verlaufKennzeichnung(item) {
      return { auftrag: "Neuer Auftrag", aenderung: "Auftrag geändert", rueckzug: "Auftrag zurückgezogen",
        rueckfrage: "Rückfrage", gefahr: "Gefahr", unterstuetzung: "Unterstützung benötigt" }[item.hervorhebung] || "";
    },
    verlaufInhalt(item) {
      const kennzeichnung = this.verlaufKennzeichnung(item);
      if (kennzeichnung && item.text === kennzeichnung) return "";
      return kennzeichnung && item.text.startsWith(kennzeichnung + "\n")
        ? item.text.slice(kennzeichnung.length + 1) : item.text;
    },
    async verlaufOeffnen(item) {
      const id = item.dispatch_id || this.meldungsZiele.find(a => a.site_id === item.site_id)?.dispatch_id;
      if (id) await this.oeffne(id);
    },
    verlaufStatus(item) {
      if (!item.eigene) return "";
      if (item.status === "uebermittelt") return "Übermittelt";
      return item.original ? this.outboxStatus(item.original) : "Übertragung prüfen";
    },
    verlaufDatum(ts) {
      const date = new Date((ts || "").replace(/(\.\d{3})\d+/, "$1"));
      return Number.isNaN(date.getTime()) ? "Zeit nicht verfügbar" : new Intl.DateTimeFormat("de-AT", {
        day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
      }).format(date);
    },
    async verlaufLaden(id = null, mehr = false) {
      if (this.zugangBeendet || this.ladeFehler === "anmeldung") return;
      const key = id == null ? "zentral" : `auftrag:${id}`;
      const stand = this.verlaufStand(id);
      if (stand.laden || (mehr && !stand.cursor)) return;
      this.meldungsVerlaeufe[key] = { ...stand, laden: true, fehler: "" };
      const ziel = this.meldungsVerlaeufe[key];
      const anfrage = generation;
      const params = new URLSearchParams({ mit_status: id == null ? "false" : "true" });
      if (id != null) params.set("dispatch_id", id);
      if (mehr) params.set("cursor", stand.cursor);
      try {
        const response = await fetch(`/einheit/api/meldungen?${params}`, { headers: this.headers(), credentials: "same-origin" });
        if (this.zugangBeendet || generation !== anfrage) return;
        if ([401, 403, 404].includes(response.status)) {
          ziel.eintraege = []; ziel.cursor = null; ziel.fehler = "Meldungsverlauf nicht mehr freigegeben.";
          cache = cache || {}; delete cache[key];
          if (this.outbox) await this.outbox.metaSpeichern(cacheName, cache);
          if (response.status === 401) await this.verlaufZugangBeenden();
          return;
        }
        if (!response.ok || response.headers.get("X-EC-Offline") === "1") throw new Error("offline");
        const data = await response.json();
        if (data.einheit_id !== config.einheit_id || data.lage_id !== config.lage_id) throw new Error("kontext");
        if (this.zugangBeendet || generation !== anfrage) return;
        // Beim Nachladen und Live-Aktualisieren bereits gelesene ältere Einträge erhalten.
        const neu = data.eintraege || [];
        const aelter = mehr ? stand.eintraege : data.cursor && neu.length
          ? stand.eintraege.filter(e => vergleicheEintraege(e, neu[neu.length - 1]) < 0) : [];
        const zusammen = new Map([...aelter, ...neu].map(e => [e.id, e]));
        ziel.eintraege = [...zusammen.values()].sort((a, b) => vergleicheEintraege(b, a));
        ziel.cursor = mehr || !aelter.length ? data.cursor : stand.cursor;
        ziel.kopfCursor = mehr ? stand.kopfCursor : data.cursor;
        ziel.offline = false; ziel.geladen = true;
        if (this.outbox) {
          try {
            cache = cache || await this.outbox.metaLaden(cacheName) || {};
            if (this.zugangBeendet || generation !== anfrage) return;
            const umfang = ziel.eintraege.length > 200 ? 30 : 200;
            cache[key] = { eintraege: kopie(ziel.eintraege.slice(0, umfang)),
              cursor: umfang === 30 ? ziel.kopfCursor : ziel.cursor, kopfCursor: ziel.kopfCursor };
            await this.outbox.metaSpeichern(cacheName, cache);
          } catch { ziel.fehler = "Verlauf geladen. Offline-Speicherung derzeit nicht verfügbar."; }
        }
      } catch {
        if (this.zugangBeendet || generation !== anfrage) return;
        if (this.outbox) {
          try {
            cache = cache || await this.outbox.metaLaden(cacheName) || {};
            if (this.zugangBeendet || generation !== anfrage) return;
            if (!ziel.geladen && cache[key]) Object.assign(ziel, cache[key], { geladen: true });
          } catch { /* Lokale Einträge bleiben auch ohne Verlaufscache erreichbar. */ }
        }
        ziel.offline = true;
        ziel.fehler = ziel.geladen ? "" : "Meldungsverlauf derzeit nicht verfügbar.";
      } finally { ziel.laden = false; }
    },
    async verlaufFotosAktualisieren() {
      const generation = ++fotoGeneration;
      const fotos = this.outboxListe.filter(e => e.typ === "foto" && e.einheit_id === config.einheit_id).slice(0, 50);
      const ids = new Set(fotos.map(e => e.client_uuid));
      for (const [uuid, url] of Object.entries(this.meldungsFotoURLs)) {
        if (!ids.has(uuid)) { URL.revokeObjectURL(url); delete this.meldungsFotoURLs[uuid]; }
      }
      for (const item of fotos) {
        if (this.meldungsFotoURLs[item.client_uuid]) continue;
        let blob;
        try { blob = await this.outbox?.fotoVorschau(item.client_uuid); } catch { continue; }
        if (generation !== fotoGeneration || this.zugangBeendet) return;
        if (blob) this.meldungsFotoURLs[item.client_uuid] = URL.createObjectURL(blob);
      }
    },
    async verlaufZugangBeenden() {
      generation++;
      fotoGeneration++;
      for (const url of Object.values(this.meldungsFotoURLs)) URL.revokeObjectURL(url);
      this.meldungsFotoURLs = {}; this.meldungsVerlaeufe = {}; cache = {};
      if (this.outbox) await this.outbox.metaSpeichern(cacheName, {});
    },
  };
}

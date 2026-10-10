import { createOutbox } from "/static/js/einheit_outbox.js";

const STATUS = {
  zugewiesen: ["bestaetigt", "Auftrag bestätigen"],
  bestaetigt: ["anfahrt", "Anfahrt"],
  anfahrt: ["vor_ort", "Vor Ort"],
  vor_ort: ["in_arbeit", "In Arbeit"],
  in_arbeit: ["abgeschlossen", "Auftrag abgeschlossen"],
};

const STATUS_LABEL = {
  zugewiesen: "Zugewiesen",
  bestaetigt: "Auftrag bestätigt",
  anfahrt: "Anfahrt",
  vor_ort: "Vor Ort",
  in_arbeit: "In Arbeit",
  abgeschlossen: "Auftrag abgeschlossen",
  nicht_durchfuehrbar: "Nicht durchführbar",
};

const PRIO_LABEL = { sofort: "Sofort", dringend: "Dringend", normal: "Normal", aufschiebbar: "Aufschiebbar" };

const KONFLIKT_TEXTE = {
  auftrag_zurueckgezogen: "Auftrag wurde zurückgezogen – Status nicht übernommen",
  ungueltiger_uebergang: "Statuswechsel nicht möglich",
  kein_einheitenkontext: "Gerät ist keiner aktiven Einheit mehr zugeordnet",
  simulation_nur_lesend: "Simulation in Echtlage nur lesend",
  nicht_gefunden: "Auftrag nicht mehr vorhanden",
  client_uuid_vergeben: "Doppelte Aktion",
  aktiver_auftrag: "Anderer Auftrag läuft noch",
};

const HINWEIS_TEXTE = {
  auftrag_geaendert: "Auftrag war inzwischen geändert – bitte prüfen",
  erfasst_at_korrigiert: "Gerätezeit war ungenau, Serverzeit verwendet",
};

// Alpine-Zustand besteht aus Proxys, die IndexedDB nicht klonen kann (DataCloneError):
// vor dem Speichern in eine reine Datenkopie umwandeln.
const reineDaten = (wert) => JSON.parse(JSON.stringify(wert ?? null));

const cookie = (name) => document.cookie.split(";")
  .map((value) => value.trim())
  .find((value) => value.startsWith(`${name}=`))
  ?.slice(name.length + 1) || "";

function registriere() {
  Alpine.data("einheitModus", (config) => ({
    ...config,
    lageId: config.lage_id,
    einheitId: config.einheit_id,
    simEinheitId: config.sim_einheit_id,
    startDispatchId: config.start_dispatch_id,
    kopf: {}, aktuell: null, weitere: [], abgeschlossen: [], zurueckgezogen: [], detail: null,
    online: navigator.onLine, syncText: "", outboxOffen: false, outboxListe: [], verlauf: [],
    flyout: null, konfliktEintrag: null, meldungText: "", felder: {}, grund: "", fotoKommentar: "",
    entwurfHinweis: false, auftragGeaendert: false, hinweisBanner: "", neuIds: [], etag: null,
    ws: null, wsTimer: null, wsVersuche: 0, pollTimer: null, retryTimer: null, lastLoad: 0, outbox: null,
    gezeigteHinweise: new Set(), geladenAb: new Date().toISOString(),
    karte: null, kartenMarker: null, zugangBeendet: false,

    // Abgeleitete Anzeigezustände.
    get naechster() { return this.weitere[0] || null; },
    get schreibbar() { return Boolean(config.schreibbar); },
    get hatKoordinaten() {
      return Number.isFinite(Number(this.detail?.stelle?.lat)) && Number.isFinite(Number(this.detail?.stelle?.lng));
    },
    get osmUrl() {
      const stelle = this.detail?.stelle;
      return this.hatKoordinaten
        ? `https://www.openstreetmap.org/?mlat=${stelle.lat}&mlon=${stelle.lng}&zoom=17`
        : "#";
    },
    get outboxText() {
      const anzahl = this.outboxListe.length;
      const fehler = this.outboxListe.filter((item) => ["fehler", "konflikt"].includes(item.status)).length;
      return fehler ? `⚠ ${fehler} Fehler/Konflikt` : `⏳ ${anzahl} ausstehend`;
    },
    // Noch nicht vom Server bestätigter Status des offenen Auftrags (letzter Outbox-Eintrag).
    get ausstehenderStatus() {
      const id = this.detail?.auftrag?.dispatch_id;
      const offen = this.outboxListe.filter((item) => item.typ === "status" && item.dispatch_id === id
        && ["ausstehend", "fehler_netz"].includes(item.status));
      return offen.length ? offen[offen.length - 1].payload.status : null;
    },
    // Status, von dem der nächste Schritt ausgeht: ausstehender lokaler Status vor Serverstand.
    get wirksamerStatus() { return this.ausstehenderStatus || this.detail?.auftrag?.einheit_status; },
    get statusText() {
      return STATUS[this.wirksamerStatus]?.[1] || "";
    },
    statusLabel(status) { return STATUS_LABEL[status] || status || ""; },
    get flyoutTitel() {
      return {
        lagemeldung: "Lagemeldung", massnahmen: "Maßnahme", note: "Notiz", foto: "Foto erfassen",
        nicht_durchfuehrbar: "Nicht durchführbar",
      }[this.flyout] || "";
    },

    // Hilfsfunktionen für API und Darstellung.
    headers() { return this.simulation ? { "X-EC-Einheit-Sim": String(this.simEinheitId) } : {}; },
    zeit(value) {
      return value ? new Intl.DateTimeFormat("de-AT", {
        hour: "2-digit", minute: "2-digit",
      }).format(new Date(value)) : "";
    },
    prioClass(priority) {
      const farbe = { sofort: "red", dringend: "orange", normal: "yellow", aufschiebbar: "muted" }[priority] || "muted";
      return `einheit-chip--${farbe}`;
    },
    prioLabel(priority) { return PRIO_LABEL[priority] || "ohne Priorität"; },
    konfliktText(code) { return KONFLIKT_TEXTE[code] || `Konflikt: ${code || "unbekannt"}`; },
    hinweisText(hinweis) { return HINWEIS_TEXTE[hinweis] || ""; },
    retrySekunden(item) {
      if (!item.naechster_versuch_at) return 0;
      return Math.max(0, Math.ceil((new Date(item.naechster_versuch_at) - Date.now()) / 1000));
    },
    outboxStatus(item) {
      if (item.status === "blockiert_zugang") return "Zugang beendet – nicht übermittelt";
      if (item.status === "konflikt") return this.konfliktText(item.letzter_fehler);
      if (item.status === "fehler") return `Fehler: ${item.letzter_fehler || "unbekannt"}`;
      if (item.status === "fehler_netz") return `↻ erneuter Versuch in ${this.retrySekunden(item)} s`;
      return "⏳ nur auf diesem Gerät";
    },

    // Initialisierung und Navigation.
    async init() {
      navigator.storage?.persist?.().catch(() => {});
      this.outbox = createOutbox({
        indexedDB: window.indexedDB,
        fetch: window.fetch.bind(window),
        csrfToken: () => decodeURIComponent(cookie("ec_csrf")),
        simEinheitId: this.simulation ? this.simEinheitId : null,
        dbName: this.simulation ? `ec-einheit-sim-${this.simEinheitId}` : (window.EINHEIT_PRINCIPAL || "") .startsWith("gk:") ? `ec-einheit-gk-${this.einheitId}` : "ec-einheit",
        onTerminal: () => { void this.zugangBeenden(); },
      });
      await this.outbox.bereinigen();
      this.outbox.onChange(() => {
        void this.outboxAktualisieren();
        void this.laden(true);
      });
      window.addEventListener("popstate", () => { void this.navigiereZuPfad(); });
      window.addEventListener("online", () => {
        this.online = true;
        if (!this.zugangBeendet) void this.flush();
        this.verbinden();
      });
      window.addEventListener("offline", () => { this.online = false; });
      document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
          if (!this.zugangBeendet) void this.flush();
          void this.laden(true);
        }
      });
      await this.laden();
      await this.outboxAktualisieren();
      await this.navigiereZuPfad();
      this.verbinden();
      this.pollTimer = setInterval(() => this.laden(true), 30000);
    },
    async navigiereZuPfad() {
      const treffer = location.pathname.match(/^\/einheit\/auftrag\/(\d+)$/);
      if (treffer) await this.oeffne(treffer[1], false);
      else if (location.pathname === "/einheit") this.detail = null;
      else if (this.startDispatchId && !this.detail) await this.oeffne(this.startDispatchId, false);
    },
    async laden(leise = false) {
      if (leise && Date.now() - this.lastLoad < 1000) return;
      this.lastLoad = Date.now();
      try {
        const headers = { ...this.headers() };
        if (this.etag) headers["If-None-Match"] = this.etag;
        const response = await fetch("/einheit/api/zustand", { headers, credentials: "same-origin" });
        if (response.status === 304) return;
        if (response.status === 401) {
          const data = await response.json().catch(() => null);
          if (/^zugang_(widerrufen|abgelaufen|ungueltig)$/.test(data?.code || "")) await this.zugangBeenden();
        }
        if (!response.ok) throw new Error("netzwerk");
        // Antwort aus dem Service-Worker-Cache: wie offline behandeln, nicht als frisch anzeigen.
        if (response.headers.get("X-EC-Offline") === "1") throw new Error("offline");
        const data = await response.json();
        this.etag = response.headers.get("ETag") || data.etag;
        this.zustand(data);
        await this.outbox.zustandSpeichern({ data, at: new Date().toISOString() });
        this.online = true;
        this.syncText = `Sync ${this.zeit(data.server_time)}`;
      } catch (error) {
        const cached = await this.outbox.zustandLaden();
        if (cached?.data) {
          this.zustand(cached.data);
          this.syncText = `Offline – Stand ${this.zeit(cached.at)}`;
        }
        this.online = false;
      }
    },
    zustand(data) {
      const vorher = new Set(this.weitere.map((item) => item.dispatch_id));
      this.kopf = data.kopf || {};
      this.aktuell = data.kategorien?.aktuell || null;
      this.weitere = data.kategorien?.weitere || [];
      this.abgeschlossen = data.kategorien?.abgeschlossen || [];
      this.zurueckgezogen = data.kategorien?.zurueckgezogen || [];
      const neu = this.weitere.filter((item) => vorher.size && !vorher.has(item.dispatch_id)).map((item) => item.dispatch_id);
      if (neu.length) {
        this.neuIds.push(...neu);
        new Audio("/static/audio/alert.mp3").play().catch(() => {});
      }
    },
    async oeffne(id, push = true) {
      id = Number(id);
      this.neuIds = this.neuIds.filter((neuId) => neuId !== id);
      try {
        const response = await fetch(`/einheit/api/auftrag/${id}`, {
          headers: this.headers(), credentials: "same-origin",
        });
        if (!response.ok) throw new Error("offline");
        const ausCache = response.headers.get("X-EC-Offline") === "1";
        const versionVorher = this.detail?.auftrag?.version;
        this.detail = await response.json();
        this.detail.offline = ausCache;
        this.auftragGeaendert = Boolean(versionVorher && versionVorher !== this.detail.auftrag.version);
        this.karteAktualisieren();
        if (push) {
          const query = this.simulation ? `?sim=${this.simEinheitId}` : "";
          history.pushState({}, "", `/einheit/auftrag/${id}${query}`);
        }
      } catch (error) {
        // Offline: Basisdaten aus dem zuletzt geladenen Zustand anzeigen.
        const auftrag = [this.aktuell, ...this.weitere, ...this.abgeschlossen, ...this.zurueckgezogen]
          .find((item) => item?.dispatch_id === id);
        if (auftrag && this.detail?.auftrag?.dispatch_id !== id) {
          this.detail = { auftrag, stelle: auftrag, offline: true };
          this.karteAktualisieren();
        } else if (this.detail) {
          this.detail.offline = true;
        }
        if (auftrag && push) {
          const query = this.simulation ? `?sim=${this.simEinheitId}` : "";
          history.pushState({}, "", `/einheit/auftrag/${id}${query}`);
        }
      }
    },
    zurueck() {
      this.detail = null;
      const query = this.simulation ? `?sim=${this.simEinheitId}` : "";
      history.pushState({}, "", `/einheit${query}`);
    },
    karteAktualisieren() {
      if (!this.hatKoordinaten) return;
      if (typeof window.L === "undefined") {
        window.addEventListener("load", () => this.karteAktualisieren(), { once: true });
        return;
      }
      const stelle = this.detail.stelle;
      const latlng = [Number(stelle.lat), Number(stelle.lng)];
      this.$nextTick(() => {
        const element = this.$refs.einsatzKarte;
        if (!element) return;
        if (!this.karte) {
          this.karte = window.L.map(element, { zoomControl: true, attributionControl: true });
          window.EinsatzcockpitMapConfig?.addOsmTileLayer(this.karte);
        }
        this.karte.setView(latlng, 17);
        if (this.kartenMarker) this.kartenMarker.remove();
        this.kartenMarker = window.L.circleMarker(latlng, {
          radius: 9, color: "#fff", weight: 2, fillColor: "#b71921", fillOpacity: 1,
        }).addTo(this.karte);
        requestAnimationFrame(() => this.karte.invalidateSize());
      });
    },

    // Erfassen von Status, Meldung und Foto.
    async naechsterStatus() {
      const status = STATUS[this.wirksamerStatus]?.[0];
      if (!status) return;
      if (status === "abgeschlossen") {
        this.flyout = "abschluss_bestaetigen";
        return;
      }
      await this.statusErfassen(status);
    },
    async statusErfassen(status, unterbrechen = false) {
      if (status === "nicht_durchfuehrbar" && !this.grund.trim()) return;
      await this.statusInOutbox(status, unterbrechen);
    },
    async statusInOutbox(status, unterbrechen = false) {
      await this.outbox.erfassen({
        typ: "status", dispatch_id: this.detail.auftrag.dispatch_id, einheit_id: this.einheitId,
        auftrag_version: this.detail.auftrag.version,
        payload: reineDaten({ status, grund: this.grund || undefined, unterbrechen: unterbrechen || undefined }),
      });
      // Die Anzeige "nur auf diesem Gerät" kommt aus ausstehenderStatus (Outbox), nicht aus detail.
      this.grund = "";
      this.flyout = null;
    },
    async oeffneMeldung(art) {
      this.flyout = art;
      this.entwurfHinweis = false;
      const key = `${this.detail.auftrag.dispatch_id}:${art}`;
      const entwurf = await this.outbox.entwurfLaden(key);
      if (entwurf) {
        this.meldungText = entwurf.text || "";
        this.felder = entwurf.felder || {};
        this.entwurfHinweis = true;
      }
      this.$nextTick(() => this.$refs.meldung?.focus());
    },
    async entwurfSpeichern() {
      if (!this.detail || !this.flyout) return;
      await this.outbox.entwurfSpeichern(`${this.detail.auftrag.dispatch_id}:${this.flyout}`, reineDaten({
        text: this.meldungText, felder: this.felder,
      }));
    },
    async meldungErfassen() {
      if (!this.meldungText.trim()) return;
      const art = this.flyout;
      await this.outbox.erfassen({
        typ: "meldung", dispatch_id: this.detail.auftrag.dispatch_id, einheit_id: this.einheitId,
        auftrag_version: this.detail.auftrag.version,
        payload: reineDaten({ art, text: this.meldungText, felder: this.felder }),
      });
      await this.outbox.entwurfLoeschen(`${this.detail.auftrag.dispatch_id}:${art}`);
      this.meldungText = "";
      this.felder = {};
      this.flyout = null;
    },
    async fotoErfassen() {
      const input = this.$refs.foto;
      if (!input?.files?.length) return;
      if (window.compressFilesInPlace) await window.compressFilesInPlace(input);
      for (const file of input.files) {
        await this.outbox.erfassen({
          typ: "foto", dispatch_id: this.detail.auftrag.dispatch_id, einheit_id: this.einheitId,
          auftrag_version: this.detail.auftrag.version, blob: file, dateiname: file.name,
          payload: { kommentar: this.fotoKommentar },
        });
      }
      this.flyout = null;
    },

    // Outbox, Rückoff und Konfliktauflösung.
    async outboxAktualisieren() {
      this.outboxListe = await this.outbox.liste();
      this.verlauf = await this.outbox.verlauf(20);
      // Hinweise nur einmal und nur für Bestätigungen seit dem Laden der Seite zeigen.
      for (const item of this.verlauf) {
        const hinweis = this.hinweisText(item.antwort?.hinweis);
        if (!hinweis || this.gezeigteHinweise.has(item.client_uuid)) continue;
        this.gezeigteHinweise.add(item.client_uuid);
        if (item.server_bestaetigt_at >= this.geladenAb) this.hinweisBanner = hinweis;
      }
      this.konfliktPruefen();
      this.retryPlanen();
    },
    konfliktPruefen() {
      if (this.flyout === "aktiver_auftrag" || !this.detail) return;
      const eintrag = this.outboxListe.find((item) => item.typ === "status"
        && item.status === "konflikt" && item.letzter_fehler === "aktiver_auftrag"
        && item.dispatch_id === this.detail.auftrag.dispatch_id);
      if (eintrag) {
        this.konfliktEintrag = eintrag;
        this.flyout = "aktiver_auftrag";
      }
    },
    async konfliktEntscheiden(unterbrechen) {
      const eintrag = this.konfliktEintrag;
      if (!eintrag) return;
      await this.outbox.verwerfen(eintrag.client_uuid);
      this.konfliktEintrag = null;
      this.flyout = null;
      if (unterbrechen) await this.statusInOutbox(eintrag.payload.status, true);
      else this.hinweisBanner = "Status nicht geändert";
    },
    retryPlanen() {
      clearTimeout(this.retryTimer);
      void this.outbox.naechsterVersuchIn().then((wartezeit) => {
        if (wartezeit === null) return;
        this.retryTimer = setTimeout(() => { void this.flush(); }, wartezeit);
      });
    },
    async flush() {
      if (this.zugangBeendet) return;
      await this.outbox.flush();
      await this.outboxAktualisieren();
    },
    async erneut(id) { await this.outbox.erneutSenden(id); },
    verwerfen(id) {
      this.konfliktEintrag = this.outboxListe.find((item) => item.client_uuid === id) || null;
      this.flyout = "verwerfen_bestaetigen";
    },
    async verwerfenBestaetigen() {
      if (this.konfliktEintrag) await this.outbox.verwerfen(this.konfliktEintrag.client_uuid);
      this.konfliktEintrag = null;
      this.flyout = null;
    },

    swCacheLeeren() { navigator.serviceWorker?.controller?.postMessage({ type: "einheit-cache-leeren" }); },
    async zugangBeenden() {
      if (this.zugangBeendet) return;
      this.zugangBeendet = true;
      await this.outbox.blockiereZugang();
      await this.outbox.zustandLeeren();
      await this.outboxAktualisieren();
      this.hinweisBanner = `Zugang beendet - ${this.outboxListe.filter((item) => item.status === "blockiert_zugang").length} Meldungen nicht übermittelt. Bitte Einsatzleitung informieren.`;
      this.swCacheLeeren();
      clearTimeout(this.wsTimer);
      this.ws?.close();
    },
    async lokaleDatenLoeschen() {
      if (this.outboxListe.length && !confirm("Lokale Daten und nicht übermittelte Meldungen wirklich löschen?")) return;
      this.swCacheLeeren();
      const name = this.outbox ? (this.simulation ? `ec-einheit-sim-${this.simEinheitId}` : (window.EINHEIT_PRINCIPAL || "").startsWith("gk:") ? `ec-einheit-gk-${this.einheitId}` : "ec-einheit") : null;
      if (name) indexedDB.deleteDatabase(name);
      if ((window.EINHEIT_PRINCIPAL || "").startsWith("gk:")) {
        await fetch("/gk/abmelden", { method: "POST", headers: { "X-CSRF-Token": decodeURIComponent(cookie("ec_csrf")) }, credentials: "same-origin" }).catch(() => {});
      }
      location.href = "/gk";
    },

    // Live-Synchronisation.
    verbinden() {
      if (this.zugangBeendet || !this.lageId || this.ws?.readyState === WebSocket.OPEN) return;
      const schema = location.protocol === "https:" ? "wss" : "ws";
      const path = window.EINHEIT_WS_PATH || `/ws/lage/${this.lageId}`;
      const ws = this.ws = new WebSocket(`${schema}://${location.host}${path}`);
      ws.addEventListener("open", () => {
        this.online = true;
        this.wsVersuche = 0;
        void this.flush();
        void this.laden(true);
        // Der Server beantwortet den Klartext "ping" mit "pong" (app/routers/ws.py).
        const ping = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send("ping");
        }, 25000);
        ws.addEventListener("close", () => clearInterval(ping), { once: true });
      });
      ws.addEventListener("message", (event) => {
        if (event.data === "pong") return;
        try {
          const nachricht = JSON.parse(event.data);
          if (nachricht.type === "zugang:widerrufen") { void this.zugangBeenden(); return; }
          const betrifftEinheit = nachricht.type === "einheit:changed"
            && nachricht.einheit_id === this.einheitId;
          const betrifftStelle = ["site:card_changed", "site_phase_changed"].includes(nachricht.type)
            && [this.aktuell, ...this.weitere].some((item) => item?.site_id === nachricht.site_id);
          if (betrifftEinheit || betrifftStelle) {
            void this.laden(true);
            if (this.detail?.auftrag?.dispatch_id) void this.oeffne(this.detail.auftrag.dispatch_id, false);
          }
        } catch (error) {
          // Ungültige WebSocket-Nachrichten beeinflussen die Tablet-Ansicht nicht.
        }
      });
      ws.addEventListener("close", () => {
        this.online = false;
        if (this.zugangBeendet) return;
        clearTimeout(this.wsTimer);
        // Wiederverbinden mit wachsendem Abstand (1 s, 2 s, 4 s … max. 30 s).
        const warten = Math.min(30000, 1000 * (2 ** this.wsVersuche));
        this.wsVersuche += 1;
        this.wsTimer = setTimeout(() => this.verbinden(), warten);
      });
      ws.addEventListener("error", () => ws.close());
    },
  }));
}

if (window.Alpine) registriere();
else document.addEventListener("alpine:init", registriere, { once: true });

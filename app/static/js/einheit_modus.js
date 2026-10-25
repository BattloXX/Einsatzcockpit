import { createOutbox, zufallsUuid } from "/static/js/einheit_outbox.js";
import { EinheitKarte, markerDarstellung, markerIcon, hatKoordinaten, zielhinweiseLayer } from "/static/js/einheit_karte.js";
import { istGeaendert, vergleicheZustand } from "/static/js/einheit_aenderungen.js";
import { auftragsartAnzeige, prioritaetAnzeige } from "/static/js/einheit_darstellung.js";
import { auftragsfuehrung } from "/static/js/einheit_ablauf.js";
import { meldungsverlauf } from "/static/js/einheit_verlauf.js";

import { kartenziel } from "/static/js/einheit_kartenziel.js";
import { gefahrenHinweise, mobileBedienung } from "/static/js/einheit_bedienung.js";

const STATUS_LABEL = {
  zugewiesen: "Zugewiesen",
  bestaetigt: "Übernommen / bestätigt",
  anfahrt: "Anfahrt",
  vor_ort: "Vor Ort",
  in_arbeit: "In Arbeit",
  abgeschlossen: "Fertig gemeldet",
  nicht_durchfuehrbar: "Nicht durchführbar",
};

const KONFLIKT_TEXTE = {
  auftrag_zurueckgezogen: "Auftrag wurde zurückgezogen – Status nicht übernommen",
  ungueltiger_uebergang: "Statuswechsel nicht möglich",
  kein_einheitenkontext: "Gerät ist keiner aktiven Einheit mehr zugeordnet",
  einheit_gewechselt: "Einheitenzuordnung geändert – alte Übermittlung nicht gesendet",
  simulation_nur_lesend: "Simulation in Echtlage nur lesend",
  nicht_gefunden: "Auftrag nicht mehr vorhanden",
  client_uuid_vergeben: "Doppelte Aktion",
  aktiver_auftrag: "Anderer Auftrag läuft noch",
  stelle_in_erkundung: "Einsatzstelle ist noch in Erkundung – Abarbeitung erst nach Bewertung",
  auftrag_geaendert: "Auftrag geändert – bitte aktuellen Auftrag prüfen und neu entscheiden",
  status_geaendert: "Bearbeitungsstand geändert – bitte aktuellen Auftrag prüfen",
  erkundungsergebnis_erforderlich: "Erkundungsergebnis fehlt oder gehört zu einem älteren Auftrag – Auftrag prüfen und Ergebnis neu erfassen",
  schnellmeldung_ungueltig: "Schnellmeldung passt nicht zum aktuellen Auftrag – bitte Auftrag prüfen",
  text_ungueltig: "Meldung ist leer oder zu lang (höchstens 4.000 Zeichen)",
  grund_erforderlich: "Bitte begründen, warum der Auftrag nicht durchführbar ist",
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
    ...meldungsverlauf(config),
    ...kartenziel(),
    ...mobileBedienung(),
    lageId: config.lage_id,
    einheitId: config.einheit_id,
    simEinheitId: config.sim_einheit_id,
    benutzerEinheitId: config.benutzer_einheit_id,
    startDispatchId: config.start_dispatch_id,
    kopf: {}, aktuell: null, weitere: [], abgeschlossen: [], zurueckgezogen: [], detail: null,
    detailAnfrage: 0, zustandAnfrage: 0, detailZielId: null, detailLaden: false, statusSpeichern: false, navigationBenutzt: false,
    detailHintergrundabgleich: false,
    statusTippSperreId: null, statusTippTimer: null, ergebnisSpeichern: false, ergebnisLaden: false, ergebnisLadeToken: 0,
    aktionsFehler: "", meldungAuftrag: null, meldungSpeichern: false, meldungLaden: false, meldungToken: 0,
    schnellmeldung: "", erkundungAuswahl: "", fotoSpeichern: false, meldungFotosVorhanden: false,
    kannRessourcePflegen: false, ressource: { personal: {}, ausstattung: [] }, neueAusstattung: {}, neuePerson: {},
    online: navigator.onLine, syncText: "", outboxVerfuegbar: false,
    outboxListe: [], verlauf: [], outboxAnfrage: 0, retryAnfrage: 0,
    flyout: null, konfliktEintrag: null, statusDialogAuftrag: null, meldungText: "", felder: {}, grund: "", fotoKommentar: "",
    erkundungLage: "", erkundungMassnahmen: "", ergebnisEntwurfHinweis: "",
    statusMeldungText: "", statusMeldungFelder: {}, statusMeldungFotos: [],
    entwurfHinweis: false, auftragGeaendert: false, hinweisBanner: "", hinweisRot: false,
    neuIds: [], etag: null,
    ws: null, wsTimer: null, wsVersuche: 0, pollTimer: null, retryTimer: null, lastLoad: 0, outbox: null,
    gezeigteHinweise: new Set(), geladenAb: new Date().toISOString(),
    karte: null, kartenMarker: null, uebersichtKarte: null, fertigeAufKarte: false,
    standortHinweis: "", standort: null, zugangBeendet: false,
    geladen: false, ladeFehler: "",
    nummerHinweis: false, nummerGeschlossen: false, nummerCodeGesendet: false,
    nummerTelefon: "", nummerCode: "", nummerMaske: "", nummerMeldung: "",
    verleihAktiv: false, materialKatalog: [], materialPositionen: [{ bezeichnung: "", menge: 1 }],
    materialName: "", materialTelefon: "", materialSmsSenden: true, materialPinSenden: false,
    materialNotizen: "",
    aktiverBereich: "einsaetze",
    mobil: window.matchMedia("(max-width: 760px)").matches,
    letzterZustand: null, geseheneVersionen: {},

    // Abgeleitete Anzeigezustände.
    get detailGefahren() { return gefahrenHinweise(this.detail); },
    get zentralerVerlauf() { return this.verlaufEintraege(null).reverse(); },
    get stellenVerlauf() { return this.verlaufEintraege(this.detail?.auftrag?.dispatch_id); },
    get eigeneUebermittlungen() {
      return this.outboxListe.filter(item => item.einheit_id === this.einheitId
        || (item.einheit_id == null && this.auftragZurAktion(item)));
    },
    get naechster() { return this.weitere[0] || null; },
    get fokusAuftrag() { return this.aktuell || this.naechster; },
    get weitereAuftraege() { return this.aktuell ? this.weitere : this.weitere.slice(1); },
    get kartenZiel() { return this.ermittleKartenZiel(); },
    get kartenHinweise() { return this.ermittleKartenHinweise(); },
    get kartenSperrenText() { return this.ermittleKartenSperrenText(); },
    get kartenAuftraege() {
      return [this.aktuell, ...this.weitere, ...(this.fertigeAufKarte ? this.abgeschlossen : [])].filter(Boolean);
    },
    auftragsartAnzeige,
    prioritaetAnzeige,
    auftragsartIcon(wert) {
      // Iconname stammt ausschließlich aus der festen Anzeigezuordnung, niemals aus Auftrags-/Meldungstext.
      return window.ecpIcon(auftragsartAnzeige(wert).icon, { size: "sm" });
    },
    auftragZurAktion(item) {
      if (item?.dispatch_id == null) return null;
      return [this.aktuell, ...this.weitere, ...this.abgeschlossen, ...this.zurueckgezogen]
        .find((auftrag) => auftrag?.dispatch_id === item.dispatch_id) || null;
    },
    get dialogAuftrag() {
      if (["verwerfen_bestaetigen", "aktiver_auftrag"].includes(this.flyout)) return this.auftragZurAktion(this.konfliktEintrag);
      if (["abschluss_bestaetigen", "unterbrechen_bestaetigen", "nicht_durchfuehrbar"].includes(this.flyout)) {
        return this.statusDialogAuftrag;
      }
      if (["lagemeldung", "massnahmen", "note"].includes(this.flyout)) return this.meldungAuftrag;
      return this.detail?.auftrag || null;
    },
    get meldungsZiele() { return [this.aktuell, ...this.weitere, ...this.abgeschlossen, ...this.zurueckgezogen].filter(Boolean); },
    get schnellmeldungsAuswahl() { return this.meldungAuftrag?.schnellmeldungen || []; },
    get kannMeldungSenden() {
      return this.schreibbar && !this.zugangBeendet && this.outboxVerfuegbar && this.meldungAuftrag
        && ["lagemeldung", "massnahmen", "note"].includes(this.flyout)
        && !this.meldungSpeichern && !this.meldungLaden
        && (this.schnellmeldung || this.meldungText.trim() || Object.values(this.felder).some(v => v?.trim()));
    },
    get erkundungErgebnisGueltig() {
      return (this.statusDialogAuftrag?.schnellmeldungen || []).some(m => m.code === this.erkundungAuswahl)
        && (this.erkundungAuswahl === "keine_massnahmen"
          || (this.erkundungLage + this.erkundungMassnahmen).replace(/[^\p{L}\p{N}]/gu, "").length >= 3);
    },
    get diktatVerfuegbar() { return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition); },
    get statusDialogVeraltet() {
      if (!["abschluss_bestaetigen", "unterbrechen_bestaetigen", "nicht_durchfuehrbar"].includes(this.flyout)) return false;
      const original = this.statusDialogAuftrag;
      const aktuell = this.detail?.auftrag;
      const ziel = { abschluss_bestaetigen: "abgeschlossen", unterbrechen_bestaetigen: "bestaetigt",
        nicht_durchfuehrbar: "nicht_durchfuehrbar" }[this.flyout];
      return Boolean(original && (original.dispatch_id !== aktuell?.dispatch_id || original.version !== aktuell?.version
        || original.einheit_status !== aktuell?.einheit_status || aktuell?.withdrawn_at || aktuell?.beendet_at
        || !this.erlaubteStatus.includes(ziel)));
    },
    get laufenderAuftrag() {
      return [this.aktuell, ...this.weitere].find((auftrag) => auftrag
        && ["anfahrt", "vor_ort", "in_arbeit"].includes(this.lokalerStatus(auftrag) || auftrag.einheit_status)) || null;
    },
    get kannStatusMelden() {
      return this.schreibbar && !this.zugangBeendet && (!this.detailLaden || this.detailHintergrundabgleich)
        && !this.statusSpeichern
        && !this.ergebnisSpeichern && !this.ergebnisLaden && this.statusTippSperreId !== this.detail?.auftrag?.dispatch_id
        && !this.ausstehenderStatus && !this.detail?.auftrag?.withdrawn_at && !this.detail?.auftrag?.beendet_at;
    },
    get konfliktAktiverName() {
      return this.konfliktEintrag?.konflikt_details?.bezeichnung || this.laufenderAuftrag?.bezeichnung || 'Laufender Auftrag';
    },
    get verbindungsText() {
      if (this.zugangBeendet) return "Zugang beendet";
      if (this.ladeFehler === "anmeldung") return "Anmeldung abgelaufen";
      if (!this.geladen && !this.ladeFehler) return "Verbindung wird geprüft";
      return this.online ? "Verbunden" : "Offline – letzter verfügbarer Stand";
    },
    auftragKurztext(auftrag) {
      const text = (auftrag.auftrag || auftrag.einsatzgrund || "Kein zusätzlicher Auftragstext").trim();
      return text.length > 240 ? `${text.slice(0, 240)}…` : text;
    },
    lokalerStatus(auftrag) {
      const eintraege = this.outboxListe.filter((item) => item.typ === "status"
        && item.dispatch_id === auftrag.dispatch_id && ["ausstehend", "fehler_netz"].includes(item.status));
      return eintraege.length ? eintraege[eintraege.length - 1].payload.status : null;
    },
    bearbeitungsstand(auftrag) {
      if (auftrag?.withdrawn_at) return "Zurückgezogen";
      const status = auftrag?.einheit_status;
      if (["anfahrt", "vor_ort", "in_arbeit"].includes(status)) return `Aktiv · ${STATUS_LABEL[status]}`;
      if (status === "abgeschlossen") return auftrag.auftragsart === "erkundung" ? "Erkundet" : "Fertig gemeldet";
      return STATUS_LABEL[status] || auftrag?.einheit_status_label || "";
    },
    get istErkundung() { return this.detail?.auftrag?.auftragsart === "erkundung"; },
    get erlaubteStatus() {
      const erlaubt = this.detail?.auftrag?.erlaubte_status;
      return Array.isArray(erlaubt) ? erlaubt : [];
    },
    get statusKonflikt() {
      return this.outboxListe.filter((item) => item.typ === "status" && item.status === "konflikt"
        && item.dispatch_id === this.detail?.auftrag?.dispatch_id).slice(-1)[0];
    },
    get hauptAktion() {
      return auftragsfuehrung(this.detail?.auftrag, {
        schreibbar: this.schreibbar && !this.zugangBeendet, ausstehend: this.ausstehenderStatus,
        konflikt: this.statusKonflikt && this.statusKonflikt.client_uuid !== this.gepruefterKonfliktId,
      });
    },
    get primaererStatus() { return this.hauptAktion.status; },
    get primaererText() { return this.hauptAktion.text; },
    gepruefterKonfliktId: null,
    get zwischenStatus() {
      return ["bestaetigt", "anfahrt", "vor_ort", "in_arbeit", "abgeschlossen"]
        .filter((status) => status !== this.primaererStatus && this.erlaubteStatus.includes(status));
    },
    statusAktion(status) {
      return { bestaetigt: "Bearbeitung unterbrechen", anfahrt: "Anfahrt melden", vor_ort: "Vor Ort melden",
        in_arbeit: "In Arbeit setzen", abgeschlossen: this.istErkundung ? "Erkundung abschließen" : "Fertig melden" }[status];
    },
    get ohneKartenposition() {
      return this.kartenAuftraege.filter(auftrag => !hatKoordinaten(auftrag)).length;
    },
    get schreibbar() { return Boolean(config.schreibbar); },
    get hatKoordinaten() {
      const stelle = this.detail?.stelle;
      return hatKoordinaten(stelle);
    },
    get osmUrl() {
      const stelle = this.detail?.stelle;
      return this.hatKoordinaten
        ? `https://www.openstreetmap.org/?mlat=${stelle.lat}&mlon=${stelle.lng}&zoom=17`
        : "#";
    },
    get outboxText() {
      if (!this.outboxVerfuegbar) return "Übermittlungen nicht verfügbar";
      if (this.statusSpeichern || this.ergebnisSpeichern || this.meldungSpeichern || this.fotoSpeichern) return "Wird gespeichert …";
      const anzahl = this.eigeneUebermittlungen.length;
      const fehler = this.eigeneUebermittlungen.filter((item) => ["fehler", "konflikt"].includes(item.status)).length;
      if (fehler) return `${fehler} Übermittlungen prüfen`;
      if (anzahl) return `${anzahl} noch nicht übermittelt`;
      return this.verlauf.some(item => item.server_bestaetigt_at && item.einheit_id === this.einheitId)
        ? "Alles übermittelt" : "Keine offenen Übermittlungen";
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
    statusLabel(status) {
      // Label je Auftragsart (z. B. "Erkundet") nur für den Status des geöffneten Auftrags.
      if (status && this.detail?.auftrag && status === this.detail.auftrag.einheit_status) {
        return this.detail.auftrag.einheit_status_label;
      }
      return STATUS_LABEL[status] || status || "";
    },
    istGeaendert(auftrag) { return istGeaendert(auftrag, this.geseheneVersionen[auftrag?.dispatch_id]); },
    bereichSichtbar(bereich) { return this.aktiverBereich === bereich; },
    get flyoutTitel() {
      return {
        lagemeldung: "Meldung an Einsatzleitung", massnahmen: "Maßnahme", note: "Notiz", foto: "Foto erfassen",
        material: "Material vor Ort belassen", nicht_durchfuehrbar: "Nicht durchführbar",
        abschluss_bestaetigen: this.istErkundung ? "Erkundungsergebnis" : "Fertig melden",
        unterbrechen_bestaetigen: "Bearbeitung unterbrechen", aktiver_auftrag: "Auftrag wechseln?",
      }[this.flyout] || "";
    },

    // Hilfsfunktionen für API und Darstellung.
    headers() {
      const headers = { "X-EC-Einheit-Kontext": String(this.einheitId) };
      if (this.simulation) headers["X-EC-Einheit-Sim"] = String(this.simEinheitId);
      else if (this.benutzerEinheitId) headers["X-EC-Einheit"] = String(this.benutzerEinheitId);
      return headers;
    },
    async outboxAusfuehren(aktion) {
      if (!this.outbox) {
        this.hinweisBanner = "Lokaler Speicher ist nicht verfügbar. Meldung konnte nicht gespeichert werden.";
        return null;
      }
      try {
        return await aktion(this.outbox);
      } catch (error) {
        console.warn("Einheiten-Outbox nicht verfuegbar", error);
        // ungueltiger_typ: Seite neuer als die geladene Outbox (veralteter Browser-Cache).
        this.hinweisBanner = error?.message === "ungueltiger_typ"
          ? "App-Version veraltet. Bitte Seite neu laden und erneut speichern."
          : "Lokaler Speicher ist nicht verfügbar. Meldung konnte nicht gespeichert werden.";
        return null;
      }
    },
    async outboxErfassen(daten) { return this.outboxAusfuehren((outbox) => outbox.erfassen(daten)); },
    async nummerAnfordern() {
      this.nummerMeldung = "";
      try {
        const response = await fetch("/einheit/api/nummer/anfordern", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": decodeURIComponent(cookie("ec_csrf")) },
          body: JSON.stringify({ telefon: this.nummerTelefon }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.meldung || "Code konnte nicht gesendet werden.");
        this.nummerMaske = data.maske || "";
        this.nummerCodeGesendet = true;
        this.nummerMeldung = "Code gesendet.";
      } catch (error) { this.nummerMeldung = error.message || "Code konnte nicht gesendet werden."; }
    },
    async nummerBestaetigen() {
      this.nummerMeldung = "";
      try {
        const response = await fetch("/einheit/api/nummer/bestaetigen", {
          method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": decodeURIComponent(cookie("ec_csrf")) },
          body: JSON.stringify({ code: this.nummerCode }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.meldung || "Bestätigung nicht möglich.");
        this.nummerHinweis = false;
        this.nummerMeldung = "Mobilnummer bestätigt.";
        if (data.sitzung_beendet) await this.zugangBeenden();
      } catch (error) { this.nummerMeldung = error.message || "Bestätigung nicht möglich."; }
    },
    zeit(value) {
      return value ? new Intl.DateTimeFormat("de-AT", {
        hour: "2-digit", minute: "2-digit",
      }).format(new Date(value)) : "";
    },
    prioClass(priority) {
      return prioritaetAnzeige(priority).chipKlasse;
    },
    prioLabel(priority) { return prioritaetAnzeige(priority).label; },
    konfliktText(code) { return KONFLIKT_TEXTE[code] || "Übermittlung nicht übernommen – bitte Auftrag prüfen"; },
    hinweisText(hinweis) { return HINWEIS_TEXTE[hinweis] || ""; },
    typLabel(typ) {
      return { meldung: "Meldung", foto: "Foto", status: "Status", material: "Material",
        material_foto: "Material-Foto", material_zurueck: "Material zurück",
        ressource_personal: "Personal", ressource_ausstattung: "Ausstattung" }[typ] || "Übermittlung";
    },
    rueckmeldungText(item) {
      return item.antwort?.meldung_text || [item.payload?.schnellmeldung_label, item.payload?.text,
        ...Object.values(item.payload?.felder || {})].filter(Boolean).join("\n");
    },
    retrySekunden(item) {
      if (!item.naechster_versuch_at) return 0;
      return Math.max(0, Math.ceil((new Date(item.naechster_versuch_at) - Date.now()) / 1000));
    },
    outboxStatus(item) {
      if (item.status === "blockiert_zugang") return "Nur lokal gespeichert · Zugang beendet · keine Serverbestätigung";
      if (item.status === "konflikt") return this.konfliktText(item.letzter_fehler);
      if (item.status === "fehler" && item.letzter_fehler === "foto_daten_fehlen") {
        return "Übertragung fehlgeschlagen – Foto auf diesem Gerät nicht mehr lesbar. Bitte neu aufnehmen.";
      }
      if (item.status === "fehler") return KONFLIKT_TEXTE[item.letzter_fehler] || "Übertragung fehlgeschlagen – bitte erneut senden";
      if (item.status === "fehler_netz") return `Übertragung fehlgeschlagen · nur lokal gespeichert · erneuter Versuch in ${this.retrySekunden(item)} s`;
      return "Nur lokal gespeichert · noch nicht übermittelt · nur auf diesem Gerät";
    },

    // Initialisierung und Navigation.
    async init() {
      this.bedienungInitialisieren();
      navigator.storage?.persist?.().catch(() => {});
      window.addEventListener("popstate", () => { void this.navigiereZuPfad(); });
      window.addEventListener("online", () => {
        this.online = true;
        if (!this.zugangBeendet) void this.flush(true);
        this.verbinden();
      });
      window.addEventListener("resize", () => {
        this.mobil = window.matchMedia("(max-width: 760px)").matches;
      });
      window.addEventListener("offline", () => { this.online = false; });
      // Pull-to-Refresh der Android-App und Wiederverbindung der globalen WebSocket:
      // "online" und "visibilitychange" laden bereits selbst, daher hier nur diese beiden Quellen.
      window.addEventListener("ec:resync", (event) => {
        const quelle = event.detail && event.detail.source;
        if (quelle !== "pull" && quelle !== "ws-reconnect") return;
        event.detail.handled = true;
        const geladen = this.flush(true);
        if (event.detail.promises) event.detail.promises.push(geladen);
      });
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) this.standortBeenden();
        if (!document.hidden) {
          if (!this.zugangBeendet) void this.flush(true);
        }
      });
      try {
        this.outbox = createOutbox({
          indexedDB: window.indexedDB,
          fetch: window.fetch.bind(window),
          csrfToken: () => decodeURIComponent(cookie("ec_csrf")),
          simEinheitId: this.simulation ? this.simEinheitId : null,
          benutzerEinheitId: this.benutzerEinheitId,
          einheitId: this.einheitId,
          dbName: this.outboxName(),
          onTerminal: () => { void this.zugangBeenden(); },
        });
      } catch (error) {
        // Ohne Outbox bleibt die Ansicht benutzbar (nur ohne Offline-Speicher), statt leer zu bleiben.
        console.warn("Einheiten-Outbox konnte nicht erstellt werden", error);
        this.outbox = null;
      }
      // Outbox zuerst: das erste Laden braucht sie fuer den lokalen Zustands-Cache (Offline-Start) und zum Speichern.
      const erstesLaden = this.laden();
      this.outbox?.onChange((neu) => {
        if (Array.isArray(neu)) {
          const eintraege = new Map([...this.outboxListe, ...neu].map(item => [item.client_uuid, item]));
          this.outboxListe = [...eintraege.values()].sort((a, b) => a.seq - b.seq);
        }
        void this.outboxAktualisieren();
        // Auch die Bestätigung unmittelbar nach dem lokalen Einreihen muss frisch geladen werden.
        void this.laden();
      });
      void (async () => {
        try {
          if (!this.outbox) throw new Error("Outbox nicht verfuegbar");
          await this.outbox.bereinigen();
          await this.outboxAktualisieren();
          this.geseheneVersionen = await this.outbox.metaLaden("gesehene_versionen") || {};
        } catch (error) {
          console.warn("Einheiten-Outbox nicht verfuegbar", error);
          try {
            const key = `${this.outboxName()}:gesehene_versionen`;
            this.geseheneVersionen = JSON.parse(localStorage.getItem(key) || "{}");
          } catch (storageError) { this.geseheneVersionen = {}; }
        }
      })();
      await erstesLaden;
      if (!this.navigationBenutzt) await this.navigiereZuPfad();
      this.verbinden();
      this.pollTimer = setInterval(() => this.laden(true), 30000);
    },
    waehleBereich(bereich) {
      if (!["einsaetze", "karte", "meldungen"].includes(bereich)) return;
      this.navigationBenutzt = true;
      if (this.detail || this.detailLaden) this.zurueck();
      if (bereich !== "karte") this.standortBeenden();
      this.aktiverBereich = bereich;
      if (bereich === "meldungen") void this.verlaufLaden();
      this.$nextTick(() => {
        if (bereich === "karte") {
          this.uebersichtKarteAktualisieren();
          if (!this.kartenLaden) void this.kartenZielLaden(true);
        }
      });
    },
    async navigiereZuPfad() {
      const treffer = location.pathname.match(/^\/einheit\/auftrag\/(\d+)$/);
      if (treffer) await this.oeffne(treffer[1], false);
      else if (location.pathname === "/einheit") this.detail = null;
      else if (this.startDispatchId && !this.detail) await this.oeffne(this.startDispatchId, false);
    },
    async laden(leise = false) {
      if (this.zugangBeendet) return false;
      if (leise && Date.now() - this.lastLoad < 1000) return;
      // Abgelaufene Anmeldung: automatische Abfragen hoeren auf, bis der Nutzer erneut versucht oder sich neu anmeldet
      if (leise && ["anmeldung", "kontext"].includes(this.ladeFehler)) return false;
      this.lastLoad = Date.now();
      const anfrage = ++this.zustandAnfrage;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        const headers = { ...this.headers() };
        if (this.etag) headers["If-None-Match"] = this.etag;
        const response = await fetch("/einheit/api/zustand", {
          headers, credentials: "same-origin", signal: controller.signal,
        });
        if (anfrage !== this.zustandAnfrage) return;
        if (response.status === 304) {
          if (this.aktiverBereich === "meldungen") void this.verlaufLaden();
          // Straßensperren haben einen eigenen Lebenszyklus und sind nicht Teil des Auftrags-ETags.
          if (this.aktiverBereich === "karte" && !this.detail) void this.kartenZielLaden(true);
          this.online = true;
          this.ladeFehler = "";
          this.syncText = `Zuletzt abgeglichen: ${this.zeit(new Date().toISOString())}`;
          await this.detailAktualisieren();
          return true;
        }
        if ([401, 403, 409].includes(response.status)) {
          const data = await response.json().catch(() => null);
          if (response.status === 401 || ["kein_einheitenkontext", "einheit_gewechselt"].includes(data?.code)) {
            this.ladeFehler = response.status === 401 || window.EINHEIT_PRINCIPAL ? "anmeldung" : "kontext";
            await this.zugangBeenden();
            return false;
          }
        }
        if (!response.ok) throw new Error(response.status === 401 ? "anmeldung" : "netzwerk");
        // Antwort aus dem Service-Worker-Cache: wie offline behandeln, nicht als frisch anzeigen.
        if (response.headers.get("X-EC-Offline") === "1") throw new Error("offline");
        const data = await response.json();
        if (anfrage !== this.zustandAnfrage) return;
        if (data.kopf?.einheit_id !== this.einheitId || data.kopf?.lage_id !== this.lageId) {
          this.ladeFehler = "kontext";
          await this.zugangBeenden();
          return false;
        }
        this.etag = response.headers.get("ETag") || data.etag;
        this.zustand(data);
        if (this.aktiverBereich === "meldungen") void this.verlaufLaden();
        this.online = true;
        this.geladen = true;
        this.ladeFehler = "";
        this.syncText = `Zuletzt abgeglichen: ${this.zeit(data.server_time)}`;
        await this.detailAktualisieren();
        if (anfrage !== this.zustandAnfrage) return;
        if (this.outbox) {
          try {
            await this.outbox.zustandSpeichern({ data, at: new Date().toISOString() });
          } catch (cacheError) {
            console.warn("Einheiten-Zustand konnte nicht lokal gespeichert werden", cacheError);
          }
        }
        return true;
      } catch (error) {
        if (anfrage !== this.zustandAnfrage) return;
        try {
          const cached = this.outbox ? await this.outbox.zustandLaden() : null;
          if (anfrage !== this.zustandAnfrage) return;
          if (cached?.data?.kopf?.einheit_id === config.einheit_id && cached?.data?.kopf?.lage_id === config.lage_id) {
            this.zustand(cached.data);
            this.geladen = true;
            this.syncText = `Datenstand: ${this.zeit(cached.at)}`;
          }
        } catch (cacheError) {
          console.warn("Einheiten-Zustand konnte nicht lokal geladen werden", cacheError);
        } finally {
          this.online = false;
          // Sichtbarer Hinweis statt leerer Seite: nur wenn weder Server noch lokaler Stand etwas lieferten
          if (this.ladeFehler !== "anmeldung" && !this.geladen) this.ladeFehler = "laden";
        }
        return false;
      } finally {
        clearTimeout(timeout);
      }
    },
    zustand(data) {
      const aenderungen = this.letzterZustand
        ? vergleicheZustand(this.letzterZustand, data) : null;
      const vorher = new Set([this.aktuell, ...this.weitere].filter(Boolean).map((item) => item.dispatch_id));
      this.kopf = data.kopf || {};
      this.aktuell = data.kategorien?.aktuell || null;
      this.weitere = data.kategorien?.weitere || [];
      this.abgeschlossen = data.kategorien?.abgeschlossen || [];
      this.zurueckgezogen = data.kategorien?.zurueckgezogen || [];
      this.kannRessourcePflegen = Boolean(data.kann_ressource_pflegen);
      this.nummerHinweis = Boolean(data.nummer?.hinweis);
      if (data.nummer?.maske) this.nummerMaske = data.nummer.maske;
      this.ressource = data.ressource || { personal: {}, ausstattung: [] };
      this.verleihAktiv = Boolean(data.verleih_aktiv);
      this.materialKatalog = data.material_katalog || [];
      if (this.aktuell) this.aktuell.aktuell = true;
      this.$nextTick(() => {
        if (this.aktiverBereich === "karte") {
          this.uebersichtKarteAktualisieren();
          void this.kartenZielLaden(true);
        }
      });
      const neu = this.weitere
        .filter((item) => vorher.size && !vorher.has(item.dispatch_id))
        .map((item) => item.dispatch_id);
      if (neu.length) {
        this.neuIds = [...new Set([...this.neuIds, ...neu])];
        new Audio("/static/audio/alert.mp3").play().catch(() => {});
      }
      if (aenderungen?.reihenfolgeGeaendert) {
        this.hinweisRot = false;
        const bezeichnung = aenderungen.naechster?.bezeichnung || "";
        this.hinweisBanner = `Die Leitstelle hat die Reihenfolge geändert – nächster Auftrag: ${bezeichnung}`;
      } else if (aenderungen?.hoeherePrioritaet.length) {
        this.hinweisRot = true;
        this.hinweisBanner = `Neuer Auftrag mit höherer Priorität: ${aenderungen.hoeherePrioritaet[0].bezeichnung}`;
        new Audio("/static/audio/alert.mp3").play().catch(() => {});
      } else if (aenderungen?.zurueckgezogen.length) {
        this.hinweisRot = false;
        this.hinweisBanner = `Auftrag ${aenderungen.zurueckgezogen[0].bezeichnung} wurde zurückgezogen`;
      }
      this.letzterZustand = reineDaten(data);
    },
    uebersichtKarteAktualisieren() {
      const element = this.$refs?.uebersichtKarte;
      if (!element || typeof window.L === "undefined") return;
      if (!this.uebersichtKarte) {
        this.uebersichtKarte = new EinheitKarte(element, { onSelect: (auftrag) => {
          this.markerAusgewaehlt(auftrag.dispatch_id);
        } });
        element.addEventListener("click", (event) => {
          const button = event.target.closest("button[data-dispatch-id]");
          if (button) void this.oeffne(button.dataset.dispatchId);
        });
      }
      this.uebersichtKarte.aktualisieren([this.aktuell, ...this.weitere, ...this.abgeschlossen].filter(Boolean), {
        fertige: this.fertigeAufKarte, standort: this.standort, ausgewaehlt: this.kartenZiel?.dispatch_id,
      });
      this.uebersichtKarte.hinweise(this.kartenHinweise);
      void this.kartenZielLaden();
    },
    markerHervorheben(dispatchId) { this.uebersichtKarte?.hervorheben(dispatchId); },
    markerAusgewaehlt(dispatchId) { this.kartenZielWaehlen(dispatchId); },
    meinStandort() {
      if (!this.uebersichtKarte) this.uebersichtKarteAktualisieren();
      this.standortHinweis = "Standort wird nur auf diesem Gerät angezeigt.";
      this.uebersichtKarte?.starteStandort(() => {
        this.standort = null;
        this.standortHinweis = "Standort nicht verfügbar – Karte funktioniert weiter";
      }, (standort) => {
        this.standort = standort;
        this.uebersichtKarteAktualisieren();
      });
    },
    async detailAktualisieren() {
      const id = this.detail?.auftrag?.dispatch_id;
      if (id && (!this.detailLaden || this.detailZielId === id)) await this.oeffne(id, false, true);
    },
    async oeffne(id, push = true, aktualisieren = false) {
      this.standortBeenden();
      if (push) this.navigationBenutzt = true;
      id = Number(id);
      const anfrage = ++this.detailAnfrage;
      this.detailZielId = id;
      const vorher = this.detail?.auftrag;
      // Automatisches Nachladen darf einen laufenden Tipp auf den geladenen Auftrag nicht unterbrechen.
      this.detailHintergrundabgleich = aktualisieren && vorher?.dispatch_id === id;
      this.detailLaden = true;
      if (vorher?.dispatch_id !== id) this.auftragGeaendert = false;
      this.neuIds = this.neuIds.filter((neuId) => neuId !== id);
      try {
        const response = await fetch(`/einheit/api/auftrag/${id}`, {
          headers: this.headers(), credentials: "same-origin",
        });
        if (anfrage !== this.detailAnfrage) return;
        if ([401, 403, 404].includes(response.status)) {
          this.detail = null;
          this.hinweisBanner = "Auftrag nicht mehr verfügbar – bitte Übersicht prüfen";
          return;
        }
        if (!response.ok) throw new Error("offline");
        const ausCache = response.headers.get("X-EC-Offline") === "1";
        const daten = await response.json();
        if (anfrage !== this.detailAnfrage) return;
        this.detail = daten;
        void this.verlaufLaden(id);
        this.detail.offline = ausCache;
        this.auftragGeaendert = Boolean(vorher?.dispatch_id === id && vorher.version !== this.detail.auftrag.version)
          || istGeaendert(this.detail.auftrag, this.geseheneVersionen[id]);
        if (!aktualisieren) await this.markiereGesehen(this.detail.auftrag);
        if (!aktualisieren) this.gepruefterKonfliktId = this.statusKonflikt?.client_uuid || null;
        this.karteAktualisieren();
        if (push) {
          history.pushState({}, "", this.einheitUrl(`/einheit/auftrag/${id}`));
        }
      } catch (error) {
        if (anfrage !== this.detailAnfrage) return;
        // Offline: Basisdaten aus dem zuletzt geladenen Zustand anzeigen.
        const auftrag = [this.aktuell, ...this.weitere, ...this.abgeschlossen, ...this.zurueckgezogen]
          .find((item) => item?.dispatch_id === id);
        if (auftrag && this.detail?.auftrag?.dispatch_id !== id) {
          this.detail = { auftrag, stelle: auftrag, gefahren: auftrag.gefahren || [], offline: true };
          this.karteAktualisieren();
        } else if (this.detail) {
          this.detail.offline = true;
        }
        if (auftrag && push) {
          history.pushState({}, "", this.einheitUrl(`/einheit/auftrag/${id}`));
        }
        if (auftrag) void this.verlaufLaden(id);
      } finally {
        if (anfrage === this.detailAnfrage) {
          this.detailLaden = false;
          this.detailHintergrundabgleich = false;
          this.konfliktPruefen();
        }
      }
    },
    async markiereGesehen(auftrag) {
      if (!auftrag?.dispatch_id || auftrag.version === undefined) return;
      this.geseheneVersionen = {
        ...this.geseheneVersionen, [auftrag.dispatch_id]: auftrag.version,
      };
      try {
        if (this.outbox) await this.outbox.metaSpeichern("gesehene_versionen", this.geseheneVersionen);
        else {
          const key = `${this.outboxName()}:gesehene_versionen`;
          localStorage.setItem(key, JSON.stringify(this.geseheneVersionen));
        }
      } catch (error) { /* Sichtbarkeitsmarker sind eine Komfortfunktion. */ }
    },
    zurueck() {
      this.detailAnfrage += 1;
      this.detailZielId = null;
      this.detailLaden = false;
      this.detail = null;
      history.pushState({}, "", this.einheitUrl("/einheit"));
    },
    einheitUrl(pfad) {
      const query = new URLSearchParams();
      if (this.simulation) query.set("sim", this.simEinheitId);
      else if (this.benutzerEinheitId) query.set("einheit", this.benutzerEinheitId);
      const parameter = query.toString();
      return `${pfad}${parameter ? `?${parameter}` : ""}`;
    },
    karteAktualisieren() {
      if (!this.hatKoordinaten) return;
      if (typeof window.L === "undefined") {
        window.addEventListener("load", () => this.karteAktualisieren(), { once: true });
        return;
      }
      const stelle = this.detail.stelle;
      const auftrag = { ...this.detail.auftrag, ...stelle };
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
        this.kartenMarker = window.L.marker(latlng, {
          icon: markerIcon(auftrag, null), title: markerDarstellung(auftrag, null).titel,
        }).addTo(this.karte);
        this.kartenMarker.getElement()?.setAttribute("aria-label", markerDarstellung(auftrag, null).titel);
        this.detailHinweise = zielhinweiseLayer(this.karte, this.detail, this.detailHinweise);
        requestAnimationFrame(() => this.karte.invalidateSize());
      });
    },

    // Erfassen von Status, Meldung und Foto.
    async naechsterStatus() {
      if (!this.kannStatusMelden || this.hauptAktion.art === "warten") return;
      if (this.hauptAktion.art === "pruefen") {
        await this.oeffne(this.detail.auftrag.dispatch_id, false);
        return;
      }
      const status = this.primaererStatus;
      if (!status) return;
      await this.statusErfassen(status);
    },
    statusTippenSperren(id) {
      this.statusTippSperreId = id;
      clearTimeout(this.statusTippTimer);
      this.statusTippTimer = setTimeout(() => { this.statusTippSperreId = null; }, 800);
    },
    async statusDialogOeffnen(art, rueckmeldung = null) {
      if (!this.kannStatusMelden || this.flyout) return;
      this.aktionsFehler = "";
      this.statusDialogAuftrag = reineDaten(this.detail.auftrag);
      this.statusMeldungText = rueckmeldung?.text || "";
      this.statusMeldungFelder = rueckmeldung?.felder || {};
      this.statusMeldungFotos = rueckmeldung?.fotos || [];
      this.flyout = art;
      if (art === "nicht_durchfuehrbar") this.grund = "";
      if (art !== "abschluss_bestaetigen" || !this.istErkundung) return;
      const id = this.statusDialogAuftrag.dispatch_id;
      const token = ++this.ergebnisLadeToken;
      this.ergebnisLaden = true;
      this.erkundungAuswahl = "";
      this.erkundungLage = "";
      this.erkundungMassnahmen = "";
      this.ergebnisEntwurfHinweis = "";
      const entwurf = await this.outboxAusfuehren((outbox) => outbox.entwurfLaden(`${id}:erkundung`));
      if (token !== this.ergebnisLadeToken) return;
      this.ergebnisLaden = false;
      if (this.flyout !== art || this.statusDialogAuftrag?.dispatch_id !== id) return;
      if (entwurf) {
        this.erkundungAuswahl = entwurf.auswahl || "";
        this.erkundungLage = entwurf.lage || "";
        this.erkundungMassnahmen = entwurf.massnahmen || "";
        this.ergebnisEntwurfHinweis = entwurf.version === this.statusDialogAuftrag.version
          ? "Erkundungsergebnis wiederhergestellt" : "Entwurf zu einem älteren Auftrag – bitte prüfen";
      }
    },
    flyoutSchliessen() {
      if (this.statusSpeichern || this.ergebnisSpeichern || this.meldungSpeichern || this.fotoSpeichern) return;
      this.flyout = null;
      this.meldungToken += 1;
      this.meldungLaden = false;
      this.ergebnisLadeToken += 1;
      this.ergebnisLaden = false;
      this.statusDialogAuftrag = null;
      this.aktionsFehler = "";
    },
    async ergebnisEntwurfSpeichern() {
      const auftrag = this.statusDialogAuftrag;
      if (!auftrag || this.ergebnisSpeichern) return;
      await this.outboxAusfuehren((outbox) => outbox.entwurfSpeichern(`${auftrag.dispatch_id}:erkundung`, {
        version: auftrag.version, auswahl: this.erkundungAuswahl, lage: this.erkundungLage, massnahmen: this.erkundungMassnahmen,
      }));
    },
    async statusErfassen(status, unterbrechen = false) {
      if (!this.kannStatusMelden || !this.erlaubteStatus.includes(status)) return;
      if (status === "nicht_durchfuehrbar" && !this.grund.trim()) return;
      if (status === "abgeschlossen") {
        await this.statusDialogOeffnen("abschluss_bestaetigen");
        return;
      }
      if (status === "bestaetigt" && ["anfahrt", "vor_ort", "in_arbeit"].includes(this.detail.auftrag.einheit_status)) {
        await this.statusDialogOeffnen("unterbrechen_bestaetigen");
        return;
      }
      const anderer = this.laufenderAuftrag;
      if (!unterbrechen && ["anfahrt", "vor_ort", "in_arbeit"].includes(status)
        && anderer && anderer.dispatch_id !== this.detail.auftrag.dispatch_id) {
        this.konfliktEintrag = {
          typ: "status", dispatch_id: this.detail.auftrag.dispatch_id, einheit_id: this.einheitId,
          auftrag_version: this.detail.auftrag.version,
          payload: { status, ausgangsstatus: this.detail.auftrag.einheit_status },
          konflikt_details: { dispatch_id: anderer.dispatch_id, bezeichnung: anderer.bezeichnung },
        };
        this.flyout = "aktiver_auftrag";
        return;
      }
      await this.statusInOutbox(status, unterbrechen);
    },
    async statusInOutbox(status, unterbrechen = false) {
      if (!this.kannStatusMelden || !this.erlaubteStatus.includes(status) || this.statusDialogVeraltet) return;
      const auftrag = this.statusDialogAuftrag || this.detail.auftrag;
      if (!auftrag || auftrag.dispatch_id !== this.detail.auftrag.dispatch_id) return;
      this.statusSpeichern = true;
      this.aktionsFehler = "";
      const kontext = { dispatch_id: auftrag.dispatch_id, einheit_id: this.einheitId, auftrag_version: auftrag.version };
      try {
        const paket = [];
        if (["abgeschlossen", "nicht_durchfuehrbar"].includes(status)) {
          if (this.statusMeldungText.trim() || Object.values(this.statusMeldungFelder).some(v => v?.trim())) {
            paket.push({ ...kontext, typ: "meldung", payload: reineDaten({ art: "lagemeldung",
              text: this.statusMeldungText, felder: this.statusMeldungFelder }) });
          }
          const input = this.$refs.statusFotos;
          if (input?.files?.length && window.compressFilesInPlace) await window.compressFilesInPlace(input);
          for (const file of [...this.statusMeldungFotos, ...Array.from(input?.files || [])]) {
            paket.push({ ...kontext, typ: "foto", blob: file, dateiname: file.name, payload: {} });
          }
        }
        if (this.statusDialogVeraltet || !this.schreibbar || this.zugangBeendet) return;
        paket.push({ ...kontext, typ: "status", payload: reineDaten({ status, grund: this.grund || undefined,
          unterbrechen: unterbrechen || undefined, ausgangsstatus: auftrag.einheit_status }) });
        const gespeichert = await this.outboxAusfuehren(outbox => outbox.erfassenPaket(paket));
        if (!gespeichert) { this.aktionsFehler = this.hinweisBanner; return; }
        this.statusTippenSperren(auftrag.dispatch_id);
        await this.outboxAktualisieren();
        this.grund = "";
        this.statusMeldungText = "";
        this.statusMeldungFotos = [];
        this.statusDialogAuftrag = null;
        this.flyout = null;
      } catch { this.aktionsFehler = "Rückmeldung konnte nicht gespeichert werden. Eingaben bleiben erhalten."; }
      finally { this.statusSpeichern = false; }
    },
    async erkundungAbschliessen() {
      if (!this.kannStatusMelden || this.statusDialogVeraltet || !this.erkundungErgebnisGueltig
        || !this.erlaubteStatus.includes("abgeschlossen")) return;
      const auftrag = reineDaten(this.statusDialogAuftrag);
      if (!auftrag) return;
      this.ergebnisSpeichern = true;
      this.aktionsFehler = "";
      const lage = this.erkundungLage.trim();
      const massnahmen = this.erkundungMassnahmen.trim();
      const kontext = { dispatch_id: auftrag.dispatch_id, einheit_id: this.einheitId, auftrag_version: auftrag.version };
      const paket = [];
      try {
        const ergebnisUuid = zufallsUuid();
        paket.push({ ...kontext, typ: "meldung", client_uuid: ergebnisUuid,
          payload: { art: "lagemeldung", schnellmeldung: this.erkundungAuswahl, erkundungsergebnis: true,
            schnellmeldung_label: auftrag.schnellmeldungen.find(m => m.code === this.erkundungAuswahl)?.label,
            text: [lage, massnahmen ? `Notwendige Maßnahmen: ${massnahmen}` : ""].filter(Boolean).join("\n") },
        });
        const input = this.$refs.erkundungFotos;
        if (input?.files?.length) {
          if (window.compressFilesInPlace) await window.compressFilesInPlace(input);
          for (const file of input.files) {
            paket.push({ ...kontext, typ: "foto", blob: file, dateiname: file.name,
              payload: { kommentar: "Erkundung" },
            });
          }
        }
        if (this.statusDialogVeraltet) return;
        paket.push({ ...kontext, typ: "status", payload: { status: "abgeschlossen",
          ausgangsstatus: auftrag.einheit_status, ergebnis_client_uuid: ergebnisUuid } });
        const gespeichert = await this.outboxAusfuehren((outbox) => outbox.erfassenPaket(paket));
        if (!gespeichert) { this.aktionsFehler = this.hinweisBanner; return; }
        this.statusTippenSperren(auftrag.dispatch_id);
        await this.outboxAktualisieren();
        await this.outboxAusfuehren((outbox) => outbox.entwurfLoeschen(`${auftrag.dispatch_id}:erkundung`));
        this.erkundungLage = "";
        this.erkundungMassnahmen = "";
        if (input) input.value = "";
        this.statusDialogAuftrag = null;
        this.flyout = null;
        this.waehleBereich("einsaetze");
      } catch (error) {
        console.warn("Erkundungsergebnis konnte nicht gespeichert werden", error);
        this.hinweisBanner = "Erkundungsergebnis konnte nicht gespeichert werden. Eingaben bleiben erhalten.";
        this.aktionsFehler = this.hinweisBanner;
      } finally {
        this.ergebnisSpeichern = false;
      }
    },
    async oeffneMeldung(art = "lagemeldung", ziel = this.detail?.auftrag || this.fokusAuftrag) {
      if (!this.schreibbar || this.zugangBeendet || !ziel || this.meldungSpeichern || this.meldungLaden) return;
      const token = ++this.meldungToken;
      this.meldungAuftrag = reineDaten(ziel);
      this.flyout = art;
      this.aktionsFehler = "";
      this.entwurfHinweis = false;
      this.meldungText = "";
      this.schnellmeldung = "";
      this.meldungFotosVorhanden = false;
      this.felder = {};
      this.meldungLaden = true;
      const entwurf = await this.outboxAusfuehren(outbox => outbox.entwurfLaden(`${ziel.dispatch_id}:${art}`));
      if (token !== this.meldungToken) return;
      this.meldungLaden = false;
      if (entwurf) {
        this.meldungText = entwurf.text || "";
        this.felder = entwurf.felder || {};
        this.schnellmeldung = this.schnellmeldungsAuswahl.some(m => m.code === entwurf.schnellmeldung) ? entwurf.schnellmeldung : "";
        this.entwurfHinweis = true;
      }
    },
    async meldungsZielWechseln(id) {
      if (this.meldungFotosVorhanden || this.meldungSpeichern || this.meldungLaden) return;
      const token = ++this.meldungToken;
      const art = this.flyout;
      const ziel = this.meldungsZiele.find(a => a.dispatch_id === Number(id));
      const speichern = this.entwurfSpeichern();
      this.meldungLaden = true;
      const gespeichert = await speichern;
      if (token !== this.meldungToken || this.flyout !== art) return;
      this.meldungLaden = false;
      if (!gespeichert) {
        if (this.$refs.meldungZiel) this.$refs.meldungZiel.value = String(this.meldungAuftrag.dispatch_id);
        this.aktionsFehler = this.hinweisBanner;
        return;
      }
      await this.oeffneMeldung(art, ziel);
      if (this.$refs.meldungFotos) this.$refs.meldungFotos.value = "";
    },
    async entwurfSpeichern() {
      if (!this.meldungAuftrag || !this.flyout || this.meldungLaden || this.meldungSpeichern) return false;
      const gespeichert = await this.outboxAusfuehren(outbox => outbox.entwurfSpeichern(
        `${this.meldungAuftrag.dispatch_id}:${this.flyout}`, reineDaten({
        text: this.meldungText, felder: this.felder, schnellmeldung: this.schnellmeldung,
      })));
      return gespeichert !== null;
    },
    async schnellmeldungWaehlen(meldung) {
      if (this.meldungSpeichern || this.meldungLaden) return;
      if (meldung.aktion !== "meldung") {
        const ziel = this.meldungAuftrag;
        const speichern = this.entwurfSpeichern();
        this.meldungSpeichern = true;
        const rueckmeldung = { text: this.meldungText, felder: reineDaten(this.felder), fotos: [] };
        try {
          if (!await speichern) { this.aktionsFehler = this.hinweisBanner; return; }
          const input = this.$refs.meldungFotos;
          if (input?.files?.length && window.compressFilesInPlace) await window.compressFilesInPlace(input);
          rueckmeldung.fotos = Array.from(input?.files || []);
        } catch { this.aktionsFehler = "Fotos konnten nicht vorbereitet werden. Eingaben bleiben erhalten."; return; }
        finally { this.meldungSpeichern = false; }
        this.flyoutSchliessen();
        await this.oeffne(ziel.dispatch_id, false);
        if (this.detail?.auftrag?.dispatch_id === ziel.dispatch_id) {
          const art = meldung.aktion === "abgeschlossen" ? "abschluss_bestaetigen" : "nicht_durchfuehrbar";
          await this.statusDialogOeffnen(art, rueckmeldung);
        }
        return;
      }
      this.schnellmeldung = this.schnellmeldung === meldung.code ? "" : meldung.code;
      await this.entwurfSpeichern();
    },
    async meldungErfassen() {
      if (!this.kannMeldungSenden) return;
      this.meldungSpeichern = true;
      this.aktionsFehler = "";
      const auftrag = reineDaten(this.meldungAuftrag);
      const art = this.flyout;
      const kontext = { dispatch_id: auftrag.dispatch_id, einheit_id: this.einheitId, auftrag_version: auftrag.version };
      try {
        const paket = [{ ...kontext, typ: "meldung", payload: reineDaten({ art,
          schnellmeldung_label: auftrag.schnellmeldungen?.find(m => m.code === this.schnellmeldung)?.label,
          schnellmeldung: this.schnellmeldung || undefined, text: this.meldungText, felder: this.felder }) }];
        const input = this.$refs.meldungFotos;
        if (input?.files?.length) {
          if (window.compressFilesInPlace) await window.compressFilesInPlace(input);
          for (const file of input.files) paket.push({ ...kontext, typ: "foto", blob: file, dateiname: file.name, payload: {} });
        }
        const gespeichert = await this.outboxAusfuehren(outbox => outbox.erfassenPaket(paket));
        if (!gespeichert) { this.aktionsFehler = this.hinweisBanner; return; }
        await this.outboxAusfuehren(outbox => outbox.entwurfLoeschen(`${auftrag.dispatch_id}:${art}`));
        this.meldungText = "";
        this.felder = {};
        this.flyout = null;
        await this.outboxAktualisieren();
      } catch {
        this.aktionsFehler = "Meldung konnte nicht gespeichert werden. Eingaben bleiben erhalten.";
      } finally { this.meldungSpeichern = false; }
    },
    async fotosErfassen(input, kommentar = "") {
      if (!input?.files?.length || this.fotoSpeichern || !this.schreibbar || this.zugangBeendet) return;
      const auftrag = reineDaten(this.detail?.auftrag);
      if (!auftrag) return;
      this.fotoSpeichern = true;
      try {
        if (window.compressFilesInPlace) await window.compressFilesInPlace(input);
        const paket = Array.from(input.files, file => ({
          typ: "foto", dispatch_id: auftrag.dispatch_id, einheit_id: this.einheitId,
          auftrag_version: auftrag.version, blob: file, dateiname: file.name, payload: { kommentar },
        }));
        const gespeichert = await this.outboxAusfuehren(outbox => outbox.erfassenPaket(paket));
        if (gespeichert) { input.value = ""; this.flyout = null; }
      } catch { this.aktionsFehler = "Fotos konnten nicht gespeichert werden. Bitte erneut versuchen."; }
      finally { this.fotoSpeichern = false; }
    },
    async fotoErfassen() { await this.fotosErfassen(this.$refs.foto, this.fotoKommentar); },
    async fotoDirektErfassen() { await this.fotosErfassen(this.$refs.fotoDirekt); },
    materialPositionHinzufuegen() {
      this.materialPositionen.push({ bezeichnung: "", menge: 1 });
    },
    materialPositionEntfernen(index) {
      this.materialPositionen.splice(index, 1);
      if (!this.materialPositionen.length) this.materialPositionHinzufuegen();
    },
    materialFormularZuruecksetzen() {
      this.materialPositionen = [{ bezeichnung: "", menge: 1 }];
      this.materialName = "";
      this.materialTelefon = "";
      this.materialSmsSenden = true;
      this.materialPinSenden = false;
      this.materialNotizen = "";
      if (this.$refs.materialFotos) this.$refs.materialFotos.value = "";
    },
    async materialErfassen() {
      const positionen = this.materialPositionen
        .filter((position) => position.bezeichnung?.trim())
        .map((position) => {
          const artikel = this.materialKatalog.find((item) => item.bezeichnung === position.bezeichnung.trim());
          return {
            bezeichnung: position.bezeichnung.trim(), menge: Math.max(1, Math.min(9999, Number(position.menge) || 1)),
            ...(artikel ? { artikel_id: artikel.id } : {}),
          };
        });
      if (!positionen.length) {
        this.hinweisBanner = "Mindestens eine Materialposition eingeben.";
        return;
      }
      const auftrag = this.detail.auftrag;
      const eintrag = await this.outboxErfassen({
        typ: "material", dispatch_id: auftrag.dispatch_id, einheit_id: this.einheitId,
        auftrag_version: auftrag.version,
        payload: reineDaten({ positionen, name: this.materialName.trim() || undefined,
          telefon: this.materialTelefon.trim() || undefined, sms_senden: Boolean(this.materialTelefon.trim() && this.materialSmsSenden),
          pin_senden: this.materialPinSenden || undefined, notizen: this.materialNotizen.trim() || undefined }),
      });
      if (!eintrag) return;
      const input = this.$refs.materialFotos;
      if (input?.files?.length && window.compressFilesInPlace) await window.compressFilesInPlace(input);
      for (const file of input?.files || []) {
        await this.outboxErfassen({
          typ: "material_foto", dispatch_id: auftrag.dispatch_id, einheit_id: this.einheitId,
          auftrag_version: auftrag.version, blob: file, dateiname: file.name,
          payload: reineDaten({ material_client_uuid: eintrag.client_uuid }),
        });
      }
      this.materialFormularZuruecksetzen();
      this.flyout = null;
    },
    async materialZuruecknehmen(ausleihe) {
      if (!window.confirm("Material als zurückgenommen markieren?")) return;
      await this.outboxErfassen({
        typ: "material_zurueck", dispatch_id: this.detail.auftrag.dispatch_id, einheit_id: this.einheitId,
        auftrag_version: this.detail.auftrag.version, payload: reineDaten({ ausleihe_id: ausleihe.id }),
      });
    },

    async personalSpeichern() {
      const personal = this.ressource.personal || {};
      await this.outboxErfassen({
        typ: "ressource_personal", einheit_id: this.einheitId,
        payload: reineDaten({ operation: "setzen", gesamt: Number(personal.gesamt || 0),
          fuehrung: Number(personal.fuehrung || 0), agt: Number(personal.agt || 0),
          sanitaeter: Number(personal.sanitaeter || 0), bemerkung: personal.bemerkung || null }),
      });
    },
    async personHinzufuegen() {
      const person = this.neuePerson;
      if (!person.name?.trim()) return;
      await this.outboxErfassen({ typ: "ressource_personal", einheit_id: this.einheitId,
        payload: reineDaten({ operation: "hinzufuegen", name: person.name, funktion: person.funktion || "mannschaft",
          qualifikationen: person.qualifikationen || null }) });
      this.neuePerson = {};
    },
    async personEntfernen(person) {
      await this.outboxErfassen({ typ: "ressource_personal", einheit_id: this.einheitId,
        payload: { operation: "entfernen", person_id: person.id } });
    },
    async ausstattungHinzufuegen() {
      const neu = this.neueAusstattung;
      if (!neu.bezeichnung?.trim()) return;
      await this.outboxErfassen({ typ: "ressource_ausstattung", einheit_id: this.einheitId,
        payload: reineDaten({ operation: "hinzufuegen", kategorie: neu.kategorie || "sonstiges",
          bezeichnung: neu.bezeichnung, menge: Number(neu.menge || 1), status: neu.status || "einsatzbereit" }) });
      this.neueAusstattung = {};
    },
    async ausstattungSpeichern(zeile) {
      await this.outboxErfassen({ typ: "ressource_ausstattung", einheit_id: this.einheitId,
        payload: reineDaten({ operation: "aendern", zeile_id: zeile.id, menge: Number(zeile.menge),
          status: zeile.status, bemerkung: zeile.bemerkung || null }) });
    },
    async ausstattungEntfernen(zeile) {
      await this.outboxErfassen({ typ: "ressource_ausstattung", einheit_id: this.einheitId,
        payload: { operation: "entfernen", zeile_id: zeile.id } });
    },

    // Outbox, Rückoff und Konfliktauflösung.
    async outboxAktualisieren() {
      const anfrage = ++this.outboxAnfrage;
      const daten = await this.outboxAusfuehren(async (outbox) => ({
        liste: await outbox.liste(), verlauf: await outbox.verlauf(20),
      }));
      if (anfrage !== this.outboxAnfrage) return;
      this.outboxVerfuegbar = Boolean(daten);
      if (!daten) return;
      this.outboxListe = daten.liste;
      this.verlauf = daten.verlauf;
      void this.verlaufFotosAktualisieren();
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
      if (this.flyout || !this.detail) return;
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
      if (!eintrag || this.statusSpeichern) return;
      this.statusSpeichern = true;
      if (unterbrechen) {
        const neu = await this.outboxErfassen({
          typ: "status", dispatch_id: eintrag.dispatch_id, einheit_id: this.einheitId,
          auftrag_version: eintrag.auftrag_version,
          payload: { ...reineDaten(eintrag.payload), unterbrechen: true,
            aktiver_dispatch_id: eintrag.konflikt_details?.dispatch_id },
        });
        if (!neu) { this.statusSpeichern = false; return; }
        this.statusTippenSperren(eintrag.dispatch_id);
      }
      if (eintrag.client_uuid) await this.outboxAusfuehren((outbox) => outbox.verwerfen(eintrag.client_uuid));
      this.konfliktEintrag = null;
      this.flyout = null;
      this.statusSpeichern = false;
      if (!unterbrechen) this.hinweisBanner = "Status nicht geändert";
      await this.outboxAktualisieren();
    },
    retryPlanen() {
      const anfrage = ++this.retryAnfrage;
      clearTimeout(this.retryTimer);
      if (!this.outbox) return;
      void this.outbox.naechsterVersuchIn().then((wartezeit) => {
        if (anfrage !== this.retryAnfrage || wartezeit === null || this.zugangBeendet) return;
        this.retryTimer = setTimeout(() => { void this.flush(); }, wartezeit);
      }).catch((error) => console.warn("Einheiten-Outbox nicht verfuegbar", error));
    },
    async flush(abgleichen = false) {
      if (this.zugangBeendet || !this.outbox) return;
      if (abgleichen && !await this.laden()) return;
      if (this.zugangBeendet) return;
      const erfolg = await this.outboxAusfuehren((outbox) => outbox.flush({ wiederverbunden: abgleichen }));
      if (erfolg === null) return;
      await this.outboxAktualisieren();
    },
    async erneut(id) { await this.outboxAusfuehren((outbox) => outbox.erneutSenden(id)); },
    // Nicht übermittelte Meldung als Text kopieren, damit nichts verloren geht (z. B. für Funk/Messenger).
    async kopieren(item) {
      const auftrag = this.auftragZurAktion(item);
      const text = [this.typLabel(item.typ), auftrag?.bezeichnung || "Eigener Auftrag",
        this.zeit(item.erfasst_at), this.rueckmeldungText(item)]
        .filter(Boolean).join(" · ");
      try {
        await navigator.clipboard.writeText(text);
        this.hinweisBanner = "Text kopiert.";
      } catch {
        window.prompt("Text markieren und kopieren:", text);
      }
    },
    verwerfen(id) {
      this.konfliktEintrag = this.outboxListe.find((item) => item.client_uuid === id) || null;
      this.flyout = "verwerfen_bestaetigen";
    },
    async verwerfenBestaetigen() {
      if (this.konfliktEintrag) await this.outboxAusfuehren((outbox) => outbox.verwerfen(this.konfliktEintrag.client_uuid));
      this.konfliktEintrag = null;
      this.flyout = null;
    },

    swCacheLeeren() { navigator.serviceWorker?.controller?.postMessage({ type: "einheit-cache-leeren" }); },
    async zugangBeenden() {
      if (this.zugangBeendet) return;
      this.zugangBeendet = true;
      // Auch Abbrüche über Outbox/WebSocket zeigen dieselbe Meldung wie das Laden (Reihenfolge ist zeitabhängig).
      if (!this.ladeFehler) this.ladeFehler = "anmeldung";
      this.zustandAnfrage += 1;
      this.detailAnfrage += 1;
      this.detailLaden = false;
      this.retryAnfrage += 1;
      clearTimeout(this.retryTimer);
      await this.outboxAusfuehren((outbox) => outbox.blockiereZugang());
      this.standortBeenden();
      this.kartenAnfrage += 1;
      this.kartenDaten = null;
      this.uebersichtKarte?.aktualisieren([]);
      this.uebersichtKarte?.hinweise(null);
      await this.outbox?.metaSpeichern(this.kartenCacheKey(), null).catch(() => {});
      await this.verlaufZugangBeenden().catch(() => {});
      await this.outboxAusfuehren((outbox) => outbox.zustandLeeren());
      await this.outboxAktualisieren();
      const offen = this.outboxListe.filter((item) => item.status === "blockiert_zugang").length;
      const begriff = offen === 1 ? "Übermittlung" : "Übermittlungen";
      this.hinweisBanner = `Zugang beendet – ${offen} ${begriff} ohne Serverbestätigung. Bitte Einsatzleitung informieren.`;
      this.swCacheLeeren();
      clearTimeout(this.wsTimer);
      this.ws?.close();
    },
    async lokaleDatenLoeschen() {
      if (this.outboxListe.length && !confirm("Lokale Daten und nicht übermittelte Meldungen wirklich löschen?")) return;
      this.swCacheLeeren();
      const name = this.outbox ? this.outboxName() : null;
      if (name) indexedDB.deleteDatabase(name);
      if (/^(gk|qr):/.test(window.EINHEIT_PRINCIPAL || "")) {
        await fetch("/gk/abmelden", {
          method: "POST",
          headers: { "X-CSRF-Token": decodeURIComponent(cookie("ec_csrf")) },
          credentials: "same-origin",
        }).catch(() => {});
      }
      location.href = "/gk";
    },

    // Live-Synchronisation.
    outboxName() {
      if (this.simulation) return `ec-einheit-sim-${this.simEinheitId}`;
      if (this.benutzerEinheitId) return `ec-einheit-benutzer-${this.benutzerEinheitId}`;
      if ((window.EINHEIT_PRINCIPAL || "").startsWith("gk:")) return `ec-einheit-gk-${this.einheitId}`;
      if ((window.EINHEIT_PRINCIPAL || "").startsWith("qr:")) return `ec-einheit-qr-${this.einheitId}`;
      return "ec-einheit";
    },

    verbinden() {
      if (this.zugangBeendet || !this.lageId || this.ws?.readyState === WebSocket.OPEN) return;
      const schema = location.protocol === "https:" ? "wss" : "ws";
      const path = window.EINHEIT_WS_PATH || `/ws/lage/${this.lageId}`;
      const ws = this.ws = new WebSocket(`${schema}://${location.host}${path}`);
      ws.addEventListener("open", () => {
        this.online = true;
        this.wsVersuche = 0;
        void this.flush(true);
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
            void this.laden(); // Ereignis nicht durch die Polling-Drossel verlieren; Detail bleibt ein lesender Refresh.
          }
        } catch (error) {
          // Ungültige WebSocket-Nachrichten beeinflussen die Tablet-Ansicht nicht.
        }
      });
      ws.addEventListener("close", () => {
        // Ein ausgefallener Live-Kanal bedeutet bei erfolgreichem HTTP-Abgleich nicht "offline".
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

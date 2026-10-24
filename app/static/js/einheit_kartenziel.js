/* Zielauswahl liest ausschließlich die bestehenden, bereits berechtigten Auftragsdaten. */
import { hatKoordinaten, navUrl } from "./einheit_karte.js";

export function kartenziel() {
  return {
    karteAuswahlId: null, kartenDaten: null, kartenAnfrage: 0, kartenLaden: false, kartenGeladenId: null,
    hatKartenkoordinaten: hatKoordinaten,
    navigationUrl: navUrl,
    ermittleKartenZiel() {
      if (this.zugangBeendet) return null;
      return this.kartenAuftraege.find(a => a.dispatch_id === this.karteAuswahlId)
        || this.fokusAuftrag || this.kartenAuftraege[0] || null;
    },
    ermittleKartenHinweise() {
      const ziel = this.kartenZiel;
      if (!ziel) return null;
      const daten = this.kartenDaten?.dispatch_id === ziel.dispatch_id
        && this.kartenDaten.lat === ziel.lat && this.kartenDaten.lng === ziel.lng ? this.kartenDaten : null;
      return { ...daten, gefahren: ziel.gefahren || [] };
    },
    ermittleKartenSperrenText() {
      if (this.kartenLaden) return "Sperren am Ziel werden geladen …";
      const daten = this.kartenHinweise;
      if (!hatKoordinaten(this.kartenZiel)) return "Keine Kartenposition – Sperren und Gefahren am Ziel nicht räumlich prüfbar.";
      if (daten?.offline) return "Offline – Sperren vom letzten geladenen Stand. Zufahrt prüfen.";
      if (daten?.strassensperren_status !== "verfuegbar") return "Sperren am Ziel nicht verfügbar. Zufahrt mit Einsatzleitung klären.";
      return daten.strassensperren.length ? "Bekannte Sperren im Zielbereich:" : "Keine bekannten Sperren im Zielbereich.";
    },
    kartenCacheKey() { return `kartenhinweise:${this.lageId}:${this.einheitId}`; },
    async kartenZielLaden(force = false) {
      const ziel = this.kartenZiel;
      const id = ziel?.dispatch_id;
      if (this.zugangBeendet || !id || (!force && this.kartenGeladenId === id)) return;
      const token = ++this.kartenAnfrage;
      this.kartenGeladenId = id;
      this.kartenLaden = true;
      if (this.kartenDaten?.dispatch_id !== id) this.kartenDaten = null;
      const gueltig = () => token === this.kartenAnfrage && this.kartenZiel?.dispatch_id === id && !this.zugangBeendet;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetch(`/einheit/api/auftrag/${id}`, {
          headers: this.headers(), credentials: "same-origin", signal: controller.signal,
        });
        if (!gueltig()) return;
        if ([401, 403, 404].includes(response.status)) {
          this.kartenDaten = null;
          await this.outbox?.metaSpeichern(this.kartenCacheKey(), null).catch(() => {});
          return;
        }
        if (!response.ok) throw new Error("nicht_verfuegbar");
        const daten = await response.json();
        if (!gueltig()) return;
        if (daten.auftrag?.dispatch_id !== id || daten.auftrag?.withdrawn_at) {
          this.kartenDaten = null;
          await this.outbox?.metaSpeichern(this.kartenCacheKey(), null).catch(() => {});
          return;
        }
        this.kartenDaten = {
          dispatch_id: id, lat: ziel.lat, lng: ziel.lng,
          strassensperren: daten.strassensperren || [], strassensperren_status: daten.strassensperren_status,
          offline: response.headers.get("X-EC-Offline") === "1",
        };
        // Gleicher profilgebundener Speicher wie die Outbox; nur Zielhinweise, keine fremden Einheiten/Medien.
        const cache = await this.outbox?.metaLaden(this.kartenCacheKey()).catch(() => null) || {};
        if (!gueltig()) return;
        cache[id] = JSON.parse(JSON.stringify(this.kartenDaten));
        const erlaubt = new Set(this.kartenAuftraege.map(a => String(a.dispatch_id)));
        await this.outbox?.metaSpeichern(this.kartenCacheKey(), Object.fromEntries(
          Object.entries(cache).filter(([key]) => erlaubt.has(key)))).catch(() => {});
      } catch (_error) {
        const cache = await this.outbox?.metaLaden(this.kartenCacheKey()).catch(() => null);
        if (!gueltig()) return;
        const daten = cache?.[id];
        this.kartenDaten = daten?.dispatch_id === id && daten.lat === ziel.lat && daten.lng === ziel.lng
          ? { ...daten, offline: true } : null;
      } finally {
        clearTimeout(timeout);
        if (token === this.kartenAnfrage) {
          this.kartenLaden = false;
          if (gueltig()) {
            this.uebersichtKarte?.hinweise(this.kartenHinweise);
            this.$nextTick?.(() => this.uebersichtKarte?.map.invalidateSize({ pan: false }));
          }
        }
      }
    },
    kartenZielWaehlen(id, zentrieren = false) {
      if (!this.kartenAuftraege.some(a => a.dispatch_id === Number(id))) return;
      this.karteAuswahlId = Number(id);
      this.uebersichtKarteAktualisieren();
      if (zentrieren) this.uebersichtKarte?.zielZeigen(id);
    },
    standortBeenden() {
      this.uebersichtKarte?.stoppeStandort();
      this.standort = null;
      this.standortHinweis = "";
    },
  };
}

/**
 * objekt_picker.js - Objekt-Auswahl fuer die Erstellen-Dialoge (ECP-01)
 *
 * Gleiche Datenquelle wie "Aus Objekt uebernehmen" (/api/uebernahme/objekte). Das gewaehlte Objekt
 * wird als verstecktes Feld "objekt_id" mitgesendet und erst vom Server beim Anlegen mit dem Einsatz
 * verknuepft (Berechtigung und Mandant werden dort geprueft). Die Auswahl bleibt in sessionStorage
 * erhalten, damit sie bei Eingabefehlern oder erneutem Oeffnen des Dialogs nicht verloren geht.
 */
function objektPicker(key, incidentId) {
  const speicher = 'ec_objekt_picker:' + key;
  return {
    objekte: [], auswahl: null, suche: '', offen: false, ladend: false, geladen: false, fehler: '',
    init() {
      try {
        const roh = sessionStorage.getItem(speicher);
        const daten = roh ? JSON.parse(roh) : null;
        if (daten && daten.objekt && Date.now() - daten.at < 10 * 60 * 1000) this.auswahl = daten.objekt;
      } catch (e) { /* Speicher nicht verfuegbar: Auswahl nur im Dialog */ }
    },
    merken() {
      try {
        if (this.auswahl) sessionStorage.setItem(speicher, JSON.stringify({ objekt: this.auswahl, at: Date.now() }));
        else sessionStorage.removeItem(speicher);
      } catch (e) { /* ignorieren */ }
    },
    async ladeObjekte() {
      if (this.geladen || this.ladend) return;
      this.ladend = true;
      this.fehler = '';
      try {
        const url = '/api/uebernahme/objekte' + (incidentId ? '?incident_id=' + encodeURIComponent(incidentId) : '');
        const antwort = await fetch(url, { credentials: 'same-origin' });
        if (!antwort.ok) throw new Error('HTTP ' + antwort.status);
        this.objekte = (await antwort.json()).objekte || [];
        this.geladen = true;
      } catch (e) {
        this.fehler = 'Objekte konnten nicht geladen werden.';
      } finally {
        this.ladend = false;
      }
    },
    get treffer() {
      const q = this.suche.toLowerCase().trim();
      const liste = this.objekte.filter((o) => !q
        || [o.name, o.vulgoname, o.adresse, o.bma].filter(Boolean).join(' ').toLowerCase().includes(q));
      return liste.slice(0, 30);
    },
    waehle(o) { this.auswahl = o; this.suche = ''; this.offen = false; this.merken(); },
    entferne() { this.auswahl = null; this.merken(); },
    zuruecksetzen() { this.auswahl = null; this.suche = ''; this.merken(); },
  };
}
window.objektPicker = objektPicker;

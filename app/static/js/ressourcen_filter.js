/**
 * ressourcen_filter.js - Eigene/fremde Einheiten in der Ressourcenuebersicht (GSL-07)
 *
 * Standard: nur eigene Einheiten. Fremde lassen sich gezielt einblenden (oder allein anzeigen). Die Karten tragen
 * data-eigen="1|0" (Server, aus Organisation/Fahrzeug); der Filter blendet nur per CSS aus, es wird nichts
 * veraendert oder zugewiesen. Die Wahl bleibt waehrend der Sitzung je Lage erhalten (sessionStorage).
 */
function ressourcenFilter(lageId) {
  const schluessel = 'ec_res_filter:' + lageId;
  return {
    modus: 'eigene', eigene: 0, fremde: 0,
    init() {
      try {
        const gespeichert = sessionStorage.getItem(schluessel);
        if (['eigene', 'alle', 'fremde'].includes(gespeichert)) this.modus = gespeichert;
      } catch (e) { /* Standard bleibt "eigene" */ }
      this.zaehle();
    },
    setze(modus) {
      this.modus = modus;
      try { sessionStorage.setItem(schluessel, modus); } catch (e) { /* ignorieren */ }
    },
    zaehle() {
      const karten = this.$root.querySelectorAll('.einh-card[data-eigen]');
      let fremd = 0;
      karten.forEach((k) => { if (k.dataset.eigen === '0') fremd += 1; });
      this.fremde = fremd;
      this.eigene = karten.length - fremd;
    },
  };
}
window.ressourcenFilter = ressourcenFilter;

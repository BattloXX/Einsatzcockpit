/**
 * overlay-guard.js - Schutz vor dauerhaft dunkler/blockierter Oberflaeche (ECP-06)
 *
 * Auf Tablets (Android-WebView/PWA) blieb nach Interaktionen im Board gelegentlich ein leeres Detail-Modal mit
 * Abdunklung stehen. Dieser Schutz greift unabhaengig von der Ursache:
 *  - ein offenes, aber leeres Karten-Detail-Modal wird geschlossen (nach Rueckkehr in die App, Seitenwiederherstellung
 *    und nach jedem HTMX-Swap),
 *  - ein Tipp auf die abgedunkelte Flaeche ausserhalb des Karten-Detail-Modals schliesst es (Tablets haben keine
 *    Escape-Taste), ausser es gibt ungespeicherte Eingaben (data-dirty),
 *  - das Mobil-Menue wird zurueckgesetzt, wenn es bei breitem Fenster (>= 901 px, dort ausgeblendet) noch als offen gilt.
 */
(function () {
  'use strict';

  function detailModal() { return document.getElementById('cardDetailModal'); }

  function istLeer(modal) {
    const body = modal.querySelector('#cardDetailBody');
    return !body || (!body.textContent.trim() && !body.querySelector('input, textarea, select, img, video, canvas'));
  }

  function raeumeAuf() {
    const modal = detailModal();
    if (modal && modal.open && istLeer(modal)) modal.close();
    if (window.innerWidth >= 901) {
      const root = document.querySelector('[x-data="appState()"]');
      if (root && window.Alpine) {
        const data = window.Alpine.$data(root);
        if (data && data.mobileMenuOpen) data.mobileMenuOpen = false;
      }
    }
  }

  function backdropGetippt(event) {
    const modal = detailModal();
    if (!modal || !modal.open || event.target !== modal) return;
    const body = modal.querySelector('#cardDetailBody');
    if (body && body.dataset.dirty === '1') return; // ungespeicherte Eingaben nicht verwerfen
    const r = modal.getBoundingClientRect();
    const ausserhalb = event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom;
    if (ausserhalb) modal.close();
  }

  document.addEventListener('visibilitychange', () => { if (!document.hidden) raeumeAuf(); });
  window.addEventListener('pageshow', raeumeAuf);
  window.addEventListener('resize', raeumeAuf);
  document.addEventListener('htmx:afterSettle', raeumeAuf);
  document.addEventListener('click', backdropGetippt);

  window.ECOverlayGuard = { raeumeAuf, istLeer };
})();

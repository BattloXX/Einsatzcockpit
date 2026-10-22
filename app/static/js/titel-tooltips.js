/**
 * titel-tooltips.js - Tooltip mit dem vollen Namen, wenn ein Titel abgeschnitten dargestellt wird (ECP-03)
 *
 * Elemente mit [data-tip-bei-kuerzung] erhalten data-tip (vorhandene Tooltip-Komponente: Hover/Fokus per CSS,
 * Long-Press auf Touchgeraeten ueber tooltips.js), sobald der Text des ersten Kindelements (oder des Elements
 * selbst) breiter ist als sein sichtbarer Bereich. Spaltenbreiten und Layout bleiben unveraendert.
 */
(function () {
  'use strict';

  function aktualisiere(wurzel) {
    (wurzel || document).querySelectorAll('[data-tip-bei-kuerzung]').forEach((el) => {
      const inner = el.firstElementChild || el;
      const text = (inner.textContent || '').trim();
      const gekuerzt = text !== '' && inner.scrollWidth > inner.clientWidth + 1;
      if (gekuerzt) {
        el.setAttribute('data-tip', text);
        el.setAttribute('aria-label', text);
        if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
      } else if (el.hasAttribute('data-tip')) {
        el.removeAttribute('data-tip');
        el.removeAttribute('aria-label');
      }
    });
  }

  let geplant = false;
  function planen() {
    if (geplant) return;
    geplant = true;
    requestAnimationFrame(() => { geplant = false; aktualisiere(); });
  }

  document.addEventListener('DOMContentLoaded', planen);
  window.addEventListener('resize', planen);
  window.addEventListener('load', planen);
  document.addEventListener('htmx:afterSettle', planen);
  window.ECTitelTooltips = { aktualisiere };
})();

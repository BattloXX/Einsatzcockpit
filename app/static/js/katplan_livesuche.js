/* Live-Suche im Plankopf und Sprung in eingeklappte Kapitel. */
(function () {
  'use strict';
  const form = document.querySelector('[data-kp-livesuche]');
  const panel = document.getElementById('kp-suche-dropdown');
  const input = form && form.querySelector('[name="q"]');
  let anzeigen = false;
  let hervorgehoben;
  let timer;
  function schliessen() {
    if (!panel) return;
    anzeigen = false;
    panel.hidden = true;
    input.setAttribute('aria-expanded', 'false');
  }
  function oeffnen() {
    if (!panel) return;
    panel.hidden = !anzeigen || !input.value.trim() || !panel.textContent.trim();
    input.setAttribute('aria-expanded', String(!panel.hidden));
  }
  function sprung() {
    if (!window.location.hash.startsWith('#element-')) return;
    const element = document.getElementById(window.location.hash.slice(1));
    if (!element) return;
    let eltern = element.parentElement;
    while (eltern) {
      if (eltern.tagName === 'DETAILS') eltern.open = true;
      eltern = eltern.parentElement;
    }
    if (hervorgehoben) hervorgehoben.classList.remove('kp-suchziel');
    clearTimeout(timer);
    hervorgehoben = element;
    element.classList.add('kp-suchziel');
    const inhalt = element.closest('.kp-planinhalt');
    if (window.Alpine && inhalt) {
      const filter = Alpine.$data(inhalt);
      filter.phase = null;
      filter.akteur = null;
      filter.gliederung = 'kapitel';
      Alpine.$data(form.closest('section')).reiter = 'plan';
      Alpine.nextTick(function () { element.scrollIntoView({ block: 'center' }); });
    } else {
      element.scrollIntoView({ block: 'center' });
    }
    timer = setTimeout(function () { element.classList.remove('kp-suchziel'); }, 2500);
  }
  if (form) {
    document.addEventListener('htmx:afterSwap', function (event) {
      if (event.detail.target === panel && document.activeElement === input) oeffnen();
    });
    input.addEventListener('focus', function () { anzeigen = true; oeffnen(); });
    input.addEventListener('input', function () { schliessen(); anzeigen = true; });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !panel.hidden) {
        event.preventDefault();
        schliessen();
      }
    });
    document.addEventListener('click', function (event) {
      if (!form.contains(event.target)) schliessen();
      const link = event.target.closest('#kp-suche-dropdown a');
      if (!link) return;
      schliessen();
      const ziel = new URL(link.href);
      if (ziel.pathname === location.pathname && ziel.search === location.search) {
        // Auch erneuter Klick auf denselben Anker soll das Kapitel oeffnen.
        if (ziel.hash === location.hash) sprung();
      }
    });
  }
  window.addEventListener('hashchange', sprung);
  // Alpine beendet zuvor die Initialisierung der Kapitel und ihrer Filter.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { setTimeout(sprung, 0); });
  else setTimeout(sprung, 0);
})();

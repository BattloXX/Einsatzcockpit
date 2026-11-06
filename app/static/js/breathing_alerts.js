(function () {
  'use strict';
  let previousRank = Infinity;
  let previousKeys = new Set();
  function update() {
    const bar = document.getElementById('breathingAlertBar');
    if (!bar) { previousRank = Infinity; previousKeys.clear(); return; }
    if (bar.dataset.initialized) return;
    bar.dataset.initialized = '1';
    const alerts = JSON.parse(bar.dataset.alerts);
    const first = alerts[0];
    const announce = first.rank < previousRank && !previousKeys.has(first.key);
    previousRank = first.rank;
    previousKeys = new Set(alerts.map(function (alert) { return alert.key; }));
    const text = bar.querySelector('[data-alert-text]');
    if (announce) {
      bar.setAttribute('role', 'alert');
      bar.setAttribute('aria-live', 'assertive');
      text.textContent = '';
      window.setTimeout(function () { text.textContent = first.text; }, 50);
      window.setTimeout(function () { bar.removeAttribute('role'); bar.removeAttribute('aria-live'); }, 3000);
    }
    let index = 0;
    function jump() {
      const card = document.getElementById('troop-' + alerts[index].troop_id);
      if (!card) return;
      card.scrollIntoView({ block: 'center', behavior: 'smooth' });
      card.classList.add('troop-card--highlight');
      window.setTimeout(function () { card.classList.remove('troop-card--highlight'); }, 2000);
    }
    bar.querySelector('[data-alert-jump]').addEventListener('click', jump);
    const next = bar.querySelector('[data-alert-next]');
    if (next) next.addEventListener('click', function () {
      index = (index + 1) % alerts.length;
      const alert = alerts[index];
      text.textContent = alert.text;
      bar.className = 'breathing-alert breathing-alert--' + alert.color + (alert.emergency ? ' breathing-alert--emergency' : '');
      jump();
    });
  }
  update();
  document.body.addEventListener('htmx:afterSettle', update);
  document.body.addEventListener('htmx:oobAfterSwap', update);
})();

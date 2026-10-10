(function () {
  var root = document.getElementById('ressourceKarte');
  if (!root) return;
  var lageId = root.dataset.lageId;
  var activeId = null;
  function close() { activeId = null; root.classList.remove('is-open'); root.innerHTML = ''; }
  function setActiveTab(button) {
    var tab = button && button.dataset.karteTab;
    if (!tab) return;
    root.querySelectorAll('[data-karte-tab]').forEach(function (item) {
      item.classList.toggle('active', item.dataset.karteTab === tab);
    });
  }
  function reload() {
    if (!activeId || document.activeElement && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || root.querySelector('[data-dirty]')) {
      if (activeId && !root.querySelector('.ressource-karte__notice')) root.insertAdjacentHTML('afterbegin', '<button class="ressource-karte__notice">Neue Daten - aktualisieren</button>');
      return;
    }
    var tab = root.querySelector('[data-karte-tab].active');
    htmx.ajax('GET', '/lage/' + lageId + '/einheiten/' + activeId + '/karte/' + (tab ? tab.dataset.karteTab : 'uebersicht'), {target:'#ressourceKarteBody', swap:'innerHTML'});
  }
  window.oeffneKarte = function (id) { activeId = id; root.classList.add('is-open'); htmx.ajax('GET', '/lage/' + lageId + '/einheiten/' + id + '/karte', {target:'#ressourceKarte', swap:'innerHTML'}); };
  document.addEventListener('click', function (event) { if (event.target.closest('[data-karte-close]')) close(); if (event.target.matches('.ressource-karte__notice')) reload(); setActiveTab(event.target.closest('[data-karte-tab]')); });
  document.addEventListener('keydown', function (event) { if (event.key === 'Escape') close(); });
  document.addEventListener('click', function (event) { var top = event.target.closest('.einh-card__top'); if (top && !event.target.closest('button, input, select, textarea, form, a')) { var card = top.closest('[data-einheit-id]'); if (card) window.oeffneKarte(card.dataset.einheitId); } });
  window.ressourcenKarteChanged = function (message) { if (message.einheit_id && String(message.einheit_id) === String(activeId)) reload(); };
  try { var id = new URLSearchParams(location.search).get('einheit'); if (id) window.oeffneKarte(id); } catch (ignore) {}
}());

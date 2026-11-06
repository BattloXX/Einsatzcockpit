/* Adressvorschläge und OSM-Prüfung im Sperrenformular. */
(function () {
  'use strict';

  function cookie(name) {
    var prefix = name + '=';
    return document.cookie.split(';').map(function (part) { return part.trim(); }).filter(function (part) {
      return part.indexOf(prefix) === 0;
    }).map(function (part) { return decodeURIComponent(part.slice(prefix.length)); })[0] || '';
  }

  function initCard(card) {
    if (card._roadClosureAddress) return;
    var form = document.querySelector('.sperren-form-card');
    var street = card.querySelector('[data-section-field="street"]');
    var city = document.getElementById('city');
    var from = card.querySelector('[data-section-field="from_text"]');
    var to = card.querySelector('[data-section-field="to_text"]');
    var status = card.querySelector('[data-address-status]');
    if (!form || !street || !city || !from || !to || !status || !window.initAddressAutocomplete) return;
    card._roadClosureAddress = true;
    var orgCity = form.dataset.orgCity || '';
    var getCity = function () { return city.value || orgCity; };
    var opts = { field: 'street', url: '/strassensperren/adresse/vorschlaege', getCity: getCity };
    window.initAddressAutocomplete(Object.assign({}, opts, { inputId: street.id }));
    function crossSelect(input) {
      return function (item) { input.value = 'Kreuzung ' + (item.street || item.label); input.dispatchEvent(new Event('input', { bubbles: true })); };
    }
    window.initAddressAutocomplete(Object.assign({}, opts, { inputId: from.id, onSelect: crossSelect(from) }));
    window.initAddressAutocomplete(Object.assign({}, opts, { inputId: to.id, onSelect: crossSelect(to) }));
    var timer = null;
    var revision = 0;
    function add(text, className) {
      var line = document.createElement('span');
      line.textContent = text;
      line.style.display = 'block';
      if (className) line.style.color = 'var(--' + className + ')';
      status.appendChild(line);
    }
    function show(data) {
      status.textContent = '';
      if (data.status === 'ok') {
        add(data.osm_name + ' (OSM' + (data.laenge_m ? ', ' + Math.round(data.laenge_m) + ' m' : '') + ')', 'success');
      } else if (data.status === 'abweichend') {
        add('≈ Gefunden als „' + (data.osm_name || '') + '“', 'warning');
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn--ghost btn--sm';
        button.textContent = 'OSM-Namen übernehmen';
        button.addEventListener('click', function () { street.value = data.osm_name || street.value; street.dispatchEvent(new Event('input', { bubbles: true })); street.dispatchEvent(new Event('change')); });
        status.appendChild(button);
      } else if (data.status === 'nicht_gefunden') {
        add('Straße nicht in OSM gefunden' + (data.vorschlaege.length ? ' - Vorschläge: ' + data.vorschlaege.join(', ') : ''), 'danger');
      } else {
        add('OSM derzeit nicht erreichbar - Prüfung übersprungen', 'text-muted');
      }
      [['Von', data.von], ['Bis', data.bis]].forEach(function (entry) {
        if (!entry[1].text || entry[1].gefunden === null) return;
        add(entry[0] + ': ' + (entry[1].gefunden ? 'Kreuzung gefunden' : 'nicht gefunden'), entry[1].gefunden ? 'success' : 'danger');
      });
    }
    function validate() {
      if (!card.isConnected) return;
      if (!street.value.trim()) { status.textContent = ''; return; }
      var current = ++revision;
      fetch('/strassensperren/adresse/pruefen', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': cookie('ec_csrf') },
        body: JSON.stringify({ street: street.value, from_text: from.value, to_text: to.value, city: city.value || orgCity })
      }).then(function (response) { return response.ok ? response.json() : null; }).then(function (data) {
        if (data && current === revision && card.isConnected) show(data);
      }).catch(function () { status.textContent = ''; });
    }
    function schedule() { clearTimeout(timer); timer = setTimeout(validate, 600); }
    [street, city, from, to].forEach(function (input) { input.addEventListener('change', schedule); input.addEventListener('blur', schedule); });
  }
  window.initRoadClosureAddress = initCard;
  function init() { document.querySelectorAll('.sperren-section-card').forEach(initCard); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

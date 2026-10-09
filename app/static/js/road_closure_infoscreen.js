/* Statusseite und Infoscreen der Straßensperren: Live-Aktualisierung ohne Reload und optionale Rotation. */
(function () {
  'use strict';

  var COLORS = { red: '#d32f2f', orange: '#f57c00', yellow: '#fbc02d', grey: '#9e9e9e' };
  var KPIS = [
    ['aktiv', 'Aktiv'], ['geplant', 'Geplant'], ['vollsperren_aktiv', 'Vollsperren aktiv'],
    ['beginnt_heute', 'Beginnt heute'], ['endet_in_7_tagen', 'Endet ≤ 7 Tage']
  ];
  var MAX_DETAILS = 8;

  var root = document.getElementById('road-closure-screen');
  if (!root) return;
  var data = JSON.parse(document.getElementById('sperren-daten').textContent);
  var mapPanel = root.querySelector('.map-panel');
  var showMap = mapPanel && !mapPanel.hidden;
  var map = null;
  var layer = null;
  var loadedAt = Date.now();
  var lastOk = new Date();
  var rotationTimer = null;
  var rotationStep = 0;

  function el(tag, text, cls) {
    var node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  function hhmm(date) {
    return date.toLocaleTimeString('de-AT', { hour: '2-digit', minute: '2-digit' });
  }

  function color(item) { return COLORS[item.color] || COLORS.grey; }

  function clock() { document.getElementById('clock').textContent = hhmm(new Date()); }

  function popup(item) {
    var box = el('div');
    box.append(el('strong', item.title));
    [item.restriction_label, item.abschnitt,
      (item.valid_from_local || '') + ' – ' + (item.valid_until_local || 'unbefristet'),
      item.reason ? 'Grund: ' + item.reason : null].forEach(function (line) {
      if (line) { box.append(el('br')); box.append(document.createTextNode(line)); }
    });
    return box;
  }

  function ensureMap() {
    if (map || !showMap || typeof L === 'undefined' || !window.EinsatzcockpitMapConfig) return map;
    map = L.map('map', { zoomControl: root.dataset.modus !== 'infoscreen' });
    window.EinsatzcockpitMapConfig.addOsmTileLayer(map);
    map.setView([parseFloat(root.dataset.lat) || 47.4664, parseFloat(root.dataset.lng) || 9.7416], 13);
    return map;
  }

  function drawMap(items) {
    if (!ensureMap()) return;
    if (layer) map.removeLayer(layer);
    var features = items.filter(function (item) { return item.geometry; }).map(function (item) {
      return { type: 'Feature', geometry: item.geometry, properties: item };
    });
    layer = L.geoJSON(features, {
      style: function (feature) { return { color: color(feature.properties), weight: 7, opacity: 0.9, fillOpacity: 0.3 }; },
      pointToLayer: function (feature, latlng) {
        return L.circleMarker(latlng, { radius: 9, color: color(feature.properties), fillOpacity: 0.8 });
      },
      onEachFeature: function (feature, item) { item.bindPopup(popup(feature.properties)); }
    }).addTo(map);
    map.invalidateSize();
    if (layer.getLayers().length) map.fitBounds(layer.getBounds(), { padding: [30, 30], maxZoom: 16 });
  }

  function renderKpis() {
    var box = document.getElementById('kpis');
    box.replaceChildren();
    KPIS.forEach(function (kpi) {
      if (kpi[0] === 'geplant' && data.zeige_geplante === false) return;
      var tile = el('div', null, 'kpi');
      tile.append(el('strong', String(data.kennzahlen[kpi[0]] || 0)), el('span', kpi[1]));
      box.append(tile);
    });
  }

  function card(item, gross) {
    var node = el('article', null, gross ? 'card card--gross' : 'card');
    node.style.borderLeftColor = color(item);
    node.append(el('h3', item.title));
    var art = [item.restriction_label].concat(item.einschraenkungen || []).filter(Boolean).join(' · ');
    if (art) node.append(el('div', art, 'meta meta--art'));
    if (item.abschnitt) node.append(el('div', item.abschnitt, 'meta'));
    node.append(el('div', (item.valid_from_local || '') + ' – ' + (item.valid_until_local || 'unbefristet'), 'meta'));
    if (item.reason) node.append(el('div', 'Grund: ' + item.reason, 'meta'));
    if (item.exceptions) node.append(el('div', 'Ausnahmen: ' + item.exceptions, 'meta'));
    if (item.nachbar) node.append(el('div', 'Nachbar: ' + item.nachbar, 'tag'));
    return node;
  }

  function renderList(items, gross) {
    var list = document.getElementById('list');
    list.replaceChildren();
    if (!items.length) { list.append(el('p', 'Derzeit keine Straßensperren', 'empty')); return; }
    [['active', 'Aktiv'], ['planned', 'Geplant']].forEach(function (group) {
      var subset = items.filter(function (item) { return item.status === group[0]; });
      if (!subset.length) return;
      if (!gross) list.append(el('h2', group[1] + ' (' + subset.length + ')', 'section-title'));
      subset.forEach(function (item) { list.append(card(item, gross)); });
    });
  }

  function setView(step) {
    var details = data.sperren.slice(0, MAX_DETAILS);
    root.classList.remove('view-map', 'view-detail');
    if (step === 0) {
      renderList(data.sperren, false);
      drawMap(data.sperren);
    } else if (step === 1) {
      root.classList.add('view-map');
      drawMap(data.sperren);
    } else {
      var item = details[step - 2];
      root.classList.add('view-detail');
      renderList([item], true);
      drawMap([item]);
    }
  }

  function restartRotation() {
    clearInterval(rotationTimer);
    rotationStep = 0;
    setView(0);
    var seconds = parseInt(data.rotation_sec, 10) || 0;
    if (root.dataset.modus !== 'infoscreen' || seconds <= 0 || data.sperren.length < 2) return;
    // Ablauf: Übersicht → Karte (nur wenn sichtbar) → Einzelansicht je Sperre (max. MAX_DETAILS).
    var sequence = [0].concat(showMap ? [1] : []);
    for (var i = 0; i < Math.min(data.sperren.length, MAX_DETAILS); i++) sequence.push(i + 2);
    rotationTimer = setInterval(function () {
      rotationStep = (rotationStep + 1) % sequence.length;
      setView(sequence[rotationStep]);
    }, seconds * 1000);
  }

  function render(next) {
    data = next;
    loadedAt = Date.now();
    lastOk = new Date();
    document.getElementById('stand').textContent = 'Stand ' + hhmm(lastOk);
    document.getElementById('connection').textContent = 'Automatische Aktualisierung';
    root.classList.remove('is-offline');
    renderKpis();
    restartRotation();
  }

  function refreshSeconds() { return Math.max(30, parseInt(data.refresh_sec, 10) || 60); }

  function tick() {
    var remaining = Math.max(0, Math.ceil((refreshSeconds() * 1000 - (Date.now() - loadedAt)) / 1000));
    document.getElementById('countdown').textContent = 'Nächste Aktualisierung in ' + remaining + ' s';
    document.getElementById('bar').style.transform = 'scaleX(' + (remaining / refreshSeconds()) + ')';
    if (remaining === 0) refresh();
  }

  var loading = false;
  function refresh() {
    if (loading) return;
    loading = true;
    fetch(root.dataset.url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then(function (response) { if (!response.ok) throw new Error(String(response.status)); return response.json(); })
      .then(render)
      .catch(function () {
        loadedAt = Date.now();
        root.classList.add('is-offline');
        document.getElementById('connection').textContent = 'Verbindung unterbrochen – Stand ' + hhmm(lastOk);
      })
      .then(function () { loading = false; });
  }

  var full = document.getElementById('fullscreen');
  if (full) full.addEventListener('click', function () {
    if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen();
  });

  clock();
  setInterval(clock, 1000);
  setInterval(tick, 1000);
  render(data);
}());

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
  var rotationSeconds = null;
  var currentView = 0;
  var pageIndex = 0;
  var pageCount = 1;
  var list = document.getElementById('list');
  var mobile = window.matchMedia('(max-width: 760px)');

  function paginated() {
    return root.dataset.modus === 'infoscreen' && !mobile.matches;
  }

  function orderedItems(items) {
    return items.filter(function (item) { return item.status === 'active'; })
      .concat(items.filter(function (item) { return item.status === 'planned'; }));
  }

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
      var tile = el('div', null, 'kpi bg-surface-container-low rounded-xl');
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
    list.replaceChildren();
    list.style.gridAutoRows = '';
    var indicator = document.getElementById('page-indicator');
    indicator.hidden = !paginated() || gross;
    var ordered = orderedItems(items);
    if (paginated() && !gross) {
      // Gleiche Kartenhoehe in CSS und Berechnung, inklusive Abstand zwischen Karten.
      var style = window.getComputedStyle(list);
      var probe = card(ordered[0] || { title: '' }, false);
      list.append(probe);
      var height = probe.getBoundingClientRect().height;
      var gap = parseFloat(style.rowGap) || 0;
      var capacity = Math.max(1, Math.floor((list.clientHeight + gap) / Math.max(1, height + gap)));
      // Auch bei sehr kleinen Fenstern passt mindestens eine begrenzte Karte hinein.
      list.style.gridAutoRows = Math.min(height, list.clientHeight) + 'px';
      probe.remove();
      pageCount = Math.max(1, Math.ceil(ordered.length / capacity));
      pageIndex = Math.min(pageIndex, pageCount - 1);
      indicator.textContent = 'Seite ' + (pageIndex + 1) + '/' + pageCount;
      ordered.slice(pageIndex * capacity, (pageIndex + 1) * capacity).forEach(function (item) {
        var node = card(item, false);
        node.style.height = '100%';
        node.prepend(el('div', item.status === 'active' ? 'Aktiv' : 'Geplant', 'tag'));
        list.append(node);
      });
    } else {
      list.style.gridAutoRows = '';
      [['active', 'Aktiv'], ['planned', 'Geplant']].forEach(function (group) {
        var subset = ordered.filter(function (item) { return item.status === group[0]; });
        if (!subset.length) return;
        if (!gross) list.append(el('h2', group[1] + ' (' + subset.length + ')', 'section-title'));
        subset.forEach(function (item) { list.append(card(item, gross)); });
      });
    }
    if (!ordered.length) list.append(el('p', 'Derzeit keine Straßensperren', 'empty'));
  }

  function setView(step) {
    var details = data.sperren.slice(0, MAX_DETAILS);
    if (step >= details.length + 2) step = 0;
    currentView = step;
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

  function advance() {
    if (!paginated()) return;
    if (currentView === 0 && pageIndex + 1 < pageCount) {
      pageIndex++;
      renderList(data.sperren, false);
      return;
    }
    pageIndex = 0;
    var rotateViews = (parseInt(data.rotation_sec, 10) || 0) > 0 && data.sperren.length >= 2;
    if (!rotateViews) { setView(0); return; }
    // Alle Uebersichtsseiten, Karte (falls sichtbar), dann Einzelansichten.
    if (currentView === 0) setView(showMap ? 1 : 2);
    else if (currentView === 1) setView(2);
    else setView(currentView + 1);
  }

  function restartRotation() {
    setView(currentView);
    // Ohne Ansichtsrotation bleiben weitere Listenseiten dennoch erreichbar.
    var seconds = parseInt(data.rotation_sec, 10) || 15;
    if (root.dataset.modus !== 'infoscreen') return;
    if (rotationTimer && rotationSeconds === seconds) return;
    clearInterval(rotationTimer);
    rotationSeconds = seconds;
    rotationTimer = setInterval(advance, Math.max(1, seconds) * 1000);
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

  function resize() {
    // In der Kartenansicht ist die Liste unsichtbar und nicht messbar.
    if (currentView !== 1) renderList(currentView >= 2 ? [data.sperren[currentView - 2]] : data.sperren, currentView >= 2);
    if (map) map.invalidateSize();
  }
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(resize).observe(list);
  mobile.addEventListener('change', function () {
    pageIndex = 0;
    if (mobile.matches) setView(0);
    resize();
  });

  clock();
  setInterval(clock, 1000);
  setInterval(tick, 1000);
  render(data);
}());

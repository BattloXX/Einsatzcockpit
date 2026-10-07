/* Karte fuer Strassensperren. */
(function () {
  'use strict';

  var COLORS = { red: '#d32f2f', orange: '#f57c00', yellow: '#fbc02d', grey: '#9e9e9e' };

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>'"]/g, function (char) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char];
    });
  }

  function localDate(value) {
    if (!value) return 'unbefristet';
    var date = new Date(value);
    return isNaN(date.getTime()) ? '' : date.toLocaleString('de-AT', { dateStyle: 'short', timeStyle: 'short' });
  }

  function popup(properties) {
    var area = [properties.street, properties.from_text || properties.to_text ?
      ((properties.from_text || '?') + ' – ' + (properties.to_text || '?')) : ''].filter(Boolean).join(': ');
    var html = '<strong>' + escapeHtml(properties.title) + '</strong>';
    if (properties.restriction_label) html += '<br>' + escapeHtml(properties.restriction_label);
    if (area) html += '<br>' + escapeHtml(area);
    html += '<br>Gültigkeit: ' + escapeHtml(localDate(properties.valid_from));
    if (properties.valid_until) html += ' – ' + escapeHtml(localDate(properties.valid_until));
    if (properties.description) html += '<br>Grund: ' + escapeHtml(properties.description);
    if (properties.source) html += '<br>Quelle: ' + escapeHtml(properties.source);
    if (properties.geometry_status === 'needs_review') html += '<br><em>Geometrie prüfen</em>';
    if (properties.url) html += '<br><a href="' + escapeHtml(properties.url) + '">Details</a>';
    return html;
  }

  window.initSperrenKarte = function (container) {
    if (!container || typeof L === 'undefined' || !window.EinsatzcockpitMapConfig) return null;
    if (container._sperrenMap) return container._sperrenMap;
    var lat = parseFloat(container.getAttribute('data-center-lat')) || 47.4664;
    var lng = parseFloat(container.getAttribute('data-center-lng')) || 9.7416;
    var map = L.map(container).setView([lat, lng], 13);
    window.EinsatzcockpitMapConfig.addOsmTileLayer(map);
    var layer;

    function load(url) {
      if (!url) return Promise.resolve();
      container.setAttribute('data-geojson-url', url);
      return fetch(url).then(function (response) {
        if (!response.ok) throw new Error('Karte konnte nicht geladen werden');
        return response.json();
      }).then(function (data) {
        if (layer) map.removeLayer(layer);
        layer = L.geoJSON(data, {
          style: function (feature) {
            var color = COLORS[(feature.properties || {}).color] || COLORS.grey;
            return { color: color, weight: 6, fillColor: color, fillOpacity: 0.25 };
          },
          pointToLayer: function (feature, point) {
            return L.circleMarker(point, { radius: 8, color: COLORS[(feature.properties || {}).color] || COLORS.grey, weight: 6, fillOpacity: 0.25 });
          },
          onEachFeature: function (feature, featureLayer) { featureLayer.bindPopup(popup(feature.properties || {})); }
        }).addTo(map);
        if (layer.getLayers().length) map.fitBounds(layer.getBounds(), { padding: [20, 20], maxZoom: 16 });
      }).catch(function () {});
    }
    map.reload = load;
    container._sperrenMap = map;
    load(container.getAttribute('data-geojson-url'));
    return map;
  };

  function init() {
    var container = document.getElementById('sperren-karte');
    if (container) window.initSperrenKarte(container);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  document.body.addEventListener('htmx:afterSwap', function (event) {
    if (!event.target || event.target.id !== 'sperren-liste') return;
    var container = document.getElementById('sperren-karte');
    var form = document.getElementById('sperren-filter');
    if (!container || !form || !container._sperrenMap) return;
    var params = new URLSearchParams(new FormData(form));
    var requestParams = event.detail && event.detail.requestConfig && event.detail.requestConfig.parameters;
    if (requestParams) Object.keys(requestParams).forEach(function (key) { params.set(key, requestParams[key]); });
    container._sperrenMap.reload('/strassensperren/karte.json?' + params.toString());
  });
})();

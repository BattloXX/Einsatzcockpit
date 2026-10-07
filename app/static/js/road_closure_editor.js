/* Editor fuer genau eine Sperrengeometrie. */
(function () {
  'use strict';

  function init() {
    var container = document.getElementById('sperren-editor');
    if (!container || typeof L === 'undefined' || container._sperrenEditor) return;
    var input = document.querySelector('input[name="geometry_geojson"]');
    var checked = document.querySelector('input[name="geometry_checked"]');
    var hint = document.getElementById('abschnitt-hinweis');
    if (!input) return;
    var map = L.map(container).setView([47.4664, 9.7416], 13);
    window.EinsatzcockpitMapConfig.addOsmTileLayer(map);
    container._sperrenEditor = map;
    var geometryLayer = null;

    function setHint(message) {
      if (!hint) return;
      hint.textContent = message || '';
      hint.hidden = !message;
    }
    function clearLayer() {
      if (geometryLayer) map.removeLayer(geometryLayer);
      geometryLayer = null;
    }
    function save(markChecked) {
      input.value = geometryLayer ? JSON.stringify(geometryLayer.toGeoJSON().geometry) : '';
      if (markChecked && checked) checked.checked = true;
    }
    function useLayer(layer, markChecked) {
      clearLayer();
      geometryLayer = layer.addTo(map);
      save(markChecked);
      if (geometryLayer.getBounds && geometryLayer.getBounds().isValid()) map.fitBounds(geometryLayer.getBounds(), { padding: [20, 20], maxZoom: 16 });
    }
    function showGeometry(geometry, markChecked) {
      var result = L.geoJSON(geometry, {
        pointToLayer: function (feature, latlng) { return L.marker(latlng); }
      });
      var layers = result.getLayers();
      if (layers.length) useLayer(layers[0], markChecked);
    }

    try { if (input.value) showGeometry(JSON.parse(input.value), false); } catch (error) { input.value = ''; }
    if (map.pm) {
      map.pm.addControls({
        position: 'topleft', drawMarker: true, drawCircleMarker: false, drawCircle: false,
        drawText: false, drawPolyline: true, drawRectangle: false, drawPolygon: true,
        editMode: true, dragMode: true, cutPolygon: false, removalMode: true, rotateMode: false
      });
      map.on('pm:create', function (event) { useLayer(event.layer, true); });
      map.on('pm:edit pm:dragend', function (event) {
        if (event.layer === geometryLayer) save(true);
      });
      map.on('pm:remove', function (event) {
        if (event.layer === geometryLayer) { geometryLayer = null; save(false); }
      });
    }
    var button = document.getElementById('btn-abschnitt');
    if (button) button.addEventListener('click', function () {
      var original = button.textContent;
      button.disabled = true;
      button.textContent = 'Wird ermittelt …';
      setHint('');
      fetch('/strassensperren/abschnitt', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          street: (document.getElementById('strasse') || {}).value || '',
          from_text: (document.getElementById('von') || {}).value || '',
          to_text: (document.getElementById('bis') || {}).value || ''
        })
      }).then(function (response) {
        return response.json().then(function (data) { return { ok: response.ok, data: data }; });
      }).then(function (result) {
        if (!result.ok) { setHint(result.data.fehler || 'Abschnitt konnte nicht ermittelt werden.'); return; }
        showGeometry(result.data.geometry, false);
        if (checked) checked.checked = false;
        setHint(result.data.hinweis || 'Bitte Abschnitt auf der Karte prüfen und ggf. korrigieren.');
      }).catch(function () { setHint('Abschnitt konnte nicht ermittelt werden.'); }).finally(function () {
        button.disabled = false;
        button.textContent = original;
      });
    });
    setTimeout(function () { map.invalidateSize(); }, 100);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

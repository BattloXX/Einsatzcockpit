/**
 * weather_layer.js — Wetter-Karten-Layer für die GSL-Lagekarte.
 *
 * Radar-Tiles: RainViewer (kostenlos, kein Key).
 * Warnzonen:   /gsl/{id}/wetter/warnungen.geojson (GeoSphere Warn-API).
 * Schwerpunkt: /gsl/{id}/wetter/focus.json        (≥15 km Kartenumkreis).
 * Attribution:  GeoSphere Austria (CC BY 4.0), RainViewer.
 */
/* global L */
(function () {
  'use strict';

  var WeatherLayer = {
    _active:     false,
    _radarLayer: null,
    _warnLayer:  null,
    _interval:   null,
    _map:        null,
    _lageId:     null,
    _btn:        null,

    init: function (map, lageId) {
      this._map    = map;
      this._lageId = lageId;
      this._addControl();
    },

    _addControl: function () {
      var self = this;
      var Ctrl = L.Control.extend({
        options: { position: 'topleft' },
        onAdd: function () {
          var div = L.DomUtil.create('div', 'leaflet-bar leaflet-control');
          var a   = L.DomUtil.create('a', '', div);
          a.href  = '#';
          a.title = 'Wetter-Layer ein-/ausblenden';
          a.setAttribute('aria-label', a.title);
          a.innerHTML = ecpIcon('cloud-rain', {size: 'sm'});
          a.id    = 'weather-layer-btn';
          a.style.cssText = [
            'font-size:1.15rem', 'display:flex', 'align-items:center',
            'justify-content:center', 'width:30px', 'height:30px',
            'text-decoration:none', 'cursor:pointer',
          ].join(';');
          self._btn = a;
          L.DomEvent.on(a, 'click', function (e) {
            L.DomEvent.preventDefault(e);
            if (self._active) { self.deactivate(); } else { self.activate(); }
          });
          return div;
        },
      });
      new Ctrl().addTo(this._map);
    },

    activate: function () {
      this._active = true;
      if (this._btn) { this._btn.style.background = 'rgba(59,130,246,.35)'; }
      this._fitToFocus();
      this._loadRadar();
      this._loadWarnings();
      var self = this;
      this._interval = setInterval(function () {
        self._loadRadar();
        self._loadWarnings();
      }, 300000);   // auto-refresh every 5 min
    },

    deactivate: function () {
      this._active = false;
      if (this._btn) { this._btn.style.background = ''; }
      if (this._radarLayer) { this._map.removeLayer(this._radarLayer); this._radarLayer = null; }
      if (this._warnLayer)  { this._map.removeLayer(this._warnLayer);  this._warnLayer  = null; }
      if (this._interval)   { clearInterval(this._interval); this._interval = null; }
    },

    _fitToFocus: function () {
      var map = this._map;
      fetch('/gsl/' + this._lageId + '/wetter/focus.json')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d || d.lat == null || d.lng == null) { return; }
          var km   = d.radius_km || 15;
          var dlat = km / 111.0;
          var dlng = km / (111.0 * Math.cos(d.lat * Math.PI / 180));
          map.fitBounds(
            [[d.lat - dlat, d.lng - dlng], [d.lat + dlat, d.lng + dlng]],
            { animate: true }
          );
        })
        .catch(function () {});
    },

    _loadRadar: function () {
      var self = this;
      if (this._radarLayer) { this._map.removeLayer(this._radarLayer); this._radarLayer = null; }
      fetch('https://api.rainviewer.com/public/weather-maps.json')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) { return; }
          var host = d.host || 'https://tilecache.rainviewer.com';
          var items = (d.radar && d.radar.nowcast) || [];
          if (!items.length) { items = (d.radar && d.radar.past) || []; }
          var item = items[items.length - 1] || null;
          if (!item) { return; }
          self._radarLayer = L.tileLayer(
            host + item.path + '/256/{z}/{x}/{y}/2/1_1.png',
            {
              opacity:     0.55,
              attribution: 'Radar: <a href="https://rainviewer.com" target="_blank">RainViewer</a>',
              zIndex:      2,
            }
          );
          self._radarLayer.addTo(self._map);
        })
        .catch(function () {});
    },

    _loadWarnings: function () {
      var self = this;
      if (this._warnLayer) { this._map.removeLayer(this._warnLayer); this._warnLayer = null; }
      fetch('/gsl/' + this._lageId + '/wetter/warnungen.geojson')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (gj) {
          if (!gj || !gj.features || !gj.features.length) { return; }
          self._warnLayer = L.geoJSON(gj, {
            pointToLayer: function (feat, ll) {
              var lvl   = (feat.properties && feat.properties.level) || 1;
              var color = (feat.properties && feat.properties.level_color) || '#fbbf24';
              var m = L.circleMarker(ll, {
                radius:      14 + lvl * 3,
                fillColor:   color,
                fillOpacity: 0.22,
                color:       color,
                weight:      2,
                opacity:     0.85,
              });
              var label = (feat.properties.event_type || 'Warnung') + ' Stufe ' + lvl;
              if (feat.properties.text) { label += '\n' + feat.properties.text; }
              m.bindTooltip(label, { sticky: true });
              return m;
            },
          }).addTo(self._map);
        })
        .catch(function () {});
    },
  };

  window.WeatherLayer = WeatherLayer;
})();

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

  var attribution = 'Wetter: <a href="https://data.hub.geosphere.at/" target="_blank" rel="noopener">GeoSphere Austria</a> (CC BY 4.0)';
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
      if (this._active) { return; }
      this._active = true;
      if (this._btn) { this._btn.style.background = 'rgba(59,130,246,.35)'; }
      this._radarLayer = L.layerGroup().addTo(this._map);
      this._warnLayer = L.layerGroup().addTo(this._map);
      this._windLayer = L.layerGroup().addTo(this._map);
      this._layerControl = L.control.layers(null, {
        'Radar': this._radarLayer, 'Amtliche Warnungen': this._warnLayer,
        'Wind am Fokuspunkt': this._windLayer
      }, {position: 'topright', collapsed: true}).addTo(this._map);
      this._map.attributionControl.addAttribution(attribution);
      this._fitToFocus();
      this._loadRadar();
      this._loadWarnings();
      var self = this;
      this._interval = setInterval(function () {
        self._fitToFocus(false);
        self._loadWarnings();
      }, 300000);   // auto-refresh every 5 min
    },

    deactivate: function () {
      this._active = false;
      if (this._radar) { this._radar.destroy(); this._radar = null; }
      if (this._radarControl) { this._radarControl.remove(); this._radarControl = null; }
      if (this._layerControl) { this._layerControl.remove(); this._layerControl = null; }
      if (this._windLayer) { this._map.removeLayer(this._windLayer); this._windLayer = null; }
      this._map.attributionControl.removeAttribution(attribution);
      if (this._btn) { this._btn.style.background = ''; }
      if (this._radarLayer) { this._map.removeLayer(this._radarLayer); this._radarLayer = null; }
      if (this._warnLayer)  { this._map.removeLayer(this._warnLayer);  this._warnLayer  = null; }
      if (this._interval)   { clearInterval(this._interval); this._interval = null; }
    },

    _fitToFocus: function (fit) {
      var self = this;
      var map = this._map;
      fetch('/gsl/' + this._lageId + '/wetter/focus.json')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!self._active || !d || d.lat == null || d.lng == null) { return; }
          var km   = d.radius_km || 15;
          var dlat = km / 111.0;
          var dlng = km / (111.0 * Math.cos(d.lat * Math.PI / 180));
          self._drawWind(d);
          if (fit === false) { return; }
          map.fitBounds(
            [[d.lat - dlat, d.lng - dlng], [d.lat + dlat, d.lng + dlng]],
            { animate: true }
          );
        })
        .catch(function () {});
    },

    _drawWind: function (d) {
      this._windLayer.clearLayers();
      if (d.wind_dir_deg == null || !Number.isFinite(d.wind_dir_deg)) { return; }
      // Meteorologische Richtung = Herkunft; Pfeil zeigt die Strömungsrichtung.
      var arrow = document.createElement('span');
      arrow.style.cssText = 'display:inline-flex;transform:rotate(' + (d.wind_dir_deg + 180) + 'deg)';
      arrow.innerHTML = ecpIcon('arrow-up');
      var marker = L.marker([d.lat, d.lng], {icon: L.divIcon({
        html: arrow.outerHTML, className: 'weather-wind-arrow', iconSize: [24, 24]
      })});
      var label = document.createElement('span');
      label.textContent = 'Wind aus ' + d.wind_dir_deg + '°'
        + (d.wind_ms == null ? '' : ' · ' + d.wind_ms.toFixed(1) + ' m/s')
        + (d.gust_max_ms == null ? '' : ' · Böe ' + d.gust_max_ms.toFixed(1) + ' m/s')
        + (d.time ? ' · gültig ' + new Date(d.time).toLocaleString('de-AT') : '')
        + (d.datenstand ? ' · ' + d.datenstand.provider + ' · Stand '
          + new Date(d.datenstand.fetched_at).toLocaleString('de-AT') : '');
      marker.bindTooltip(label).addTo(this._windLayer);
    },

    _loadRadar: function () {
      var self = this;
      var Ctrl = L.Control.extend({
        options: {position: 'bottomleft'},
        onAdd: function () {
          var div = L.DomUtil.create('div', 'leaflet-bar');
          div.style.cssText = 'background:var(--surface,#fff);padding:8px;max-width:240px';
          div.innerHTML = '<div style="display:flex;align-items:center;gap:6px">'
            + '<button type="button" data-radar-id="radar-play-btn" aria-label="Radar Abspielen / Pause">'
            + ecpIcon('player-pause') + '</button>'
            + '<input type="range" aria-label="Radarzeit" data-radar-id="radar-scrubber" min="0" max="0" value="0" style="width:130px">'
            + '</div><span data-radar-id="radar-time-label"></span>'
            + '<span data-radar-id="radar-loading"> Radar wird geladen…</span>'
            + '<span data-radar-id="radar-live-badge" style="display:none"> LIVE</span>'
            + '<div style="display:none"><span data-radar-id="slabel-start"></span>'
            + '<span data-radar-id="slabel-mid"></span><span data-radar-id="slabel-end"></span></div>';
          L.DomEvent.disableClickPropagation(div);
          L.DomEvent.disableScrollPropagation(div);
          self._radar = window.WeatherRadar.create(self._radarLayer, div);
          return div;
        }
      });
      this._radarControl = new Ctrl().addTo(this._map);
    },

    _loadWarnings: function () {
      var self = this;
      fetch('/gsl/' + this._lageId + '/wetter/warnungen.geojson')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (gj) {
          if (!self._active || !gj || !gj.features) { return; }
          self._warnLayer.clearLayers();
          // Nur vorhandene Geometrien darstellen, niemals Flächen aus Punkten ableiten.
          L.geoJSON(gj, {
            style: function (feat) {
              return {color: feat.properties.level_color || '#fbbf24', fillOpacity: 0.22, weight: 2};
            },
            onEachFeature: function (feat, layer) {
              var label = document.createElement('span');
              label.textContent = feat.properties.event_type + ' Stufe ' + feat.properties.level
                + (feat.properties.text ? ' · ' + feat.properties.text : '');
              layer.bindTooltip(label, {sticky: true});
            },
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
              // Text wird in onEachFeature als Textknoten gebunden.
              return m;
            },
          }).addTo(self._warnLayer);
        })
        .catch(function () {});
    },
  };

  window.WeatherLayer = WeatherLayer;
})();

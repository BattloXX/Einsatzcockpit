/* Abschnittsliste und Karteneditor: Geoman bearbeitet nur den aktiven Abschnitt. */
(function () {
  'use strict';
  var nextKey = 0;
  function geometry(value) { return typeof value === 'string' ? JSON.parse(value) : value; }
  window.roadClosureSections = function () {
    return {
      sections: [], active: 0,
      init: function () {
        var initial = JSON.parse(document.getElementById('sperren-initial-sections').textContent);
        this.sections = initial.map(this.section);
        this.active = this.sections[0]._key;
        this.$watch('sections', () => this.refresh());
        this.$watch('active', () => this.refresh());
        this.refresh();
      },
      section: function (item) {
        return Object.assign({ street: '', from_text: '', to_text: '', direction: '', geometry_geojson: null,
          geometry_status: 'missing', geometry_quality: '', geometry_meta_json: '', geometry_checked: false },
        item, { _key: ++nextKey, busy: false, hint: '' });
      },
      payload: function () {
        return this.sections.map(function (item) {
          var result = Object.assign({}, item);
          delete result._key; delete result.busy; delete result.hint;
          return result;
        });
      },
      refresh: function () {
        // Ein Snapshot trennt Leaflet-Objekte von Alpines reaktivem Zustand.
        window.dispatchEvent(new CustomEvent('sperren-sections', {
          detail: JSON.parse(JSON.stringify({ sections: this.sections, active: this.active }))
        }));
      },
      add: function () {
        var section = this.section({});
        this.sections.push(section); this.active = section._key;
      },
      remove: function (key) {
        if (this.sections.length === 1) return;
        this.sections = this.sections.filter(item => item._key !== key);
        if (this.active === key) this.active = this.sections[0]._key;
      },
      setGeometry: function (detail) {
        var section = this.sections.find(item => item._key === detail.key);
        if (!section) return;
        Object.assign(section, { geometry_geojson: detail.geometry, geometry_checked: !!detail.geometry,
          geometry_status: detail.geometry ? 'ok' : 'missing', geometry_quality: detail.geometry ? 'manuell' : '',
          geometry_meta_json: '' });
      },
      resolve: async function (section) {
        section.busy = true; section.hint = '';
        try {
          var response = await fetch('/strassensperren/abschnitt', {
            method: 'POST', headers: { 'Content-Type': 'application/json',
              'X-CSRF-Token': this.$el.querySelector('[name="_csrf"]').value },
            body: JSON.stringify({ street: section.street, from_text: section.from_text, to_text: section.to_text,
              city: document.getElementById('city').value || this.$el.dataset.orgCity || '' })
          });
          var data = await response.json();
          if (!response.ok) throw new Error(data.fehler || 'Abschnitt konnte nicht ermittelt werden.');
          section.geometry_geojson = data.geometry;
          section.geometry_quality = data.quality || '';
          section.geometry_checked = data.quality === 'hoch';
          section.geometry_status = section.geometry_checked ? 'ok' : 'needs_review';
          section.geometry_meta_json = JSON.stringify({ methode: 'osm', osm_name: data.osm_name,
            endpoints: data.endpoints || [], mehrdeutigkeiten: data.mehrdeutigkeiten || [], quality: data.quality });
          section.hint = (data.hinweise || []).concat((data.mehrdeutigkeiten || []).map(item => 'Mehrdeutigkeit: ' + item.text)).join('\n');
        } catch (error) { section.hint = error.message || 'Abschnitt konnte nicht ermittelt werden.'; }
        finally { section.busy = false; }
      }
    };
  };

  function init() {
    var container = document.getElementById('sperren-editor');
    if (!container || typeof L === 'undefined' || container._sperrenEditor) return;
    var map = L.map(container).setView([47.4664, 9.7416], 13);
    window.EinsatzcockpitMapConfig.addOsmTileLayer(map);
    container._sperrenEditor = map;
    var layers = new Map();
    var active = null;
    var signature = '';
    var colors = ['#dc2626', '#2563eb', '#15803d', '#9333ea', '#c2410c', '#0e7490'];
    function save(key, group) {
      var geometries = [];
      editableLayers(group, function (layer) { geometries.push(layer.toGeoJSON().geometry); });
      var value = geometries.length === 1 ? geometries[0] : {
        type: 'GeometryCollection', geometries: geometries
      };
      window.dispatchEvent(new CustomEvent('sperren-geometry', { detail: { key: key, geometry: geometries.length ? value : null } }));
    }
    function editableLayers(group, callback) {
      group.eachLayer(function (layer) {
        if (layer.eachLayer) editableLayers(layer, callback);
        else callback(layer, group);
      });
    }
    window.addEventListener('sperren-sections', function (event) {
      var state = event.detail;
      var current = JSON.stringify([state.active, state.sections.map(item => [item._key, item.geometry_geojson])]);
      if (current === signature) return;
      signature = current; active = state.active;
      layers.forEach(group => map.removeLayer(group)); layers.clear();
      state.sections.forEach(function (section, index) {
        if (!section.geometry_geojson) return;
        var enabled = section._key === active;
        var group;
        try {
          group = L.geoJSON(geometry(section.geometry_geojson), {
            style: { color: colors[index % colors.length] },
            pmIgnore: !enabled,
            pointToLayer: function (feature, latlng) {
              return L.marker(latlng, { pmIgnore: !enabled, icon: window.ecpMarkerIcon ? window.ecpMarkerIcon('map-pin', { color: colors[index % colors.length] }) : undefined });
            }
          });
        } catch (error) { return; }
        layers.set(section._key, group); group.addTo(map);
        editableLayers(group, function (layer, parent) {
          layer.bindTooltip(String(index + 1), { permanent: true, direction: 'center' });
          layer.on('pm:edit pm:dragend', function () { if (section._key === active) save(section._key, group); });
          layer.on('pm:remove', function () {
            if (section._key !== active) return;
            parent.removeLayer(layer); save(section._key, group);
          });
        });
      });
      var bounds = L.featureGroup(Array.from(layers.values())).getBounds();
      if (bounds.isValid()) map.fitBounds(bounds, { padding: [20, 20], maxZoom: 16 });
    });
    if (map.pm) {
      map.pm.addControls({ position: 'topleft', drawMarker: true, drawCircleMarker: false, drawCircle: false,
        drawText: false, drawPolyline: true, drawRectangle: false, drawPolygon: true, editMode: true,
        dragMode: true, cutPolygon: false, removalMode: true, rotateMode: false });
      map.on('pm:create', function (event) {
        map.removeLayer(event.layer);
        window.dispatchEvent(new CustomEvent('sperren-geometry', { detail: { key: active, geometry: event.layer.toGeoJSON().geometry } }));
      });
    }
    window.dispatchEvent(new CustomEvent('sperren-editor-ready'));
    setTimeout(function () { map.invalidateSize(); }, 100);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

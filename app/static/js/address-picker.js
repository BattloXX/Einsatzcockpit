/* Gemeinsamer Adress-Picker fuer Einsatz- und Lage-Dialoge. */
(function () {
  'use strict';

  var leafletLoading = false;
  var leafletCallbacks = [];

  function ensureLeaflet(callback) {
    if (typeof L !== 'undefined' && typeof initMapPicker !== 'undefined') {
      callback();
      return;
    }
    leafletCallbacks.push(callback);
    if (leafletLoading) return;
    leafletLoading = true;
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/css/leaflet.min.css';
    document.head.appendChild(link);
    var leaflet = document.createElement('script');
    leaflet.src = '/static/js/leaflet.min.js';
    leaflet.onload = function () {
      var picker = document.createElement('script');
      picker.src = '/static/js/map-picker.js';
      picker.onload = function () {
        leafletCallbacks.splice(0).forEach(function (fn) { fn(); });
      };
      document.head.appendChild(picker);
    };
    document.head.appendChild(leaflet);
  }

  function ensureAutocomplete(callback) {
    if (typeof initAddressAutocomplete !== 'undefined') {
      callback();
      return;
    }
    var script = document.createElement('script');
    script.src = '/static/js/address-autocomplete.js';
    script.onload = callback;
    document.head.appendChild(script);
  }

  window.initAddressPicker = function (opts) {
    var prefix = opts.prefix;
    var city = document.getElementById(opts.cityId || prefix + 'City');
    var street = document.getElementById(opts.streetId || prefix + 'Street');
    var houseNo = document.getElementById(opts.noId || prefix + 'No');
    var latInput = document.getElementById(opts.latId || prefix + 'Lat');
    var lngInput = document.getElementById(opts.lngId || prefix + 'Lng');
    var status = document.getElementById(opts.statusId || prefix + 'GeocodeStatus');
    var button = document.getElementById(opts.geocodeBtnId || prefix + 'GeocodeBtn');
    var mapId = opts.mapId || prefix + 'MapContainer';
    var container = document.getElementById(mapId);
    var root = container && container.closest('[data-address-picker]');
    if (!city || !street || !houseNo || !latInput || !lngInput || !status || !button || !container) return null;

    var defaultLat = Number(opts.defaultLat || (root && root.dataset.defaultLat) || 47.41);
    var defaultLng = Number(opts.defaultLng || (root && root.dataset.defaultLng) || 9.74);
    var map = null;
    var marker = null;
    var manualPin = false;
    var timer = null;
    var reverseRequest = 0;

    function setStatus(text, color) {
      status.textContent = text;
      status.style.color = color || 'var(--text-muted)';
    }
    function setMarker(lat, lng, zoom) {
      latInput.value = Number(lat).toFixed(6);
      lngInput.value = Number(lng).toFixed(6);
      if (map && marker) {
        marker.setLatLng([lat, lng]);
        if (zoom) map.setView([lat, lng], zoom);
      }
    }
    function initMap() {
      if (map) {
        setTimeout(function () { map.invalidateSize(); }, 200);
        setTimeout(function () { map.invalidateSize(); }, 600);
        return;
      }
      var lat = parseFloat(latInput.value) || defaultLat;
      var lng = parseFloat(lngInput.value) || defaultLng;
      map = L.map(mapId).setView([lat, lng], 14);
      window.EinsatzcockpitMapConfig.addOsmTileLayer(map);
      marker = L.marker([lat, lng], { draggable: true }).addTo(map);
      marker.on('dragend', function (event) {
        var point = event.target.getLatLng();
        setManualPin(point.lat, point.lng);
      });
      map.on('click', function (event) {
        setManualPin(event.latlng.lat, event.latlng.lng);
      });
      setTimeout(function () { map.invalidateSize(); }, 200);
      setTimeout(function () { map.invalidateSize(); }, 600);
    }
    function setManualPin(lat, lng) {
      manualPin = true;
      setMarker(lat, lng);
      reverseGeocode(lat, lng);
    }
    function reverseGeocode(lat, lng) {
      var request = ++reverseRequest;
      setStatus('…Adresse wird ermittelt');
      var csrf = (document.cookie.match(/ec_csrf=([^;]+)/) || [])[1] || '';
      var data = new FormData();
      data.append('lat', lat);
      data.append('lng', lng);
      data.append('_csrf', csrf);
      fetch('/adresse/reverse', { method: 'POST', body: data })
        .then(function (response) { if (!response.ok) throw new Error(); return response.json(); })
        .then(function (result) {
          if (request !== reverseRequest) return;
          if (result.city) city.value = result.city;
          if (!result.street) {
            setStatus('Keine Adresse an diesem Punkt');
            return;
          }
          street.value = result.street;
          houseNo.value = result.house_number || '';
          setStatus(result.display_name || 'gefunden', 'var(--success, #18a957)');
        })
        .catch(function () {
          if (request === reverseRequest) {
            setStatus('Keine Adresse gefunden – Koordinaten bleiben gesetzt', 'var(--danger)');
          }
        });
    }
    function geocode(force) {
      if (!force && manualPin) return;
      if (!street.value.trim() && !city.value.trim()) return;
      setStatus('…wird gesucht');
      var csrf = (document.cookie.match(/ec_csrf=([^;]+)/) || [])[1] || '';
      var data = new FormData();
      data.append('address_street', street.value);
      data.append('address_no', houseNo.value);
      data.append('address_city', city.value);
      data.append('_csrf', csrf);
      fetch('/adresse/geocode', { method: 'POST', body: data })
        .then(function (response) { if (!response.ok) throw new Error(); return response.json(); })
        .then(function (result) {
          setMarker(result.lat, result.lng, 16);
          setStatus(result.display_name || 'gefunden', 'var(--success, #18a957)');
          manualPin = false;
        })
        .catch(function () { setStatus('Nicht gefunden', 'var(--danger)'); });
    }
    function autoGeocode() {
      clearTimeout(timer);
      timer = setTimeout(function () { geocode(false); }, 400);
    }
    ensureAutocomplete(function () {
      function onSelect(item) {
        // Alle Adressteile aus dem Vorschlag übernehmen, nicht nur das Feld,
        // in dem getippt wurde (z. B. "Kirchstraße 12" → auch Nr. 12).
        if (item.street) street.value = item.street;
        if (item.house_number) houseNo.value = item.house_number;
        if (item.city) city.value = item.city;
        if (item.lat != null && item.lng != null) {
          manualPin = false;
          setMarker(item.lat, item.lng, 16);
          setStatus(item.label, 'var(--success, #18a957)');
        }
      }
      initAddressAutocomplete({ inputId: city.id, field: 'city', onSelect: onSelect });
      initAddressAutocomplete({ inputId: street.id, field: 'street', getCity: function () { return city.value; }, onSelect: onSelect });
      initAddressAutocomplete({ inputId: houseNo.id, field: 'house', getCity: function () { return city.value; }, getStreet: function () { return street.value; }, onSelect: onSelect });
    });
    [city, street, houseNo].forEach(function (input) { input.addEventListener('blur', autoGeocode); });
    button.addEventListener('click', function () { geocode(true); });
    var dialog = container.closest('dialog');
    if (dialog) {
      new MutationObserver(function () {
        if (dialog.open) ensureLeaflet(function () { setTimeout(initMap, 100); });
      }).observe(dialog, { attributes: true, attributeFilter: ['open'] });
      // Dialog kann vor der Initialisierung geöffnet worden sein (?neuer_einsatz=1).
      if (dialog.open) ensureLeaflet(function () { setTimeout(initMap, 100); });
    }

    return {
      reset: function () {
        clearTimeout(timer);
        reverseRequest += 1;
        manualPin = false;
        latInput.value = '';
        lngInput.value = '';
        setStatus('');
        if (map && marker) {
          marker.setLatLng([defaultLat, defaultLng]);
          map.setView([defaultLat, defaultLng], 14);
        }
      }
    };
  };
}());

/**
 * map-picker.js — Wiederverwendbarer Leaflet-Karten-Picker
 *
 * Initialisiert eine Leaflet-Karte mit einem verschiebbaren Marker.
 * Setzt den Leaflet-Icon-Pfad auf /static/img/leaflet/.
 *
 * ── OSM-Tile-Server-Regel ───────────────────────────────────────────────────
 * IMMER die Subdomain-Variante verwenden:
 *   URL:       'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
 *   Option:    subdomains: 'abc'
 *
 * NIEMALS die direkte URL ohne {s} verwenden:
 *   ✗  'https://tile.openstreetmap.org/{z}/{x}/{y}.png'   → HTTP 503
 *
 * Begründung: tile.openstreetmap.org ist nur für Tests. Die drei Subdomains
 * a/b/c (a.tile.…, b.tile.…, c.tile.…) verteilen die Last und entsprechen
 * der OSM-Nutzungsrichtlinie für Produktionsanwendungen. Fehlt `subdomains`,
 * sendet Leaflet alle Requests an einen einzigen Host → 503-Fehler.
 * ───────────────────────────────────────────────────────────────────────────
 *
 * Verwendung:
 *   initMapPicker({
 *     containerId: 'mapPickerContainer',
 *     latInputId:  'lat',
 *     lngInputId:  'lng',
 *     defaultLat:  47.4664,
 *     defaultLng:  9.7416,
 *   });
 */

// Leaflet-Marker-Icons auf den lokalen Pfad umlenken
(function patchLeafletIcons() {
  if (typeof L === 'undefined') return;
  delete L.Icon.Default.prototype._getIconUrl;
  L.Icon.Default.mergeOptions({
    iconUrl:       '/static/img/leaflet/marker-icon.png',
    iconRetinaUrl: '/static/img/leaflet/marker-icon-2x.png',
    shadowUrl:     '/static/img/leaflet/marker-shadow.png',
  });
})();

/**
 * @param {object} opts
 * @param {string} opts.containerId  — ID des Karten-Container-div
 * @param {string} opts.latInputId   — ID des Lat-Eingabefelds
 * @param {string} opts.lngInputId   — ID des Lng-Eingabefelds
 * @param {number} [opts.defaultLat] — Fallback-Latitude (Wolfurt)
 * @param {number} [opts.defaultLng] — Fallback-Longitude (Wolfurt)
 * @returns {L.Map|null}
 */
function initMapPicker(opts) {
  if (typeof L === 'undefined') {
    console.warn('map-picker.js: Leaflet ist nicht geladen.');
    return null;
  }

  const container = document.getElementById(opts.containerId);
  const latInput   = document.getElementById(opts.latInputId);
  const lngInput   = document.getElementById(opts.lngInputId);
  if (!container || !latInput || !lngInput) return null;

  const DEFAULT_LAT = opts.defaultLat ?? 47.4664;
  const DEFAULT_LNG = opts.defaultLng ?? 9.7416;

  let initLat = parseFloat(latInput.value) || DEFAULT_LAT;
  let initLng = parseFloat(lngInput.value) || DEFAULT_LNG;

  const map = L.map(container, { zoomControl: true }).setView([initLat, initLng], 15);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende',
    subdomains: 'abc',
    maxZoom: 19,
  }).addTo(map);

  const marker = L.marker([initLat, initLng], { draggable: true }).addTo(map);

  // Marker-Drag → Eingabefelder aktualisieren
  marker.on('dragend', function () {
    const pos = marker.getLatLng();
    latInput.value = pos.lat.toFixed(6);
    lngInput.value = pos.lng.toFixed(6);
  });

  // Klick auf Karte → Marker setzen + Felder aktualisieren
  map.on('click', function (e) {
    marker.setLatLng(e.latlng);
    latInput.value = e.latlng.lat.toFixed(6);
    lngInput.value = e.latlng.lng.toFixed(6);
  });

  // Eingabefelder → Marker bewegen (auf Enter oder blur)
  function syncMarkerFromInputs() {
    const lat = parseFloat(latInput.value);
    const lng = parseFloat(lngInput.value);
    if (!isNaN(lat) && !isNaN(lng)) {
      marker.setLatLng([lat, lng]);
      map.setView([lat, lng], map.getZoom());
    }
  }
  latInput.addEventListener('change', syncMarkerFromInputs);
  lngInput.addEventListener('change', syncMarkerFromInputs);

  // Leaflet braucht invalidateSize nach Anzeige in einem Dialog
  if (container.closest('dialog')) {
    container.closest('dialog').addEventListener('toggle', function () {
      setTimeout(() => map.invalidateSize(), 50);
    });
  }

  // Fallback: mehrfach invalidieren für HTMX-Modals mit async Leaflet-Load
  setTimeout(() => map.invalidateSize(), 100);
  setTimeout(() => map.invalidateSize(), 500);

  return map;
}

/* Einsatzinformation-Lagekarte: Einsatzort-Marker + Objekt-Symbole + Hydranten.
 *
 * Datenquelle:
 *   - Objekt-Symbole: /objekte/{id}/karte/objekte.json (window.objektSymbolHtml aus objekt_karte.js)
 *   - Hydranten/Löschwasser: /einsatz/{id}/hydranten.json (OSM/OSMHydrant + manuelle Objekt-Symbole)
 *
 * Rendert Karte, Marker-Popups und die "Nächste Hydranten"-Liste (#hydranten-liste).
 */
(function () {
  "use strict";

  var el = document.getElementById("einsatz-info-karte");
  if (!el || typeof L === "undefined") { return; }

  var incidentId = el.dataset.incidentId;
  var incLat = parseFloat(el.dataset.lat);
  var incLng = parseFloat(el.dataset.lng);
  var hatEinsatzKoords = !isNaN(incLat) && !isNaN(incLng);
  var objektIds = (el.dataset.objektIds || "").split(",").filter(Boolean);

  var karte = L.map(el, { zoomControl: true });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap"
  }).addTo(karte);

  var bounds = [];
  function fit() {
    if (bounds.length > 1) {
      karte.fitBounds(bounds, { padding: [30, 30], maxZoom: 18 });
    } else if (bounds.length === 1) {
      karte.setView(bounds[0], 17);
    } else {
      karte.setView([47.4652, 9.7503], 14); /* Fallback Wolfurt */
    }
  }

  /* ── Einsatzort-Marker ── */
  if (hatEinsatzKoords) {
    L.marker([incLat, incLng], {
      icon: L.divIcon({
        html: '<div class="einsatz-marker">🚨</div>',
        className: "einsatz-marker-divicon",
        iconSize: null,
        iconAnchor: [16, 32]
      }),
      zIndexOffset: 1000
    }).addTo(karte).bindPopup("<strong>Einsatzort</strong>");
    bounds.push([incLat, incLng]);
  }
  fit();

  /* ── Objekt-Symbole der bestätigten Objekte ── */
  objektIds.forEach(function (oid) {
    fetch("/objekte/" + oid + "/karte/objekte.json")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) { return; }
        (d.eintraege || []).forEach(function (e) {
          var punkt = e.geometry && e.geometry.type === "Point";
          if (e.geometry && !punkt) {
            L.geoJSON(e.geometry, { style: { color: "#d42225", weight: 3, fillOpacity: 0.12 } }).addTo(karte);
            return;
          }
          var lat = punkt ? e.geometry.coordinates[1] : e.lat;
          var lng = punkt ? e.geometry.coordinates[0] : e.lng;
          if (lat == null || lng == null) { return; }
          var html = (typeof window.objektSymbolHtml === "function")
            ? window.objektSymbolHtml(e.typ, e.label)
            : '<div class="oks oks--box">' + (e.label || e.typ) + "</div>";
          L.marker([lat, lng], {
            icon: L.divIcon({ html: html, className: "oks-divicon", iconSize: null, iconAnchor: [16, 16] })
          }).addTo(karte).bindPopup("<strong>" + (e.label || e.typ) + "</strong>");
          bounds.push([lat, lng]);
        });
        fit();
      })
      .catch(function () {});
  });

  /* ── Hydranten / Löschwasser ── */
  var HYDRANT_LABEL = {
    ueberflur: "Überflurhydrant",
    unterflur: "Unterflurhydrant",
    loeschwasser: "Löschwasserstelle",
    hydrant: "Hydrant"
  };
  var HYDRANT_ICON_TEXT = { ueberflur: "H", unterflur: "UH", loeschwasser: "≈" };
  var hydrantById = {};

  function hydrantIcon(typ) {
    var t = HYDRANT_ICON_TEXT[typ] || "H";
    var cls = "hydrant-icon hydrant-icon--" + (typ || "hydrant");
    return L.divIcon({
      html: '<div class="' + cls + '">' + t + "</div>",
      className: "hydrant-divicon",
      iconSize: null,
      iconAnchor: [11, 11]
    });
  }

  function renderHydrantenListe(hydranten, stand) {
    var box = document.getElementById("hydranten-liste");
    if (!box) { return; }
    if (!hydranten || !hydranten.length) {
      box.innerHTML = '<span class="text-muted">Keine Hydranten im Umkreis gefunden.</span>';
      return;
    }
    var html = '<div class="hydrant-liste">';
    hydranten.slice(0, 8).forEach(function (h) {
      var label = HYDRANT_LABEL[h.typ] || "Hydrant";
      var dist = (h.entfernung_m != null) ? (h.entfernung_m + " m" + (h.richtung ? " " + h.richtung : "")) : "";
      var quelle = h.quelle === "objekt" ? " · Objekt" : "";
      html += '<button type="button" class="hydrant-liste__row" data-hid="' + h.id + '">'
        + '<span class="hydrant-icon hydrant-icon--' + (h.typ || "hydrant") + '">'
        + (HYDRANT_ICON_TEXT[h.typ] || "H") + "</span>"
        + '<span class="hydrant-liste__text"><strong>' + label + "</strong>"
        + (h.ref ? ' <span class="text-muted">' + h.ref + "</span>" : "")
        + quelle + "</span>"
        + '<span class="hydrant-liste__dist">' + dist + "</span>"
        + "</button>";
    });
    html += "</div>";
    if (stand) {
      html += '<div class="text-muted" style="font-size:.72rem;margin-top:8px;">Stand: ' + stand + " · Quelle: OpenStreetMap</div>";
    }
    box.innerHTML = html;
    box.querySelectorAll(".hydrant-liste__row").forEach(function (row) {
      row.addEventListener("click", function () {
        var m = hydrantById[row.dataset.hid];
        if (m) { karte.setView(m.getLatLng(), 18); m.openPopup(); }
      });
    });
  }

  fetch("/einsatz/" + incidentId + "/hydranten.json")
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d) {
        var card = document.getElementById("hydranten-card");
        if (card) { card.style.display = "none"; }
        return;
      }
      (d.hydranten || []).forEach(function (h) {
        if (h.lat == null || h.lng == null) { return; }
        // Manuelle Objekt-Hydranten sind bereits als Objekt-Symbole auf der Karte —
        // nur in der Liste zeigen, nicht doppelt als Marker zeichnen.
        if (h.quelle === "objekt") { return; }
        var m = L.marker([h.lat, h.lng], { icon: hydrantIcon(h.typ) }).addTo(karte);
        var label = HYDRANT_LABEL[h.typ] || "Hydrant";
        m.bindPopup("<strong>" + label + "</strong>"
          + (h.ref ? "<br>" + h.ref : "")
          + (h.entfernung_m != null ? "<br>" + h.entfernung_m + " m" : ""));
        hydrantById[h.id] = m;
        bounds.push([h.lat, h.lng]);
      });
      renderHydrantenListe(d.hydranten, d.stand);
      fit();
    })
    .catch(function () {
      var box = document.getElementById("hydranten-liste");
      if (box) { box.innerHTML = '<span class="text-muted">Hydranten konnten nicht geladen werden.</span>'; }
    });
})();

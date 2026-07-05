/* Einsatzinformation-Lagekarte: Einsatzort-Marker + Objekt-Symbole + Hydranten.
 *
 * Datenquelle:
 *   - Objekt-Symbole: /objekte/{id}/karte/objekte.json (window.objektSymbolHtml aus objekt_karte.js)
 *   - Hydranten/Löschwasser: /einsatz/{id}/hydranten.json (OSM/OSMHydrant + manuelle Objekt-Symbole)
 *
 * Zoom-Logik: Die Karte zoomt auf Einsatzort + gematchte Objekte ("objektBounds"),
 * damit das Objekt erkennbar ist. Hydranten (bis 2 km entfernt) erweitern den
 * Ausschnitt NICHT, sonst zoomt die Karte zu weit heraus.
 *
 * Liste: zeigt max. 5 Entnahmestellen gleichzeitig, die restlichen erscheinen beim
 * Scrollen im inneren Scrollbereich.
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
  // Karte liegt in einem Grid/Sticky-Container → nach dem Layout neu vermessen.
  setTimeout(function () { karte.invalidateSize(); }, 200);
  window.addEventListener("resize", function () { karte.invalidateSize(); });

  // objektBounds treibt den Zoom (Einsatzort + Objekte). hydrantBounds nur
  // als sanfter Fallback, wenn es sonst nichts zum Zentrieren gibt.
  var objektBounds = [];
  var hydrantBounds = [];
  var zentrumFallback = null;

  function fit() {
    if (objektBounds.length > 1) {
      karte.fitBounds(objektBounds, { padding: [45, 45], maxZoom: 18 });
    } else if (objektBounds.length === 1) {
      karte.setView(objektBounds[0], 18);
    } else if (zentrumFallback) {
      karte.setView(zentrumFallback, 17);
    } else if (hydrantBounds.length) {
      karte.fitBounds(hydrantBounds, { padding: [45, 45], maxZoom: 17 });
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
    objektBounds.push([incLat, incLng]);
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
          objektBounds.push([lat, lng]);
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

  // Liste zeigt max. 5 Einträge gleichzeitig (CSS-Höhendeckel); die restlichen
  // Entnahmestellen erscheinen beim Scrollen im inneren Scrollbereich. Die Marker
  // liegen ohnehin alle auf der Karte.
  var LISTE_SICHTBAR = 5;

  function hydrantRowHtml(h) {
    var label = HYDRANT_LABEL[h.typ] || "Hydrant";
    var dist = (h.entfernung_m != null) ? (h.entfernung_m + " m" + (h.richtung ? " " + h.richtung : "")) : "";
    var quelle = h.quelle === "objekt" ? " · Objekt" : "";
    return '<button type="button" class="hydrant-liste__row" data-hid="' + h.id + '">'
      + '<span class="hydrant-icon hydrant-icon--' + (h.typ || "hydrant") + '">'
      + (HYDRANT_ICON_TEXT[h.typ] || "H") + "</span>"
      + '<span class="hydrant-liste__text"><strong>' + label + "</strong>"
      + (h.ref ? ' <span class="text-muted">' + h.ref + "</span>" : "")
      + quelle + "</span>"
      + '<span class="hydrant-liste__dist">' + dist + "</span>"
      + "</button>";
  }

  function bindHydrantRow(row) {
    row.addEventListener("click", function () {
      var m = hydrantById[row.dataset.hid];
      if (m) { karte.setView(m.getLatLng(), 18); m.openPopup(); }
    });
  }

  function renderHydrantenListe(hydranten, stand, aktiv) {
    var box = document.getElementById("hydranten-liste");
    if (!box) { return; }
    if (!hydranten || !hydranten.length) {
      box.innerHTML = aktiv === false
        ? '<span class="text-muted">Hydranten-Layer für diese Organisation deaktiviert.</span>'
        : '<span class="text-muted">Keine Hydranten im Umkreis gefunden.</span>';
      return;
    }
    var html = "";
    hydranten.forEach(function (h) { html += hydrantRowHtml(h); });
    var mehr = hydranten.length > LISTE_SICHTBAR
      ? '<div class="hydrant-liste__mehr text-muted">' + hydranten.length
        + " Entnahmestellen · scrollen für mehr</div>"
      : "";
    box.innerHTML = '<div class="hydrant-liste" id="hydrant-liste-scroll">' + html + "</div>"
      + mehr
      + (stand ? '<div class="text-muted" style="font-size:.72rem;margin-top:8px;">Stand: ' + stand + " · Quelle: OpenStreetMap</div>" : "");
    document.querySelectorAll("#hydrant-liste-scroll .hydrant-liste__row").forEach(bindHydrantRow);
  }

  fetch("/einsatz/" + incidentId + "/hydranten.json")
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d) {
        renderHydrantenListe([], null, true);
        return;
      }
      // Zentrum-Fallback: hat der Einsatz keine Koordinaten, aber der Server
      // ein verknüpftes Objekt als Bezug geliefert → Karte darauf zentrieren.
      if (!objektBounds.length && d.zentrum && d.zentrum.lat != null) {
        zentrumFallback = [d.zentrum.lat, d.zentrum.lng];
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
        hydrantBounds.push([h.lat, h.lng]);
      });
      renderHydrantenListe(d.hydranten, d.stand, d.aktiv);
      fit();
    })
    .catch(function () {
      var box = document.getElementById("hydranten-liste");
      if (box) { box.innerHTML = '<span class="text-muted">Hydranten konnten nicht geladen werden.</span>'; }
    });
})();

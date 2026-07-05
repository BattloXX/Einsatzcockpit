/* Objekt-Lagekarte: Leaflet + Geoman-Editor mit Symbolpalette.
 *
 * Symbol-Rendering analog taktSymbolHtml der GSL-Karte (incident_major/karte.html):
 * rot umrandete Text-Boxen (FSD/BMZ/FBF/...), gelbe Gefahren-Dreiecke, Pfeile
 * fuer Zugaenge, Hydranten-Symbole. Kein Server-Symbolkatalog.
 *
 * initObjektKarte({ elementId, objektId, csrfToken, editierbar })
 */
(function () {
  "use strict";

  var SYMBOLE = {
    fsd:               { text: "FSD",  stil: "box" },
    schluesselbox:     { text: "BOX",  stil: "box" },
    bsp:               { text: "BSP",  stil: "box" },
    bmz:               { text: "BMZ",  stil: "box" },
    fbf:               { text: "FBF",  stil: "box" },
    dlk_stellplatz:    { text: "DLK",  stil: "box" },
    objektfunk:        { text: "FUNK", stil: "box" },
    sammelplatz:       { text: "SP",   stil: "gruen" },
    feuerloescher:     { text: "FL",   stil: "rot" },
    hauptzugang:       { text: "➜",    stil: "pfeil-voll" },
    nebenzugang:       { text: "➜",    stil: "pfeil-leer" },
    stiege:            { text: "ST",   stil: "gruen" },
    aufzug:            { text: "AZ",   stil: "box" },
    gefahr_ex:         { text: "EX",   stil: "dreieck" },
    gefahr_gas:        { text: "GAS",  stil: "dreieck" },
    gefahr_chemie:     { text: "CHE",  stil: "dreieck" },
    gefahr_strom:      { text: "kV",   stil: "dreieck" },
    gefahr_pv:         { text: "PV",   stil: "dreieck" },
    hydrant_ueberflur: { text: "H",    stil: "hydrant" },
    hydrant_unterflur: { text: "UH",   stil: "hydrant" }
  };

  function objektSymbolHtml(typ, label) {
    // Unbekannte Typen (z. B. aus EUS-Import): Typ-Kuerzel statt "?" anzeigen
    var s = SYMBOLE[typ] || { text: String(typ || "?").substring(0, 4).toUpperCase(), stil: "box" };
    var inner;
    if (s.stil === "dreieck") {
      inner = '<div class="oks oks--dreieck"><span>' + s.text + "</span></div>";
    } else if (s.stil === "hydrant") {
      inner = '<div class="oks oks--hydrant">' + s.text + "</div>";
    } else if (s.stil === "pfeil-voll") {
      inner = '<div class="oks oks--pfeil oks--pfeil-voll">' + s.text + "</div>";
    } else if (s.stil === "pfeil-leer") {
      inner = '<div class="oks oks--pfeil oks--pfeil-leer">' + s.text + "</div>";
    } else if (s.stil === "gruen") {
      inner = '<div class="oks oks--box oks--gruen">' + s.text + "</div>";
    } else if (s.stil === "rot") {
      inner = '<div class="oks oks--box oks--rot">' + s.text + "</div>";
    } else {
      inner = '<div class="oks oks--box">' + s.text + "</div>";
    }
    if (label) {
      inner += '<div class="oks__label">' + label.replace(/</g, "&lt;") + "</div>";
    }
    return '<div class="oks-wrap">' + inner + "</div>";
  }

  // Auch global verfuegbar (Alarm-Infoscreen rendert Symbole ohne initObjektKarte)
  window.objektSymbolHtml = objektSymbolHtml;

  function symbolIcon(typ, label) {
    return L.divIcon({
      html: objektSymbolHtml(typ, label),
      className: "oks-divicon",
      iconSize: null,
      iconAnchor: [16, 16]
    });
  }

  window.initObjektKarte = function (opts) {
    var karte = L.map(opts.elementId, { zoomControl: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap"
    }).addTo(karte);

    var layerById = {};
    var aktiverTyp = null;

    function apiUrl(pfad) {
      return "/objekte/" + opts.objektId + "/karte" + pfad;
    }

    function apiPost(pfad, daten) {
      return fetch(apiUrl(pfad), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": opts.csrfToken
        },
        body: JSON.stringify(daten)
      }).then(function (r) {
        if (!r.ok) { throw new Error("HTTP " + r.status); }
        return r.json();
      });
    }

    function eintragEntfernen(id) {
      apiPost("/objekte/" + id + "/loeschen", {}).then(function () {
        if (layerById[id]) { karte.removeLayer(layerById[id]); delete layerById[id]; }
      }).catch(function () { alert("Löschen fehlgeschlagen"); });
    }

    function popupHtml(e) {
      var html = "<strong>" + (e.label || e.typ) + "</strong>";
      if (opts.editierbar) {
        html += '<br><button type="button" class="btn btn--danger btn--xs" ' +
          'onclick="window._oksDelete(' + e.id + ')">Entfernen</button>';
      }
      return html;
    }
    window._oksDelete = eintragEntfernen;

    function eintragAnzeigen(e) {
      var layer;
      var punktGeometrie = e.geometry && e.geometry.type === "Point";
      if (e.geometry && !punktGeometrie) {
        layer = L.geoJSON(e.geometry, {
          style: { color: "#d42225", weight: 3, fillOpacity: 0.12 }
        });
        if (opts.editierbar) {
          layer.on("pm:edit", function (ev) {
            var gj = ev.layer.toGeoJSON();
            apiPost("/objekte/" + e.id, { geometry: gj.geometry }).catch(function () {});
          });
        }
      } else {
        // Punkte immer als Symbol-Marker rendern — auch wenn sie (z. B. aus dem
        // EUS-Import) als GeoJSON-Point statt lat/lng gespeichert sind. L.geoJSON
        // wuerde sonst Leaflets Default-Icon nutzen, dessen marker-icon.png es
        // unter /static nicht gibt (kaputtes-Bild-Symbol).
        var lat = punktGeometrie ? e.geometry.coordinates[1] : e.lat;
        var lng = punktGeometrie ? e.geometry.coordinates[0] : e.lng;
        layer = L.marker([lat, lng], {
          icon: symbolIcon(e.typ, e.label),
          draggable: !!opts.editierbar
        });
        if (opts.editierbar) {
          layer.on("dragend", function (ev) {
            var pos = ev.target.getLatLng();
            var daten = punktGeometrie
              ? { geometry: { type: "Point", coordinates: [pos.lng, pos.lat] } }
              : { lat: pos.lat, lng: pos.lng };
            apiPost("/objekte/" + e.id, daten).catch(function () {});
          });
        }
      }
      layer.bindPopup(popupHtml(e));
      layer.addTo(karte);
      layerById[e.id] = layer;
    }

    fetch(apiUrl("/objekte.json"))
      .then(function (r) { return r.json(); })
      .then(function (daten) {
        var o = daten.objekt;
        if (o.lat != null && o.lng != null) {
          karte.setView([o.lat, o.lng], 18);
        } else {
          karte.setView([47.4652, 9.7503], 15); /* Fallback Wolfurt */
        }
        daten.eintraege.forEach(eintragAnzeigen);
        var punkte = daten.eintraege.filter(function (e) { return e.lat != null; });
        if (o.lat == null && punkte.length) {
          karte.setView([punkte[0].lat, punkte[0].lng], 18);
        }
      });

    if (!opts.editierbar) { return karte; }

    /* ── Editor: Palette (Klick waehlt Symbol, Kartenklick platziert) ── */
    var palette = document.getElementById("oks-palette");
    if (palette) {
      palette.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-typ]");
        if (!btn) { return; }
        var vorher = palette.querySelector(".oks-palette__eintrag--aktiv");
        if (vorher) { vorher.classList.remove("oks-palette__eintrag--aktiv"); }
        if (aktiverTyp === btn.dataset.typ) {
          aktiverTyp = null;
          return;
        }
        aktiverTyp = btn.dataset.typ;
        btn.classList.add("oks-palette__eintrag--aktiv");
      });
    }

    karte.on("click", function (ev) {
      if (!aktiverTyp) { return; }
      var typ = aktiverTyp;
      var label = "";
      var labelFeld = document.getElementById("oks-label");
      if (labelFeld) { label = labelFeld.value.trim(); }
      apiPost("/objekte", { typ: typ, lat: ev.latlng.lat, lng: ev.latlng.lng, label: label })
        .then(function (e) {
          eintragAnzeigen(e);
          if (labelFeld) { labelFeld.value = ""; }
        })
        .catch(function () { alert("Speichern fehlgeschlagen"); });
    });

    /* ── Geoman: Linien/Flaechen ── */
    if (karte.pm) {
      karte.pm.addControls({
        position: "topleft",
        drawMarker: false,
        drawCircleMarker: false,
        drawCircle: false,
        drawText: false,
        drawPolyline: true,
        drawRectangle: true,
        drawPolygon: true,
        editMode: true,
        dragMode: false,
        cutPolygon: false,
        removalMode: false,
        rotateMode: false
      });
      karte.on("pm:create", function (ev) {
        var gj = ev.layer.toGeoJSON();
        var label = "";
        var labelFeld = document.getElementById("oks-label");
        if (labelFeld) { label = labelFeld.value.trim(); }
        apiPost("/objekte", { typ: "geometrie", geometry: gj.geometry, label: label })
          .then(function (e) {
            karte.removeLayer(ev.layer);
            eintragAnzeigen(e);
            if (labelFeld) { labelFeld.value = ""; }
          })
          .catch(function () {
            karte.removeLayer(ev.layer);
            alert("Speichern fehlgeschlagen");
          });
      });
    }

    return karte;
  };
})();
